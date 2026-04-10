#!/usr/bin/env python3
"""Weekend Getaway Flight Scanner.

Scans the Kiwi.com Tequila API for cheap round-trip weekend flights
(Fri evening out, Sun evening back) from Shannon (preferred) and
Dublin into Europe, capped at EUR 100. Dublin fares are adjusted
upward by the cost of a return Limerick<->Dublin bus so the two
origins can be compared on an "effective price from Limerick" basis.

Booking links point at Google Flights and Skyscanner (stable public
URL schemes), not the carrier's own site. Kiwi aggregates Ryanair,
Aer Lingus, Wizz, easyJet and others, so coverage is broader than
hitting Ryanair directly.

Setup:
    1. Get a free API key from https://tequila.kiwi.com
    2. export KIWI_API_KEY='your-key-here'
    3. pip install -r requirements.txt
    4. python scanner.py

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

# ---------- Config ----------
PRICE_CAP_EUR = 100.0
# Approx Limerick <-> Dublin return via Bus Eireann / Citylink / Dublin Coach.
BUS_RETURN_COST_EUR = 30.0
ORIGINS = ["SNN", "DUB"]  # Shannon first, Dublin as fallback.
WEEKENDS_AHEAD = 26       # Scan ~6 months of upcoming weekends (live mode).

# European destinations to include. Kiwi accepts a comma-separated list
# of 2-letter country codes as `fly_to`. Ireland is excluded.
EUROPE_COUNTRIES = (
    "GB,FR,DE,ES,IT,PT,NL,BE,LU,AT,CH,CZ,PL,HU,SK,SI,HR,"
    "GR,RO,BG,RS,ME,MK,AL,BA,SE,DK,NO,FI,EE,LV,LT,IS,MT,CY"
)

# Friday evening departures and Sunday afternoon/evening returns.
OUTBOUND_FROM = "16:00"
OUTBOUND_TO = "23:59"
INBOUND_FROM = "15:00"
INBOUND_TO = "23:59"

# Weekend getaways: direct flights only, cabin bag implicit.
MAX_STOPOVERS = 0
MAX_FLY_DURATION_HOURS = 5

KIWI_URL = "https://api.tequila.kiwi.com/v2/search"
API_KEY = os.environ.get("KIWI_API_KEY", "").strip()

OUTPUT_PATH = Path(__file__).parent / "dashboard" / "deals.json"

# How many upcoming weekends to generate prospect links for when we
# have no API key and can only act as a route catalogue. Kept smaller
# than the live-mode window because prospects mode has no price filter
# and the card count is `routes x weekends`, which balloons fast.
PROSPECTS_WEEKENDS = 8

# Verified direct routes to Europe from Shannon and Dublin (Ryanair /
# Aer Lingus / easyJet as of recent schedules). Used for "prospects"
# mode when no Kiwi API key is available -- every (origin, dest)
# entry becomes a click-through card to Google Flights / Skyscanner.
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


def kiwi_date(d: dt.date) -> str:
    """Kiwi Tequila expects DD/MM/YYYY."""
    return d.strftime("%d/%m/%Y")


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


# ---------- Kiwi fetch ----------
def fetch_fares(origin: str, friday: dt.date, sunday: dt.date) -> dict:
    params = {
        "fly_from": origin,
        "fly_to": EUROPE_COUNTRIES,
        "date_from": kiwi_date(friday),
        "date_to": kiwi_date(friday),
        "return_from": kiwi_date(sunday),
        "return_to": kiwi_date(sunday),
        "flight_type": "round",
        "curr": "EUR",
        # Ask for slightly more than our cap so Dublin fares that would fit
        # after adding the bus surcharge still come through.
        "price_to": int(PRICE_CAP_EUR + BUS_RETURN_COST_EUR),
        "max_stopovers": MAX_STOPOVERS,
        "max_fly_duration": MAX_FLY_DURATION_HOURS,
        "dtime_from": OUTBOUND_FROM,
        "dtime_to": OUTBOUND_TO,
        "ret_dtime_from": INBOUND_FROM,
        "ret_dtime_to": INBOUND_TO,
        "adults": 1,
        "limit": 200,
        "sort": "price",
        "asc": 1,
    }
    headers = {"apikey": API_KEY, "accept": "application/json"}
    resp = requests.get(KIWI_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------- Normalisation ----------
def normalise_fare(item: dict, origin: str) -> dict | None:
    """Turn a Kiwi `data[*]` entry into our flat deal schema."""
    dest_iata = item.get("flyTo")
    if not dest_iata:
        return None

    try:
        lat = float(item["latTo"])
        lon = float(item["lngTo"])
    except (KeyError, TypeError, ValueError):
        return None

    price = float(item.get("price", 0))
    bus = 0.0 if origin == "SNN" else BUS_RETURN_COST_EUR
    effective = price + bus

    country = item.get("countryTo") or {}
    country_name = country.get("name", "") if isinstance(country, dict) else ""

    # A round-trip item's `route` contains segments tagged with `return`:
    # 0 for outbound legs, 1 for inbound legs.
    route = item.get("route") or []
    outbound_segs = [s for s in route if s.get("return", 0) == 0]
    inbound_segs = [s for s in route if s.get("return", 0) == 1]
    if not outbound_segs or not inbound_segs:
        return None

    out_first, out_last = outbound_segs[0], outbound_segs[-1]
    in_first, in_last = inbound_segs[0], inbound_segs[-1]

    out_dep = out_first.get("local_departure", "")
    out_arr = out_last.get("local_arrival", "")
    in_dep = in_first.get("local_departure", "")
    in_arr = in_last.get("local_arrival", "")

    if not out_dep or not in_dep:
        return None

    # Belt-and-braces: reject anything outside the Fri-evening /
    # Sun-afternoon-evening windows even if Kiwi returned it. This is
    # what the previous sandbox experiment tripped on: a 09:20 morning
    # flight was not a "Friday evening getaway" no matter what the API
    # said. Compare on the HH:MM slice of the ISO local_departure.
    out_hhmm = out_dep[11:16]  # "YYYY-MM-DDTHH:MM:SS" -> "HH:MM"
    in_hhmm = in_dep[11:16]
    if not (OUTBOUND_FROM <= out_hhmm <= OUTBOUND_TO):
        return None
    if not (INBOUND_FROM <= in_hhmm <= INBOUND_TO):
        return None

    out_date = out_dep[:10]
    in_date = in_dep[:10]

    out_flight = f"{out_first.get('airline', '')}{out_first.get('flight_no', '')}".strip()
    in_flight = f"{in_first.get('airline', '')}{in_first.get('flight_no', '')}".strip()

    return {
        "origin": origin,
        "destination_iata": dest_iata,
        "destination_city": item.get("cityTo", dest_iata),
        "destination_country": country_name,
        "destination_lat": lat,
        "destination_lon": lon,
        "flight_price_eur": round(price, 2),
        "bus_surcharge_eur": round(bus, 2),
        "effective_price_eur": round(effective, 2),
        "currency": "EUR",
        "outbound_departure": out_dep,
        "outbound_arrival": out_arr,
        "outbound_flight_number": out_flight,
        "inbound_departure": in_dep,
        "inbound_arrival": in_arr,
        "inbound_flight_number": in_flight,
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

    Used as an honest fallback when we don't have a Kiwi API key: we
    can't claim to know fares, so we produce click-through cards that
    open Google Flights / Skyscanner for the user to check live prices.

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


def write_prospects_mode() -> int:
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
    print(
        f"No KIWI_API_KEY set -- writing {len(entries)} route prospects "
        f"({sum(len(v) for v in EUROPE_ROUTES.values())} routes x "
        f"{len(weekends)} weekends) to {OUTPUT_PATH}.\n"
        f"\n"
        f"Click any Google Flights / Skyscanner link in the dashboard to "
        f"see the live price. For automated under-EUR {PRICE_CAP_EUR:.0f} "
        f"filtering, get a free key at https://tequila.kiwi.com and set "
        f"KIWI_API_KEY."
    )
    return 0


# ---------- Main ----------
def main() -> int:
    if not API_KEY:
        return write_prospects_mode()

    all_deals: list[dict] = []
    weekends = list(next_weekends(WEEKENDS_AHEAD))
    print(
        f"Scanning {len(weekends)} weekends from {weekends[0][0]} "
        f"to {weekends[-1][1]} for fares <= EUR {PRICE_CAP_EUR}..."
    )

    for origin in ORIGINS:
        for friday, sunday in weekends:
            label = f"{origin} {friday}->{sunday}"
            try:
                data = fetch_fares(origin, friday, sunday)
            except requests.RequestException as e:
                print(f"  [warn] {label}: {e}", file=sys.stderr)
                continue

            items = data.get("data") or []
            kept = 0
            for item in items:
                deal = normalise_fare(item, origin)
                if deal is None:
                    continue
                if deal["effective_price_eur"] <= PRICE_CAP_EUR:
                    all_deals.append(deal)
                    kept += 1
            print(f"  {label}: {len(items)} results, {kept} under cap")
            # Be polite to the free tier.
            time.sleep(0.4)

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
        "source": "kiwi-tequila",
        "deals": deals,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {len(deals)} deals to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
