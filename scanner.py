#!/usr/bin/env python3
"""Weekend Getaway Flight Scanner.

Scans Ryanair's public fare-finder API for cheap round-trip weekend
flights (Fri evening out, Sun evening back) from Shannon (preferred)
and Dublin into Europe, capped at EUR 100. Dublin fares are adjusted
upward by the cost of a return Limerick<->Dublin bus so the two
origins can be compared on an "effective price from Limerick" basis.

Ryanair's fare-finder endpoint is public, unauthenticated, and used
by ryanair.com's own "fare finder" page. No API key. No signup. It
covers Ryanair only -- which for SNN and short-haul DUB is roughly
the entire relevant market anyway.

Booking links in the output still point at Google Flights and
Skyscanner because those are the URL schemes that hold up across
reloads; we just use Ryanair as the data source, not the booking
target.

Setup:
    pip install -r requirements.txt
    python scanner.py

Set SCANNER_PROSPECTS_ONLY=1 in the environment to force the
route-catalogue fallback (useful for offline/sandbox testing, or if
Ryanair's endpoint is ever unreachable).

Writes the results to dashboard/deals.json for the front-end to render.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import quote_plus

import requests

# curl_cffi gives us libcurl-impersonate under the hood: real Chrome
# TLS fingerprint, cipher order, ALPN, HTTP/2 settings. Required for
# Cloudflare-fronted endpoints (Ryanair is one) that fingerprint the
# TLS handshake, not just the User-Agent. If it isn't installed we
# fall back to plain `requests` and log a warning -- useful for local
# offline dev but expected to 403 against the real Ryanair endpoint.
try:
    from curl_cffi import requests as _curl_requests  # type: ignore
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _curl_requests = None  # type: ignore
    _CURL_CFFI_AVAILABLE = False

# When using curl_cffi, which Chrome build to impersonate. chrome120
# is present in every 0.7.x release of curl_cffi, which is the floor
# we pin in requirements.txt. chrome124/131 are newer but not in every
# 0.7.x -- picking a safer value avoids a ValueError at runtime if pip
# resolves to an older patch release. Keep the numeric version aligned
# with the RYANAIR_HEADERS Sec-Ch-Ua fields so the handshake and the
# client hints don't contradict each other.
CURL_IMPERSONATE = "chrome120"


def _http_get(url: str, **kwargs):
    """Single entry point for outbound HTTP GETs to Ryanair.

    Routes through curl_cffi when available so the TLS handshake
    matches Chrome's; falls back to plain requests otherwise. Kept as
    a standalone helper so tests can monkey-patch exactly one symbol.

    If curl_cffi is present but the requested impersonate profile is
    unknown to the installed version (ValueError), we retry with plain
    requests instead of crashing the whole scan.
    """
    global _CURL_CFFI_AVAILABLE  # noqa: PLW0603
    if _CURL_CFFI_AVAILABLE:
        try:
            return _curl_requests.get(
                url, impersonate=CURL_IMPERSONATE, **kwargs
            )
        except ValueError as e:
            # Most likely an unsupported impersonate= value for this
            # version of curl_cffi. Warn once and fall through to
            # plain requests for the rest of the run.
            print(
                f"  [warn] curl_cffi rejected impersonate={CURL_IMPERSONATE!r}: "
                f"{e}. Falling back to plain requests.",
                file=sys.stderr,
            )
            _CURL_CFFI_AVAILABLE = False
    return requests.get(url, **kwargs)


# ---------- Config ----------
PRICE_CAP_EUR = 100.0
# Approx Limerick <-> Dublin return via Bus Eireann / Citylink / Dublin Coach.
BUS_RETURN_COST_EUR = 30.0
ORIGINS = ["SNN", "DUB"]  # Shannon first, Dublin as fallback.
WEEKENDS_AHEAD = 26       # Scan ~6 months of upcoming weekends (live mode).

# Friday evening departures and Sunday afternoon/evening returns.
OUTBOUND_FROM = "16:00"
OUTBOUND_TO = "23:59"
INBOUND_FROM = "15:00"
INBOUND_TO = "23:59"

# Ryanair's public fare-finder endpoint. Returns round-trip fares
# matching date/time/price filters, no authentication required.
RYANAIR_URL = "https://services-api.ryanair.com/farfnd/v4/roundTripFares"

# Browser-ish headers paired with the curl_cffi Chrome 120 impersonation.
# Keep the version number in sync with CURL_IMPERSONATE above -- the TLS
# handshake and the client hints must agree or Cloudflare flags the
# mismatch.
RYANAIR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IE,en-GB;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://www.ryanair.com",
    "Referer": "https://www.ryanair.com/ie/en/cheap-flights",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Sec-Ch-Ua": '"Google Chrome";v="120", "Chromium";v="120", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

OUTPUT_PATH = Path(__file__).parent / "dashboard" / "deals.json"

# Force prospects-mode fallback even when Ryanair would be reachable.
# Useful for offline/sandbox runs.
FORCE_PROSPECTS = bool(os.environ.get("SCANNER_PROSPECTS_ONLY", "").strip())

# How many upcoming weekends to generate prospect links for when we
# fall back to the route catalogue. Kept smaller than the live-mode
# window because prospects mode has no price filter and the card
# count is `routes x weekends`, which balloons fast.
PROSPECTS_WEEKENDS = 8

# Verified direct routes to Europe from Shannon and Dublin (Ryanair /
# Aer Lingus as of recent schedules). Used ONLY for the prospects-mode
# fallback (when Ryanair's live endpoint is unreachable); every (origin,
# dest) entry becomes a click-through card to Google Flights / Skyscanner.
# Prices are intentionally NOT included; follow the links to check live.
EUROPE_ROUTES: dict[str, list[tuple[str, str, str, float, float]]] = {
    "SNN": [
        ("STN", "London Stansted",   "United Kingdom", 51.8860,  0.2389),
        ("LHR", "London Heathrow",   "United Kingdom", 51.4700, -0.4543),
        ("MAN", "Manchester",        "United Kingdom", 53.3537, -2.2750),
        ("EDI", "Edinburgh",         "United Kingdom", 55.9500, -3.3725),
        ("LPL", "Liverpool",         "United Kingdom", 53.3336, -2.8497),
        ("ALC", "Alicante",          "Spain",          38.2822, -0.5582),
        ("AGP", "Malaga",            "Spain",          36.6749, -4.4991),
        ("FAO", "Faro",              "Portugal",       37.0144, -7.9659),
        ("ACE", "Lanzarote",         "Spain",          28.9455, -13.6052),
    ],
    "DUB": [
        # UK
        ("STN", "London Stansted",   "United Kingdom", 51.8860,  0.2389),
        ("LGW", "London Gatwick",    "United Kingdom", 51.1537, -0.1821),
        ("LHR", "London Heathrow",   "United Kingdom", 51.4700, -0.4543),
        ("LTN", "London Luton",      "United Kingdom", 51.8747, -0.3683),
        ("MAN", "Manchester",        "United Kingdom", 53.3537, -2.2750),
        ("EDI", "Edinburgh",         "United Kingdom", 55.9500, -3.3725),
        ("GLA", "Glasgow",           "United Kingdom", 55.8719, -4.4331),
        ("BHX", "Birmingham",        "United Kingdom", 52.4539, -1.7480),
        ("BRS", "Bristol",           "United Kingdom", 51.3827, -2.7191),
        # Benelux / France / Germany
        ("AMS", "Amsterdam",         "Netherlands",    52.3086,  4.7639),
        ("BRU", "Brussels",          "Belgium",        50.9014,  4.4844),
        ("CRL", "Brussels Charleroi","Belgium",        50.4592,  4.4538),
        ("CDG", "Paris CDG",         "France",         49.0097,  2.5479),
        ("BVA", "Paris Beauvais",    "France",         49.4544,  2.1128),
        ("FRA", "Frankfurt",         "Germany",        50.0379,  8.5622),
        ("BER", "Berlin",            "Germany",        52.3667, 13.5033),
        ("MUC", "Munich",            "Germany",        48.3538, 11.7861),
        ("HAM", "Hamburg",           "Germany",        53.6304,  9.9882),
        # Iberia
        ("BCN", "Barcelona",         "Spain",          41.2974,  2.0833),
        ("GRO", "Girona",            "Spain",          41.9010,  2.7605),
        ("MAD", "Madrid",            "Spain",          40.4936, -3.5668),
        ("ALC", "Alicante",          "Spain",          38.2822, -0.5582),
        ("AGP", "Malaga",            "Spain",          36.6749, -4.4991),
        ("LIS", "Lisbon",            "Portugal",       38.7742, -9.1342),
        ("OPO", "Porto",             "Portugal",       41.2481, -8.6814),
        ("FAO", "Faro",              "Portugal",       37.0144, -7.9659),
        # Italy
        ("FCO", "Rome Fiumicino",    "Italy",          41.8003, 12.2389),
        ("CIA", "Rome Ciampino",     "Italy",          41.7994, 12.5949),
        ("MXP", "Milan Malpensa",    "Italy",          45.6306,  8.7281),
        ("BGY", "Milan Bergamo",     "Italy",          45.6739,  9.7042),
        ("NAP", "Naples",            "Italy",          40.8860, 14.2908),
        ("BLQ", "Bologna",           "Italy",          44.5354, 11.2887),
        ("VCE", "Venice",            "Italy",          45.5053, 12.3519),
        ("PSA", "Pisa",              "Italy",          43.6839, 10.3927),
        # Central / Eastern Europe
        ("PRG", "Prague",            "Czechia",        50.1008, 14.2600),
        ("VIE", "Vienna",            "Austria",        48.1103, 16.5697),
        ("BUD", "Budapest",          "Hungary",        47.4369, 19.2556),
        ("KRK", "Krakow",            "Poland",         50.0777, 19.7848),
        ("WAW", "Warsaw",            "Poland",         52.1657, 20.9671),
        ("WRO", "Wroclaw",           "Poland",         51.1027, 16.8858),
        ("GDN", "Gdansk",            "Poland",         54.3776, 18.4662),
        # Nordics / Switzerland / Mediterranean
        ("CPH", "Copenhagen",        "Denmark",        55.6180, 12.6561),
        ("ARN", "Stockholm Arlanda", "Sweden",         59.6519, 17.9186),
        ("ZRH", "Zurich",            "Switzerland",    47.4647,  8.5492),
        ("GVA", "Geneva",            "Switzerland",    46.2381,  6.1089),
        ("MLA", "Malta",             "Malta",          35.8575, 14.4775),
    ],
}


# ---------- Date helpers ----------
def next_weekends(n: int) -> Iterator[tuple[dt.date, dt.date]]:
    """Yield (friday, sunday) pairs for the next `n` upcoming weekends.

    Always starts from the *next* Friday, never the current day, because a
    Friday-evening departure booked on the same Friday morning isn't a
    realistic weekend getaway opportunity.
    """
    today = dt.date.today()
    days_to_friday = (4 - today.weekday()) % 7  # 4 == Friday
    if days_to_friday == 0:
        days_to_friday = 7  # skip today, always look forward
    first_friday = today + dt.timedelta(days=days_to_friday)
    for i in range(n):
        friday = first_friday + dt.timedelta(weeks=i)
        sunday = friday + dt.timedelta(days=2)
        yield friday, sunday


# ---------- Deep-link builders ----------
def google_flights_url(origin: str, dest: str, out_date: str, in_date: str) -> str:
    """Stable Google Flights search URL using the natural-language `q` param."""
    q = f"Flights from {origin} to {dest} on {out_date} through {in_date}"
    return "https://www.google.com/travel/flights?q=" + quote_plus(q)


def skyscanner_url(origin: str, dest: str, out_date: str, in_date: str) -> str:
    """Skyscanner direct search URL (yyMMdd date format, lowercase IATA)."""
    out_yy = dt.date.fromisoformat(out_date).strftime("%y%m%d")
    in_yy = dt.date.fromisoformat(in_date).strftime("%y%m%d")
    return (
        f"https://www.skyscanner.net/transport/flights/"
        f"{origin.lower()}/{dest.lower()}/{out_yy}/{in_yy}/"
    )


# ---------- Ryanair fetch ----------
def fetch_fares(origin: str, friday: dt.date, sunday: dt.date) -> dict:
    """Hit Ryanair's public round-trip fare-finder for a single weekend."""
    params = {
        "departureAirportIataCode": origin,
        "outboundDepartureDateFrom": friday.isoformat(),
        "outboundDepartureDateTo": friday.isoformat(),
        "outboundDepartureTimeFrom": OUTBOUND_FROM,
        "outboundDepartureTimeTo": OUTBOUND_TO,
        "inboundDepartureDateFrom": sunday.isoformat(),
        "inboundDepartureDateTo": sunday.isoformat(),
        "inboundDepartureTimeFrom": INBOUND_FROM,
        "inboundDepartureTimeTo": INBOUND_TO,
        # Ask for slightly more than our cap so Dublin fares that still
        # fit after adding the bus surcharge come through.
        "priceValueTo": str(int(PRICE_CAP_EUR + BUS_RETURN_COST_EUR)),
        "currency": "EUR",
        "market": "en-ie",
        "adultPaxCount": "1",
        "limit": "200",
        "offset": "0",
    }
    resp = _http_get(
        RYANAIR_URL, params=params, headers=RYANAIR_HEADERS, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


# ---------- Normalisation ----------
def _city_name(airport: dict) -> str:
    city = airport.get("city")
    if isinstance(city, dict):
        return city.get("name") or airport.get("name", "")
    return airport.get("cityName") or airport.get("name", "")


def normalise_fare(fare: dict, origin: str) -> dict | None:
    """Turn a Ryanair `fares[*]` entry into our flat deal schema."""
    try:
        outbound = fare["outbound"]
        inbound = fare["inbound"]
        arr = outbound["arrivalAirport"]
        summary = fare["summary"]["price"]
    except (KeyError, TypeError):
        return None

    flight_price = float(summary.get("value", 0))
    bus = 0.0 if origin == "SNN" else BUS_RETURN_COST_EUR
    effective = flight_price + bus

    coords = arr.get("coordinates") or {}
    try:
        lat = float(coords["latitude"])
        lon = float(coords["longitude"])
    except (KeyError, TypeError, ValueError):
        return None

    out_dep = outbound.get("departureDate", "")
    out_arr = outbound.get("arrivalDate", "")
    in_dep = inbound.get("departureDate", "")
    in_arr = inbound.get("arrivalDate", "")
    if not out_dep or not in_dep:
        return None

    # Belt-and-braces: reject anything outside the Fri-evening /
    # Sun-afternoon-evening windows even if Ryanair's filter let it
    # through. A 09:20 "Friday" flight is not a weekend getaway no
    # matter what the API says.
    out_hhmm = out_dep[11:16]  # "YYYY-MM-DDTHH:MM:SS" -> "HH:MM"
    in_hhmm = in_dep[11:16]
    if not (OUTBOUND_FROM <= out_hhmm <= OUTBOUND_TO):
        return None
    if not (INBOUND_FROM <= in_hhmm <= INBOUND_TO):
        return None

    dest_iata = arr.get("iataCode", "")
    if not dest_iata:
        return None
    out_date = out_dep[:10]
    in_date = in_dep[:10]

    return {
        "origin": origin,
        "destination_iata": dest_iata,
        "destination_city": _city_name(arr),
        "destination_country": arr.get("countryName", ""),
        "destination_lat": lat,
        "destination_lon": lon,
        "flight_price_eur": round(flight_price, 2),
        "bus_surcharge_eur": round(bus, 2),
        "effective_price_eur": round(effective, 2),
        "currency": summary.get("currencyCode", "EUR"),
        "outbound_departure": out_dep,
        "outbound_arrival": out_arr,
        "outbound_flight_number": outbound.get("flightNumber", ""),
        "inbound_departure": in_dep,
        "inbound_arrival": in_arr,
        "inbound_flight_number": inbound.get("flightNumber", ""),
        "google_flights_url": google_flights_url(origin, dest_iata, out_date, in_date),
        "skyscanner_url": skyscanner_url(origin, dest_iata, out_date, in_date),
    }


# ---------- Prospects mode (no API key) ----------
PROSPECTS_TIME_NOTE = (
    "Link opens ALL flights for these dates -- Google Flights / "
    "Skyscanner URL schemes can't encode a time-of-day filter. "
    "Filter for departures after 16:00 (Fri) and 15:00 (Sun) yourself."
)


def build_prospects(weekends: list[tuple[dt.date, dt.date]]) -> list[dict]:
    """Every known route x every upcoming weekend, with NO price data.

    Used as a fallback when Ryanair's live endpoint is unreachable (or
    when SCANNER_PROSPECTS_ONLY is set for testing). We can't claim to
    know fares in this mode, so we emit click-through cards that open
    Google Flights / Skyscanner for the user to check live prices.

    IMPORTANT: prospects mode CANNOT enforce the Fri-evening / Sun-evening
    time window -- neither Google Flights' `?q=` scheme nor Skyscanner's
    URL scheme accepts a departure-time filter. Each entry carries a
    `time_window_note` so the dashboard can warn the user; the actual
    filtering has to happen on the destination site.
    """
    entries: list[dict] = []
    for origin, routes in EUROPE_ROUTES.items():
        bus = 0.0 if origin == "SNN" else BUS_RETURN_COST_EUR
        for iata, city, country, lat, lon in routes:
            for friday, sunday in weekends:
                entries.append({
                    "origin": origin,
                    "destination_iata": iata,
                    "destination_city": city,
                    "destination_country": country,
                    "destination_lat": lat,
                    "destination_lon": lon,
                    "flight_price_eur": None,
                    "bus_surcharge_eur": round(bus, 2),
                    "effective_price_eur": None,
                    "currency": "EUR",
                    "outbound_departure": f"{friday.isoformat()}T18:00:00",
                    "outbound_arrival": "",
                    "outbound_flight_number": "",
                    "inbound_departure": f"{sunday.isoformat()}T19:00:00",
                    "inbound_arrival": "",
                    "inbound_flight_number": "",
                    "time_window_note": PROSPECTS_TIME_NOTE,
                    "google_flights_url": google_flights_url(
                        origin, iata, friday.isoformat(), sunday.isoformat()
                    ),
                    "skyscanner_url": skyscanner_url(
                        origin, iata, friday.isoformat(), sunday.isoformat()
                    ),
                })
    return entries


def write_prospects_mode(reason: str = "") -> int:
    weekends = list(next_weekends(PROSPECTS_WEEKENDS))
    entries = build_prospects(weekends)
    # Sort: soonest weekend first, Shannon ahead of Dublin, then country/city.
    entries.sort(key=lambda d: (
        d["outbound_departure"][:10],
        0 if d["origin"] == "SNN" else 1,
        d["destination_country"],
        d["destination_city"],
    ))

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "price_cap_eur": PRICE_CAP_EUR,
        "bus_return_cost_eur": BUS_RETURN_COST_EUR,
        "origins": list(EUROPE_ROUTES.keys()),
        "weekends_scanned": len(weekends),
        "mode": "prospects",
        "source": "route-catalogue",
        "deals": entries,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    prefix = f"{reason} -- " if reason else ""
    print(
        f"{prefix}writing {len(entries)} route prospects "
        f"({sum(len(v) for v in EUROPE_ROUTES.values())} routes x "
        f"{len(weekends)} weekends) to {OUTPUT_PATH}."
    )
    return 0


# ---------- Test notification (no scan, no network to Ryanair) ----------
def send_test_notification() -> int:
    """Fire a single fake-deal Discord/ntfy message and exit.

    Decoupled from the scan pipeline on purpose: lets you prove the
    webhook wiring is correct without waiting for the cron, without
    worrying whether Ryanair is reachable, and without needing two
    scans to get past the first-run baseline guard.
    """
    discord = os.environ.get("NOTIFY_DISCORD_WEBHOOK_URL", "").strip()
    ntfy = os.environ.get("NOTIFY_NTFY_URL", "").strip()
    if not (discord or ntfy):
        print(
            "ERROR: NOTIFY_DISCORD_WEBHOOK_URL is not set in the environment.\n"
            "Set it and re-run:\n"
            "    export NOTIFY_DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'\n"
            "    python scanner.py --test-notification",
            file=sys.stderr,
        )
        return 1

    fake_deal = {
        "origin": "SNN",
        "destination_iata": "FAO",
        "destination_city": "[TEST] Faro",
        "destination_country": "Portugal",
        "destination_lat": 37.01,
        "destination_lon": -7.97,
        "flight_price_eur": 39.99,
        "bus_surcharge_eur": 0.0,
        "effective_price_eur": 39.99,
        "currency": "EUR",
        "outbound_departure": "2026-05-08T19:25:00",
        "outbound_arrival": "2026-05-08T22:00:00",
        "outbound_flight_number": "FR-TEST",
        "inbound_departure": "2026-05-10T20:10:00",
        "inbound_arrival": "2026-05-10T22:30:00",
        "inbound_flight_number": "FR-TEST",
        "google_flights_url": (
            "https://www.google.com/travel/flights?q="
            "Flights+from+SNN+to+FAO+on+2026-05-08+through+2026-05-10"
        ),
        "skyscanner_url": (
            "https://www.skyscanner.net/transport/flights/snn/fao/260508/260510/"
        ),
    }

    print(f"Sending test notification (discord={bool(discord)}, ntfy={bool(ntfy)})...")
    from notifier import _notify_discord, _notify_ntfy, DEFAULT_ALERT_CAP_EUR
    if discord:
        _notify_discord(discord, [fake_deal], DEFAULT_ALERT_CAP_EUR)
    if ntfy:
        _notify_ntfy(ntfy, [fake_deal], DEFAULT_ALERT_CAP_EUR)
    print("Done. Check your Discord channel -- the message title should start with '[TEST]'.")
    return 0


# ---------- Main ----------
def main() -> int:
    if "--test-notification" in sys.argv[1:]:
        return send_test_notification()

    if FORCE_PROSPECTS:
        return write_prospects_mode("SCANNER_PROSPECTS_ONLY set")

    all_deals: list[dict] = []
    weekends = list(next_weekends(WEEKENDS_AHEAD))
    http_client = (
        f"curl_cffi impersonate={CURL_IMPERSONATE}"
        if _CURL_CFFI_AVAILABLE
        else "plain python-requests (likely to 403)"
    )
    print(
        f"Scanning {len(weekends)} weekends from {weekends[0][0]} "
        f"to {weekends[-1][1]} for fares <= EUR {PRICE_CAP_EUR} "
        f"(Ryanair public fare-finder, HTTP client: {http_client})..."
    )

    total_calls = 0
    failed_calls = 0
    error_summary: dict[str, int] = {}
    for origin in ORIGINS:
        for friday, sunday in weekends:
            total_calls += 1
            label = f"{origin} {friday}->{sunday}"
            try:
                data = fetch_fares(origin, friday, sunday)
            except requests.HTTPError as e:
                failed_calls += 1
                code = e.response.status_code if e.response is not None else "?"
                err_key = f"HTTP {code}"
                error_summary[err_key] = error_summary.get(err_key, 0) + 1
                # Log the body once per distinct status code so we can see
                # Cloudflare challenge pages etc.
                if error_summary[err_key] == 1 and e.response is not None:
                    snippet = e.response.text[:200].replace("\n", " ")
                    print(
                        f"  [warn] {label}: HTTP {code}  body: {snippet!r}",
                        file=sys.stderr,
                    )
                else:
                    print(f"  [warn] {label}: HTTP {code}", file=sys.stderr)
                continue
            except requests.RequestException as e:
                failed_calls += 1
                err_key = type(e).__name__
                error_summary[err_key] = error_summary.get(err_key, 0) + 1
                print(f"  [warn] {label}: {err_key}: {e}", file=sys.stderr)
                continue

            fares = data.get("fares") or []
            kept = 0
            for fare in fares:
                deal = normalise_fare(fare, origin)
                if deal is None:
                    continue
                if deal["effective_price_eur"] <= PRICE_CAP_EUR:
                    all_deals.append(deal)
                    kept += 1
            print(f"  {label}: {len(fares)} fares, {kept} under cap")
            # Be polite to the public endpoint.
            time.sleep(0.3)

    print(
        f"\nRyanair scan summary: {total_calls} calls, "
        f"{total_calls - failed_calls} ok, {failed_calls} failed, "
        f"{len(all_deals)} deals under cap."
    )
    if error_summary:
        print(
            "Failure breakdown: "
            + ", ".join(f"{k}={v}" for k, v in sorted(error_summary.items()))
        )

    # If Ryanair was totally unreachable (every call failed), fall back
    # to the route-catalogue so the dashboard still has *something* to
    # render instead of an empty deals.json.
    if total_calls > 0 and failed_calls == total_calls:
        print(
            f"All {total_calls} Ryanair calls failed -- falling back to prospects mode.",
            file=sys.stderr,
        )
        return write_prospects_mode("Ryanair unreachable")

    # Dedupe on (origin, destination, outbound date) keeping the cheapest.
    dedup: dict[tuple[str, str, str], dict] = {}
    for d in all_deals:
        key = (d["origin"], d["destination_iata"], d["outbound_departure"][:10])
        if key not in dedup or d["effective_price_eur"] < dedup[key]["effective_price_eur"]:
            dedup[key] = d

    deals = sorted(dedup.values(), key=lambda x: x["effective_price_eur"])

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "price_cap_eur": PRICE_CAP_EUR,
        "bus_return_cost_eur": BUS_RETURN_COST_EUR,
        "origins": ORIGINS,
        "weekends_scanned": len(weekends),
        "mode": "live",
        "source": "ryanair-farfnd",
        "deals": deals,
    }

    # Notify BEFORE we overwrite deals.json, so the notifier can compare
    # the new scan against the old file on disk. Wrapped in a bare try
    # so a Discord/ntfy failure never tanks the scan itself.
    try:
        from notifier import notify_if_configured
        notify_if_configured(OUTPUT_PATH, deals)
    except Exception as e:
        print(f"  [notify] error: {e}", file=sys.stderr)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {len(deals)} deals to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
