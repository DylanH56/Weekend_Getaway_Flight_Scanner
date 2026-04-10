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
import signal
import sys
import threading
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
    try:
        import curl_cffi as _curl_cffi_pkg  # type: ignore
        _CURL_CFFI_VERSION = getattr(_curl_cffi_pkg, "__version__", "unknown")
    except Exception:
        _CURL_CFFI_VERSION = "unknown"
except ImportError:
    _curl_requests = None  # type: ignore
    _CURL_CFFI_AVAILABLE = False
    _CURL_CFFI_VERSION = "not installed"

# When using curl_cffi, which Chrome build to impersonate. chrome120
# is present in every 0.7.x release of curl_cffi, which is the floor
# we pin in requirements.txt. chrome124/131 are newer but not in every
# 0.7.x -- picking a safer value avoids a ValueError at runtime if pip
# resolves to an older patch release. Keep the numeric version aligned
# with the RYANAIR_HEADERS Sec-Ch-Ua fields so the handshake and the
# client hints don't contradict each other.
CURL_IMPERSONATE = "chrome120"


def _http_get_with_watchdog(url: str, watchdog_seconds: float, **kwargs):
    """Run _http_get on a daemon thread with a hard wall-clock cap.

    If the underlying call hangs -- inside libcurl, inside requests,
    inside DNS, doesn't matter -- the daemon thread keeps running but
    the main thread joins with a timeout and raises. The leaked thread
    is daemon=True so it doesn't prevent process exit.

    Layer 1 of the 4-layer Aer Lingus anti-hang defense. See commit
    message for the full set.

    Note: Python can't forcibly kill threads, so a leaked thread holds
    whatever resources curl_cffi/requests allocated for the hung call.
    For a short-lived scanner process that exits at the end of _run(),
    this is fine -- OS cleanup handles it.
    """
    result: dict = {}

    def worker() -> None:
        try:
            result["resp"] = _http_get(url, **kwargs)
        except BaseException as e:  # noqa: BLE001
            result["exc"] = e

    t = threading.Thread(target=worker, daemon=True, name="al-http-watchdog")
    t.start()
    t.join(timeout=watchdog_seconds)

    if t.is_alive():
        raise TimeoutError(
            f"watchdog: {url} exceeded {watchdog_seconds:.0f}s hard cap"
        )
    if "exc" in result:
        raise result["exc"]
    return result.get("resp")


def _http_get(url: str, *, allow_plain_fallback: bool = True, **kwargs):
    """Single entry point for outbound HTTP GETs to Ryanair.

    Routes through curl_cffi when available so the TLS handshake
    matches Chrome's; falls back to plain requests otherwise. Kept as
    a standalone helper so tests can monkey-patch exactly one symbol.

    If curl_cffi raises ANYTHING (unknown impersonate profile, API
    shape change in a future release, libcurl runtime mismatch, etc.)
    we log it, flip the global flag so subsequent calls skip
    curl_cffi, and (by default) retry with plain requests.

    ``allow_plain_fallback=False`` disables the plain-requests retry
    for callers that CANNOT tolerate it. Specifically: Aer Lingus,
    whose Cloudflare layer appears to hang indefinitely on plain
    requests -- observed at line 459 of scan #N, where after a
    curl_cffi 10s timeout the code silently blocks forever inside
    ``requests.get()``. For those callers we re-raise the curl_cffi
    exception and let the caller decide whether to retry or abort.
    Ryanair still defaults to allow_plain_fallback=True since it's
    been observed to work fine with plain requests.
    """
    global _CURL_CFFI_AVAILABLE  # noqa: PLW0603
    if _CURL_CFFI_AVAILABLE:
        try:
            return _curl_requests.get(
                url, impersonate=CURL_IMPERSONATE, **kwargs
            )
        except Exception as e:
            print(
                f"  [warn] curl_cffi call failed ({type(e).__name__}: {e}); "
                f"{'falling back to plain requests for the rest of this run.' if allow_plain_fallback else 'plain-requests fallback disabled for this caller, re-raising.'}",
                file=sys.stderr,
            )
            _CURL_CFFI_AVAILABLE = False
            if not allow_plain_fallback:
                raise
    return requests.get(url, **kwargs)


# ---------- Config ----------
# Upper bound for the scanner's flight-price filter (applied to
# Ryanair's raw round-trip fare, NOT the Limerick-bus-adjusted number).
# The dashboard renders a slider defaulting to EUR 100 that re-filters
# the already-loaded deals client-side, so EUR 150 here gives the user
# 50 euro of slack either way without re-running the scan.
PRICE_CAP_EUR = 150.0
# Approx Limerick <-> Dublin return via Bus Eireann / Citylink / Dublin Coach.
# NOT added to flight_price_eur anymore -- shown separately on the card
# as an informational note so Dublin and Shannon are compared on raw
# Ryanair price. The bus_surcharge_eur field is still emitted in each
# deal so the dashboard can display it if the user wants to see the
# total door-to-door cost.
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
# Mirror of every print() the scanner emits, written next to deals.json
# so the branch itself carries a diagnostic trace even when we can't get
# at the GitHub Actions step log. Small enough to commit on every run.
LOG_PATH = Path(__file__).parent / "dashboard" / "last_scan_log.txt"
# Rolling price history, appended on every scan and pruned to the
# retention window defined in history.py. Used to annotate each deal
# with lowest_ever_eur / price_delta_eur and to fire "cheapest ever"
# Discord alerts.
HISTORY_PATH = Path(__file__).parent / "dashboard" / "history.json"
# Persistent Wikipedia thumbnail cache: once we know Birmingham's
# thumbnail URL it's stable, so we never refetch.
PHOTO_CACHE_PATH = Path(__file__).parent / "dashboard" / "city_photos.json"
# Short-lived weather forecast cache, TTL managed by enrichments.py.
WEATHER_CACHE_PATH = Path(__file__).parent / "dashboard" / "weather.json"

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
# Weekend window definitions. Each entry is:
#   (id, label, out_offset_from_friday, in_offset_from_friday)
# where offsets are in days from the base Friday. The classic Fri->Sun
# is (0, 2); Thursday night kick-off is (-1, 2); long weekend with
# Monday return is (0, 3); bank-holiday Fri->Tue is (0, 4).
#
# The scanner queries every enabled window for every base weekend,
# tags each deal with `weekend_window`, and the dashboard surfaces a
# filter chip so the user can pick their preferred window(s).
WEEKEND_WINDOWS: list[tuple[str, str, int, int]] = [
    ("fri_sun", "Fri \u2192 Sun",   0, 2),  # classic 2-night weekend
    ("thu_sun", "Thu \u2192 Sun",  -1, 2),  # 3-night, leave Thu evening
    ("fri_mon", "Fri \u2192 Mon",   0, 3),  # 3-night, return Mon
    ("fri_tue", "Fri \u2192 Tue",   0, 4),  # 4-night, bank-holiday long weekend
]


def next_weekends(n: int) -> Iterator[tuple[dt.date, dt.date]]:
    """Yield (friday, sunday) pairs for the next `n` upcoming weekends.

    Retained for backwards compatibility with code and tests that
    just want the classic Fri->Sun pair. The scanner itself iterates
    next_weekend_windows() so that multi-window scans fan out
    automatically.
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


def next_weekend_windows(
    n: int,
    windows: list[tuple[str, str, int, int]] | None = None,
) -> Iterator[tuple[str, str, dt.date, dt.date]]:
    """Yield (window_id, window_label, out_date, in_date) for every
    (weekend, window) combination across the next `n` base Fridays.

    Each base Friday fans out across the enabled weekend windows.
    Callers get concrete outbound/return dates already computed.
    """
    if windows is None:
        windows = WEEKEND_WINDOWS
    today = dt.date.today()
    days_to_friday = (4 - today.weekday()) % 7
    if days_to_friday == 0:
        days_to_friday = 7
    first_friday = today + dt.timedelta(days=days_to_friday)
    for i in range(n):
        friday = first_friday + dt.timedelta(weeks=i)
        for window_id, window_label, out_offset, in_offset in windows:
            out_date = friday + dt.timedelta(days=out_offset)
            in_date = friday + dt.timedelta(days=in_offset)
            yield window_id, window_label, out_date, in_date


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
def _ryanair_fetch_fares(origin: str, friday: dt.date, sunday: dt.date) -> dict:
    """Hit Ryanair's public round-trip fare-finder for a single weekend.

    Deliberately does NOT send outboundDepartureTimeFrom / inboundDepartureTimeFrom --
    those params caused 400 Bad Request on the run at commit abff9ca, most
    likely because farfnd/v4 doesn't support them anymore. The belt-and-braces
    HH:MM check in `normalise_fare` still enforces the evening window, so we
    lose nothing by dropping the API-level filter.

    Also normalises whatever exception the HTTP client raises for >= 400
    responses into a plain `requests.HTTPError` with the response attached,
    so the main loop's existing HTTPError handler sees a consistent type no
    matter whether curl_cffi or plain requests made the call.
    """
    # Minimal param set matching what ryanair.com's own fare-finder page
    # sends. The previous iteration included `limit` and `offset` which
    # farfnd/v4 rejects with `{"code":"InvalidLimit","message":"Invalid limit"}`.
    # Any extra param that the endpoint doesn't recognise is a hard 400,
    # so stick to the documented set only.
    params = {
        "departureAirportIataCode": origin,
        "market": "en-ie",
        "adultPaxCount": "1",
        "outboundDepartureDateFrom": friday.isoformat(),
        "outboundDepartureDateTo": friday.isoformat(),
        "inboundDepartureDateFrom": sunday.isoformat(),
        "inboundDepartureDateTo": sunday.isoformat(),
        # Filter on raw flight price directly; the bus surcharge isn't
        # added to the headline number anymore (user wants SNN and DUB
        # compared on like-for-like Ryanair cost). The dashboard slider
        # re-filters client-side, defaults to EUR 100.
        "priceValueTo": str(int(PRICE_CAP_EUR)),
        "currency": "EUR",
    }
    resp = _http_get(
        RYANAIR_URL, params=params, headers=RYANAIR_HEADERS, timeout=30
    )

    status = getattr(resp, "status_code", None)
    if status is None or status >= 400:
        body_snippet = ""
        try:
            body_snippet = (resp.text or "")[:300].replace("\n", " ")
        except Exception:
            pass
        raise requests.HTTPError(
            f"HTTP {status} for Ryanair farfnd  body={body_snippet!r}",
            response=resp,
        )

    return resp.json()


# ---------- Normalisation ----------
# Per-run reject counters for visibility. Bumped in _*_normalise_fare
# helpers and dumped at the end of the scan. Helps diagnose "API
# returned 700 fares, we kept 0" situations where we need to know
# WHICH gate ate everything.
_reject_counts: dict[str, int] = {
    "missing_fields": 0,
    "missing_dates": 0,
    "outside_evening_window": 0,
    "missing_iata": 0,
    "unknown_destination": 0,
}


def _apply_common_filters(
    origin: str,
    dest_iata: str,
    flight_price: float,
    out_dep: str,
    in_dep: str,
) -> tuple[float | None, float | None, str, str] | None:
    """Shared post-normalisation checks used by every source.

    Returns (lat, lon, out_hhmm, in_hhmm) if the fare passes all the
    common filters (evening window, IATA present, etc); returns None
    and bumps the appropriate reject counter otherwise.
    """
    if not dest_iata:
        _reject_counts["missing_iata"] += 1
        return None
    if not out_dep or not in_dep:
        _reject_counts["missing_dates"] += 1
        return None

    # Belt-and-braces evening window check.
    out_hhmm = out_dep[11:16]
    in_hhmm = in_dep[11:16]
    if not (OUTBOUND_FROM <= out_hhmm <= OUTBOUND_TO):
        _reject_counts["outside_evening_window"] += 1
        return None
    if not (INBOUND_FROM <= in_hhmm <= INBOUND_TO):
        _reject_counts["outside_evening_window"] += 1
        return None

    fallback = _IATA_COORDS.get(dest_iata)
    if fallback is not None:
        lat, lon = fallback
    else:
        _reject_counts["unknown_destination"] += 1
        lat = lon = None

    return (lat, lon, out_hhmm, in_hhmm)

# Supplementary IATA -> (lat, lon) map for destinations Ryanair returns
# that aren't in EUROPE_ROUTES. EUROPE_ROUTES is deliberately kept
# small because prospects mode renders one card per (origin, dest,
# weekend) and ballooning it 3x would make the prospects fallback
# unusable. This extra lookup is *only* consulted from normalise_fare
# during live scans, so adding destinations here doesn't bloat the
# fallback at all. Coverage focus: the destinations we saw in the
# `unknown_destination=194` reject bucket from run #12.
_EXTRA_IATA_COORDS: dict[str, tuple[float, float]] = {
    # --- Spain / Canaries / Balearics ---
    "SVQ": (37.4180, -5.8931),     # Seville
    "VLC": (39.4893, -0.4816),     # Valencia
    "BIO": (43.3011, -2.9106),     # Bilbao
    "SDR": (43.4271, -3.8200),     # Santander
    "IBZ": (38.8729, 1.3731),      # Ibiza
    "PMI": (39.5517, 2.7388),      # Palma de Mallorca
    "MAH": (39.8626, 4.2187),      # Menorca
    "TFS": (28.0445, -16.5725),    # Tenerife South
    "TFN": (28.4827, -16.3415),    # Tenerife North
    "LPA": (27.9319, -15.3866),    # Gran Canaria
    "FUE": (28.4527, -13.8638),    # Fuerteventura
    "REU": (41.1474, 1.1672),      # Reus (Tarragona / Costa Dorada)
    "XRY": (36.7446, -6.0601),     # Jerez
    # --- Italy ---
    "CAG": (39.2515, 9.0543),      # Cagliari
    "OLB": (40.8987, 9.5176),      # Olbia (Sardinia)
    "AHO": (40.6321, 8.2908),      # Alghero
    "PMO": (38.1759, 13.0910),     # Palermo
    "CTA": (37.4668, 15.0664),     # Catania
    "TRN": (45.2008, 7.6496),      # Turin
    "PSR": (42.4317, 14.1811),     # Pescara
    "BRI": (41.1389, 16.7606),     # Bari
    "BDS": (40.6576, 17.9470),     # Brindisi
    "TRS": (45.8275, 13.4722),     # Trieste
    "VRN": (45.3957, 10.8885),     # Verona
    "AOI": (43.6163, 13.3623),     # Ancona
    "GOA": (44.4134, 8.8374),      # Genoa
    # --- France ---
    "NCE": (43.6584, 7.2158),      # Nice
    "MRS": (43.4367, 5.2148),      # Marseille
    "TLS": (43.6293, 1.3638),      # Toulouse
    "BOD": (44.8283, -0.7156),     # Bordeaux
    "BIQ": (43.4683, -1.5311),     # Biarritz
    "LIL": (50.5633, 3.0894),      # Lille
    "NTE": (47.1569, -1.6078),     # Nantes
    "LYS": (45.7256, 5.0811),      # Lyon
    "MPL": (43.5762, 3.9630),      # Montpellier
    "PGF": (42.7404, 2.8706),      # Perpignan
    "CCF": (43.2160, 2.3063),      # Carcassonne
    "BVE": (45.1508, 1.4697),      # Brive
    "RDZ": (44.4079, 2.4827),      # Rodez
    # --- Germany / Austria / Switzerland ---
    "HHN": (49.9487, 7.2639),      # Frankfurt Hahn
    "NRN": (51.6025, 6.1422),      # Weeze (Düsseldorf area)
    "BSL": (47.5896, 7.5299),      # EuroAirport Basel
    "MLH": (47.5896, 7.5299),      # Same airport, French IATA
    # --- Belgium / Netherlands / Luxembourg ---
    "EIN": (51.4500, 5.3747),      # Eindhoven
    "LUX": (49.6233, 6.2044),      # Luxembourg
    "MST": (50.9117, 5.7704),      # Maastricht
    # --- UK / Ireland ---
    "BHX": (52.4539, -1.7480),     # Birmingham (already in DUB list, dedup is fine)
    "CWL": (51.3967, -3.3432),     # Cardiff
    "LBA": (53.8659, -1.6606),     # Leeds Bradford
    "NCL": (55.0375, -1.6916),     # Newcastle
    "EMA": (52.8311, -1.3281),     # East Midlands
    "BOH": (50.7800, -1.8425),     # Bournemouth
    "NWI": (52.6758, 1.2828),      # Norwich
    "EXT": (50.7344, -3.4139),     # Exeter
    "SNN": (52.7020, -8.9249),     # Shannon (self, for map anchor)
    "DUB": (53.4213, -6.2700),     # Dublin (self, for map anchor)
    "KIR": (52.1809, -9.5238),     # Kerry
    "ORK": (51.8413, -8.4911),     # Cork
    "IOM": (54.0833, -4.6239),     # Isle of Man
    # --- Poland extras ---
    "WMI": (52.4511, 20.6518),     # Warsaw Modlin
    "POZ": (52.4210, 16.8263),     # Poznan
    "KTW": (50.4743, 19.0800),     # Katowice
    "LUZ": (51.2403, 22.7136),     # Lublin
    "RZE": (50.1100, 22.0190),     # Rzeszow
    "SZZ": (53.5847, 14.9022),     # Szczecin
    "BZG": (53.0968, 17.9777),     # Bydgoszcz
    # --- Czech / Slovakia / Hungary ---
    "BTS": (48.1702, 17.2127),     # Bratislava
    "BRQ": (49.1513, 16.6944),     # Brno
    "OSR": (49.6963, 18.1111),     # Ostrava
    "DEB": (47.4889, 21.6153),     # Debrecen
    # --- Croatia / Slovenia / Balkans ---
    "ZAG": (45.7429, 16.0688),     # Zagreb
    "ZAD": (44.1083, 15.3467),     # Zadar
    "PUY": (44.8935, 13.9222),     # Pula
    "SPU": (43.5389, 16.2980),     # Split
    "DBV": (42.5614, 18.2682),     # Dubrovnik
    "LJU": (46.2237, 14.4576),     # Ljubljana
    "SJJ": (43.8247, 18.3315),     # Sarajevo
    "BEG": (44.8184, 20.3091),     # Belgrade
    "SKP": (41.9616, 21.6214),     # Skopje
    "TIA": (41.4147, 19.7206),     # Tirana
    "TGD": (42.3594, 19.2519),     # Podgorica
    # --- Greece ---
    "ATH": (37.9364, 23.9445),     # Athens
    "SKG": (40.5197, 22.9709),     # Thessaloniki
    "CHQ": (35.5317, 24.1497),     # Chania
    "HER": (35.3397, 25.1803),     # Heraklion
    "RHO": (36.4054, 28.0862),     # Rhodes
    "KGS": (36.7933, 27.0917),     # Kos
    "JTR": (36.3992, 25.4793),     # Santorini
    "JMK": (37.4351, 25.3481),     # Mykonos
    "KLX": (37.6818, 21.2955),     # Kalamata
    "PVK": (38.9254, 20.7653),     # Preveza
    "CFU": (39.6018, 19.9118),     # Corfu
    # --- Nordic ---
    "OSL": (60.1939, 11.1004),     # Oslo Gardermoen
    "TRF": (59.1867, 10.2586),     # Sandefjord Torp (Oslo area)
    "NYO": (58.7886, 16.9122),     # Stockholm Skavsta
    "GOT": (57.6686, 12.2950),     # Gothenburg
    "HEL": (60.3172, 24.9633),     # Helsinki
    "BLL": (55.7403, 9.1518),      # Billund
    "AAL": (57.0928, 9.8492),      # Aalborg
    # --- Baltic ---
    "RIX": (56.9236, 23.9711),     # Riga
    "VNO": (54.6341, 25.2858),     # Vilnius
    "KUN": (54.9639, 24.0848),     # Kaunas
    "TLL": (59.4133, 24.8328),     # Tallinn
    # --- Morocco / Israel / Other ---
    "RAK": (31.6069, -8.0363),     # Marrakesh
    "FEZ": (33.9272, -4.9781),     # Fez
    "AGA": (30.3250, -9.4131),     # Agadir
    "TNG": (35.7269, -5.9168),     # Tangier
    "NDR": (34.9888, -3.0282),     # Nador
    # --- Portugal ---
    "FNC": (32.6979, -16.7745),    # Madeira (Funchal)
    "PDL": (37.7412, -25.6979),    # Ponta Delgada (Azores)
    "TER": (38.7593, -27.0908),    # Terceira (Azores)
    # --- Cyprus / Malta ---
    "LCA": (34.8751, 33.6249),     # Larnaca
    "PFO": (34.7180, 32.4857),     # Paphos
    # --- Romania / Bulgaria ---
    "OTP": (44.5711, 26.0858),     # Bucharest Otopeni
    "CLJ": (46.7852, 23.6862),     # Cluj-Napoca
    "TSR": (45.8098, 21.3379),     # Timisoara
    "IAS": (47.1785, 27.6205),     # Iasi
    "SOF": (42.6952, 23.4114),     # Sofia
    "BOJ": (42.5696, 27.5152),     # Burgas
    "VAR": (43.2321, 27.8251),     # Varna
}

# IATA -> (lat, lon) lookup combining EUROPE_ROUTES (for prospects
# mode + live) with _EXTRA_IATA_COORDS (live only). Ryanair's farfnd/v4
# response no longer carries a `coordinates` field on the arrival
# airport, so we source them ourselves. Anything not in this combined
# map comes through with lat/lon null; the dashboard renders those
# deals in the sidebar but skips the map marker, and the
# `unknown_destination` reject counter tracks how many we're missing.
_IATA_COORDS: dict[str, tuple[float, float]] = {}
for _origin_routes in EUROPE_ROUTES.values():
    for _iata, _city, _country, _lat, _lon in _origin_routes:
        _IATA_COORDS.setdefault(_iata, (_lat, _lon))
for _iata, _latlon in _EXTRA_IATA_COORDS.items():
    _IATA_COORDS.setdefault(_iata, _latlon)


def _city_name(airport: dict) -> str:
    city = airport.get("city")
    if isinstance(city, dict):
        return city.get("name") or airport.get("name", "")
    return airport.get("cityName") or airport.get("name", "")


def _ryanair_normalise_fare(fare: dict, origin: str) -> dict | None:
    """Turn a Ryanair `fares[*]` entry into our flat deal schema."""
    try:
        outbound = fare["outbound"]
        inbound = fare["inbound"]
        arr = outbound["arrivalAirport"]
        summary = fare["summary"]["price"]
    except (KeyError, TypeError):
        _reject_counts["missing_fields"] += 1
        return None

    dest_iata = arr.get("iataCode", "")
    flight_price = float(summary.get("value", 0))
    bus = 0.0 if origin == "SNN" else BUS_RETURN_COST_EUR
    effective = flight_price + bus

    out_dep = outbound.get("departureDate", "")
    out_arr = outbound.get("arrivalDate", "")
    in_dep = inbound.get("departureDate", "")
    in_arr = inbound.get("arrivalDate", "")

    common = _apply_common_filters(origin, dest_iata, flight_price, out_dep, in_dep)
    if common is None:
        return None
    lat, lon, _out_hhmm, _in_hhmm = common

    out_date = out_dep[:10]
    in_date = in_dep[:10]

    return {
        "origin": origin,
        "carrier_code": "FR",
        "carrier_name": "Ryanair",
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


# ---------- Aer Lingus fetch ----------
# Aer Lingus uses a GET endpoint at /api/v2/flights/fixed that returns
# a round-trip availability search. Endpoint confirmed via a real
# browser capture (see session 01QJRsJg6woitt3nmdwvKQdr). Response
# shape is data.journey.{outbound,inbound}.flights[] where each flight
# is a one-way segment with its own priceInfo.fares[] list; we have
# to pair the cheapest outbound `low` fare with the cheapest inbound
# `low` fare ourselves and sum the prices for the round-trip total.
AER_LINGUS_URL = "https://www.aerlingus.com/api/v2/flights/fixed"
# Fallback URLs no longer needed; left empty to keep the code shape.
AER_LINGUS_FALLBACK_URLS: list[str] = []

_AER_LINGUS_WORKING_URL: str | None = None
# Global kill-switch: flipped to True if EVERY call on the first
# weekend fails, OR if DISABLE_AER_LINGUS env var is set to truthy.
# Once flipped, every subsequent _aer_lingus_fetch_fares returns empty
# immediately. Reset on next module import (fresh workflow run).
#
# Set DISABLE_AER_LINGUS=1 as a repo variable (Settings -> Secrets and
# variables -> Actions -> Variables) to emergency-disable AL without
# a code push. Takes effect on the next scheduled run.
_AER_LINGUS_DISABLED: bool = (
    os.environ.get("DISABLE_AER_LINGUS", "").strip().lower()
    not in ("", "0", "false", "no", "off")
)
if _AER_LINGUS_DISABLED:
    print(
        "  [info] aer_lingus: disabled via DISABLE_AER_LINGUS env var",
        file=sys.stderr,
    )

# Per-route kill-switch: (origin, dest) tuples that have failed enough
# consecutive weekends to earn a skip for the rest of the run. Populated
# by _aer_lingus_fetch_fares as failures accumulate. This is what
# actually keeps the log clean when *some* routes work and others don't
# -- e.g. LHR returns valid JSON but LGW/MAN/etc. 401 on every call.
_AER_LINGUS_DEAD_ROUTES: set[tuple[str, str]] = set()
# Consecutive-failure counter per (origin, dest). Reset to 0 on any
# successful JSON parse for that route.
_AER_LINGUS_ROUTE_FAILURES: dict[tuple[str, str], int] = {}
# How many failures in a row before a route is marked dead.
_AER_LINGUS_ROUTE_FAIL_THRESHOLD = 2
# Warning dedup: (origin, dest, error_key) tuples we've already printed
# at least once. Prevents the same HTTP 401 from being logged 13 times
# in a row for the same route.
_AER_LINGUS_WARNED: set[tuple[str, str, str]] = set()

# Wall-clock budget cap for the entire Aer Lingus portion of a scan.
# Set to 3 minutes: enough time for a healthy run (~60-120s), short
# enough that a network-hung CI run can't drag the total scan past
# ~5-6 minutes. Initialized on the first _aer_lingus_fetch_fares call
# of the run. Once elapsed, AL is disabled for the rest of the run.
_AER_LINGUS_START_TIME: float | None = None
_AER_LINGUS_BUDGET_SECONDS: float = 180.0

# Per-call timeout for the Aer Lingus HTTP call. 6s is aggressive but
# healthy responses come back in 500ms-2s, and anything slower is
# almost certainly a hung connection we don't want to wait on. Plain
# `requests` (the curl_cffi fallback) respects this; curl_cffi has its
# own 10s internal cap that fires first regardless.
_AER_LINGUS_CALL_TIMEOUT: float = 6.0

AER_LINGUS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Ch-Ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    # Aer Lingus's own frontend sends the literal string "null" for
    # the correlation id -- mirror that exactly, some backends reject
    # missing headers.
    "X-Correlation-Id": "null",
    "Priority": "u=1, i",
}

# Aer Lingus's network is broader than Ryanair's but per-call cost is
# much higher (one HTTP call per destination instead of farfnd's
# cheapest-across-all-dests). To keep the overall scan under ~5
# minutes we trim aggressively:
#   * SNN gets only LHR (the one route Ryanair doesn't cover).
#   * DUB gets the 8 biggest hubs -- enough to catch sales to London,
#     major European capitals, and Rome.
# Additional destinations can be added later as individual one-liners.
AER_LINGUS_DESTINATIONS: dict[str, list[str]] = {
    "SNN": ["LHR"],  # Shannon only flies to Heathrow on Aer Lingus
    "DUB": [
        "LHR",   # London Heathrow
        "LGW",   # London Gatwick
        "MAN",   # Manchester
        "EDI",   # Edinburgh
        "AMS",   # Amsterdam
        "BCN",   # Barcelona
        "MAD",   # Madrid
        "FCO",   # Rome Fiumicino
    ],
}


def _aer_lingus_referer(origin: str, dest: str, out_date: dt.date, in_date: dt.date) -> str:
    """Build the Referer header Aer Lingus expects -- matching what
    www.aerlingus.com/app/make/flight-search-result sends. Uses ISO
    dates in the referer (YYYY-MM-DD) even though the API query
    params use DD/MM/YYYY -- yes, really."""
    return (
        "https://www.aerlingus.com/app/make/flight-search-result"
        f"?fareType=RETURN&fareCategory=ECONOMY"
        f"&sourceAirportCode_0={origin}&destinationAirportCode_0={dest}"
        f"&departureDate_0={out_date.isoformat()}"
        f"&sourceAirportCode_1={dest}&destinationAirportCode_1={origin}"
        f"&departureDate_1={in_date.isoformat()}"
        f"&numAdults=1&numYoungAdults=0&numChildren=0&numInfants=0"
        f"&promoCode=&groupBooking=false"
    )


def _aer_lingus_cheapest_low_fare(flight: dict) -> dict | None:
    """Extract the cheapest `type: "low"` fare from a flight's
    priceInfo.fares[]. Returns None if there's no low fare available
    (e.g. only plus/flex/aerspace are on offer)."""
    fares = (flight.get("priceInfo") or {}).get("fares") or []
    low = [
        f for f in fares
        if f.get("type") == "low"
        and isinstance(f.get("price"), (int, float))
    ]
    if not low:
        return None
    return min(low, key=lambda f: f["price"])


def _aer_lingus_is_ei_operated(flight: dict) -> bool:
    """True iff the flight is actually flown by Aer Lingus (EI),
    not a BA / other codeshare sold under an EI flight number. We
    prefer real AL flights so the carrier badge stays meaningful."""
    trips = flight.get("trips") or []
    if not trips:
        return False
    info = (trips[0] or {}).get("info") or {}
    return info.get("operatingAirlineCode") == "EI"


def _maybe_warn_aer_lingus(
    origin: str, dest: str, error_key: str, extra: str | None = None
) -> None:
    """Log an Aer Lingus fetch failure, but only the first occurrence of
    each (origin, dest, error_key) tuple per run. Stops HTTP 401 from
    being printed 13 times for the same route when that route is stuck
    behind the same bot-check gate on every weekend."""
    key = (origin, dest, error_key)
    if key in _AER_LINGUS_WARNED:
        return
    _AER_LINGUS_WARNED.add(key)
    msg = f"  [warn] aer_lingus {origin}->{dest}: {error_key}"
    if extra:
        msg += f" ({extra})"
    print(msg, file=sys.stderr)


def _aer_lingus_fetch_fares(
    origin: str, friday: dt.date, sunday: dt.date
) -> dict:
    """Query Aer Lingus's /api/v2/flights/fixed endpoint per route.

    AL doesn't expose a Ryanair-style "cheapest per route from origin"
    call, so we loop over AER_LINGUS_DESTINATIONS[origin] and make one
    GET per (origin, dest, weekend). Response shape is:

        data.journey.outbound.flights[]    - outbound candidates
        data.journey.inbound.flights[]     - inbound candidates

    Each flight entry is ONE segment with a priceInfo.fares[] list of
    fare types (low / plus / flex / aerspace). We pick the cheapest
    `low` on each side, filter out BA codeshares, and synthesise a
    round-trip fare object by summing the two prices. That synthetic
    object is what _aer_lingus_normalise_fare consumes.

    Either direction can have `flights: null` + a SOLD_OUT message,
    in which case we skip that destination silently.

    Two layers of self-healing:
      * Per-route dead-list: (origin, dest) pairs are skipped after
        _AER_LINGUS_ROUTE_FAIL_THRESHOLD consecutive failures. Prevents
        the log from filling with 13x the same HTTP 401 for a route
        that's clearly not coming back this run.
      * Global kill-switch: if the *first* weekend's *every* call fails
        (Kasada full-block, DNS broken, etc.), flip _AER_LINGUS_DISABLED
        and skip AL entirely for the rest of the run.
    """
    global _AER_LINGUS_WORKING_URL, _AER_LINGUS_DISABLED
    global _AER_LINGUS_START_TIME

    if _AER_LINGUS_DISABLED:
        return {"fares": []}

    # Hard requirement: Aer Lingus is fronted by Cloudflare and needs
    # the curl_cffi Chrome TLS fingerprint to respond. Plain `requests`
    # appears to hang indefinitely on reads (observed at line 459 of
    # scan #N: "curl_cffi call failed (Timeout: ...after 10002ms);
    # falling back to plain requests" followed by an infinite stall).
    # If curl_cffi is unavailable -- either because it's not installed
    # or because it fell back mid-run -- refuse to talk to AL at all.
    # Ryanair can fall back to plain requests without hanging; AL
    # cannot. Log once on the transition.
    if not _CURL_CFFI_AVAILABLE:
        _AER_LINGUS_DISABLED = True
        print(
            "  [warn] aer_lingus: curl_cffi unavailable -- plain requests "
            "hangs on the AL endpoint. Disabling AL for the rest of this run.",
            file=sys.stderr,
        )
        return {"fares": []}

    # Start the wall-clock budget on the first AL call of the run.
    if _AER_LINGUS_START_TIME is None:
        _AER_LINGUS_START_TIME = time.time()

    # Wall-clock budget check: once elapsed, disable AL for the rest
    # of the run. Prevents a CI network-hang from dragging the total
    # scan time past reasonable bounds. Fires silently apart from a
    # one-time "budget exceeded" log line.
    elapsed = time.time() - _AER_LINGUS_START_TIME
    if elapsed > _AER_LINGUS_BUDGET_SECONDS:
        _AER_LINGUS_DISABLED = True
        print(
            f"  [warn] aer_lingus: budget of {_AER_LINGUS_BUDGET_SECONDS:.0f}s "
            f"exhausted ({elapsed:.0f}s elapsed) -- disabling AL for the "
            f"rest of this run.",
            file=sys.stderr,
        )
        return {"fares": []}

    destinations = AER_LINGUS_DESTINATIONS.get(origin, [])
    if not destinations:
        return {"fares": []}

    all_fares: list[dict] = []
    # DD/MM/YYYY is what the API query params want (verified via live
    # browser capture). The referer and the scanner's internal dates
    # both stay as ISO -- the format swap is only for the query.
    dep_date_str = friday.strftime("%d/%m/%Y")
    ret_date_str = sunday.strftime("%d/%m/%Y")

    any_call_succeeded = False
    any_call_attempted = False

    for dest in destinations:
        route_key = (origin, dest)

        # Per-route dead-list: silently skip routes that have failed
        # too many times in a row already this run.
        if route_key in _AER_LINGUS_DEAD_ROUTES:
            continue

        # Mid-loop curl_cffi check: if an earlier call in this loop
        # already tripped the curl_cffi fallback, do NOT try the next
        # destination with plain requests (it will hang). Abort AL.
        if not _CURL_CFFI_AVAILABLE:
            _AER_LINGUS_DISABLED = True
            print(
                "  [warn] aer_lingus: curl_cffi fell back mid-weekend -- "
                "disabling AL for the rest of this run to avoid the "
                "plain-requests hang.",
                file=sys.stderr,
            )
            break

        # Mid-loop budget check: if we've blown the wall-clock budget
        # partway through a weekend, bail out immediately instead of
        # finishing the remaining destinations.
        if time.time() - (_AER_LINGUS_START_TIME or 0) > _AER_LINGUS_BUDGET_SECONDS:
            _AER_LINGUS_DISABLED = True
            print(
                f"  [warn] aer_lingus: budget exhausted mid-weekend -- "
                f"disabling AL for the rest of this run.",
                file=sys.stderr,
            )
            break

        any_call_attempted = True

        params = {
            "origin": f"{origin},{dest}",        # leg1_origin,leg2_origin
            "destination": f"{dest},{origin}",   # leg1_dest,leg2_dest
            "departureDate": dep_date_str,
            "returnDate": ret_date_str,
            "numYouths": "0",
            "numAdults": "1",
            "numChildren": "0",
            "numInfants": "0",
            "fare": "low",
        }
        headers = {
            **AER_LINGUS_HEADERS,
            "Referer": _aer_lingus_referer(origin, dest, friday, sunday),
        }

        failure_reason: str | None = None
        data: dict | None = None
        try:
            # Layer 1: thread watchdog -- hard wall-clock cap on this
            # call regardless of where it hangs. Slightly longer than
            # the call timeout so the per-request timeout fires first
            # on cleanly-timing-out calls and the watchdog only fires
            # if things have gone truly sideways.
            resp = _http_get_with_watchdog(
                AER_LINGUS_URL,
                watchdog_seconds=_AER_LINGUS_CALL_TIMEOUT + 2.0,
                params=params,
                headers=headers,
                timeout=_AER_LINGUS_CALL_TIMEOUT,
                allow_plain_fallback=False,
            )
            status = getattr(resp, "status_code", None)
            if status is None or status >= 400:
                try:
                    body_snippet = (resp.text or "")[:120].replace("\n", " ")
                except Exception:
                    body_snippet = ""
                failure_reason = f"HTTP {status}"
                _maybe_warn_aer_lingus(
                    origin, dest, failure_reason,
                    extra=f"body={body_snippet!r}" if body_snippet else None,
                )
            else:
                data = resp.json()
        except Exception as e:
            failure_reason = f"{type(e).__name__}"
            _maybe_warn_aer_lingus(
                origin, dest, failure_reason, extra=str(e)[:120]
            )

        if failure_reason is not None:
            # Bump the consecutive-failure counter for this route and
            # mark dead if threshold reached.
            n = _AER_LINGUS_ROUTE_FAILURES.get(route_key, 0) + 1
            _AER_LINGUS_ROUTE_FAILURES[route_key] = n
            if n >= _AER_LINGUS_ROUTE_FAIL_THRESHOLD:
                _AER_LINGUS_DEAD_ROUTES.add(route_key)
                # One-time "route retired" note so the log shows when a
                # route was given up on rather than just going silent.
                print(
                    f"  [info] aer_lingus {origin}->{dest}: {n} consecutive "
                    f"failures, skipping this route for the rest of the run.",
                    file=sys.stderr,
                )
            continue

        # Success: reset the failure counter and confirm the working URL.
        _AER_LINGUS_ROUTE_FAILURES.pop(route_key, None)
        any_call_succeeded = True
        if _AER_LINGUS_WORKING_URL is None:
            _AER_LINGUS_WORKING_URL = AER_LINGUS_URL
            print(
                f"  [info] aer_lingus working endpoint confirmed: {AER_LINGUS_URL}",
                file=sys.stderr,
            )

        journey = (data.get("data") or {}).get("journey") or {}
        out_flights = ((journey.get("outbound") or {}).get("flights")) or []
        in_flights = ((journey.get("inbound") or {}).get("flights")) or []

        # Either direction unavailable (SOLD_OUT or empty) -- no
        # round-trip possible for this weekend.
        if not out_flights or not in_flights:
            continue

        out_candidates = [
            (f, _aer_lingus_cheapest_low_fare(f))
            for f in out_flights
            if _aer_lingus_is_ei_operated(f)
        ]
        out_candidates = [(f, p) for f, p in out_candidates if p is not None]

        in_candidates = [
            (f, _aer_lingus_cheapest_low_fare(f))
            for f in in_flights
            if _aer_lingus_is_ei_operated(f)
        ]
        in_candidates = [(f, p) for f, p in in_candidates if p is not None]

        if not out_candidates or not in_candidates:
            continue

        out_candidates.sort(key=lambda x: x[1]["price"])
        in_candidates.sort(key=lambda x: x[1]["price"])

        out_flight, out_fare = out_candidates[0]
        in_flight, in_fare = in_candidates[0]

        all_fares.append({
            "_origin": origin,
            "_destination": dest,
            "_total_price": float(out_fare["price"]) + float(in_fare["price"]),
            "_outbound_flight": out_flight,
            "_inbound_flight": in_flight,
        })

        time.sleep(0.1)  # be polite, aerlingus.com is rate-limited

    # Global kill-switch: if we actually TRIED calls this weekend, none
    # of them succeeded, AND we've never seen a working response all
    # run, the endpoint is cooked. Skip AL for the rest of the scan.
    if (
        any_call_attempted
        and not any_call_succeeded
        and _AER_LINGUS_WORKING_URL is None
    ):
        _AER_LINGUS_DISABLED = True
        print(
            f"  [warn] aer_lingus: every call on first weekend failed -- "
            f"disabling AL for the rest of this run.",
            file=sys.stderr,
        )

    return {"fares": all_fares}


def _aer_lingus_normalise_fare(fare: dict, origin: str) -> dict | None:
    """Turn a synthetic Aer Lingus round-trip fare into our flat deal schema.

    The input `fare` is NOT a raw Aer Lingus API item -- it's a
    dict built by _aer_lingus_fetch_fares that pairs the cheapest
    outbound and inbound single-segment flights for a given weekend:

        {
          "_origin":            "DUB",
          "_destination":       "LHR",
          "_total_price":       292.80,   # out_low + in_low, EUR
          "_outbound_flight":   { ... raw AL flight dict with trips[] ... },
          "_inbound_flight":    { ... raw AL flight dict with trips[] ... },
        }

    We pull the trip info (departure / arrival timestamps, flight
    numbers, airport codes) out of trips[0].departure / arrival /
    info on each side, then hand off to _apply_common_filters for
    the usual evening-window + IATA-coord checks.
    """
    try:
        origin_from_fare = fare.get("_origin") or origin
        dest_iata = fare.get("_destination", "")
        total_price = fare.get("_total_price")
        outbound_flight = fare.get("_outbound_flight") or {}
        inbound_flight = fare.get("_inbound_flight") or {}

        if not dest_iata or not isinstance(total_price, (int, float)):
            _reject_counts["missing_fields"] += 1
            return None

        flight_price = float(total_price)
        bus = 0.0 if origin_from_fare == "SNN" else BUS_RETURN_COST_EUR
        effective = flight_price + bus

        # Aer Lingus puts the actual flight details under trips[0].
        out_trip = ((outbound_flight.get("trips") or [{}])[0]) or {}
        in_trip = ((inbound_flight.get("trips") or [{}])[0]) or {}

        # Dates come back as "2026-04-12T08:50:00.000" -- strip the
        # milliseconds so the HH:MM slice in _apply_common_filters
        # still lines up at [11:16].
        def _trim_iso(s: str) -> str:
            return s[:19] if s and len(s) >= 19 else s

        out_dep = _trim_iso((out_trip.get("departure") or {}).get("date") or "")
        out_arr = _trim_iso((out_trip.get("arrival") or {}).get("date") or "")
        in_dep = _trim_iso((in_trip.get("departure") or {}).get("date") or "")
        in_arr = _trim_iso((in_trip.get("arrival") or {}).get("date") or "")

        common = _apply_common_filters(
            origin_from_fare, dest_iata, flight_price, out_dep, in_dep
        )
        if common is None:
            return None
        lat, lon, _out_hhmm, _in_hhmm = common

        out_info = out_trip.get("info") or {}
        in_info = in_trip.get("info") or {}
        # out_info.code is like "EI 153"; strip the space for a
        # compact flight number. Fall back to number if code missing.
        out_flight_num = (
            (out_info.get("code") or "").replace(" ", "")
            or f"EI{out_info.get('number', '')}"
        )
        in_flight_num = (
            (in_info.get("code") or "").replace(" ", "")
            or f"EI{in_info.get('number', '')}"
        )

        out_date = out_dep[:10]
        in_date = in_dep[:10]

        return {
            "origin": origin_from_fare,
            "carrier_code": "EI",
            "carrier_name": "Aer Lingus",
            "destination_iata": dest_iata,
            "destination_city": dest_iata,  # AL doesn't give city name; IATA as fallback
            "destination_country": "",
            "destination_lat": lat,
            "destination_lon": lon,
            "flight_price_eur": round(flight_price, 2),
            "bus_surcharge_eur": round(bus, 2),
            "effective_price_eur": round(effective, 2),
            "currency": "EUR",
            "outbound_departure": out_dep,
            "outbound_arrival": out_arr,
            "outbound_flight_number": out_flight_num,
            "inbound_departure": in_dep,
            "inbound_arrival": in_arr,
            "inbound_flight_number": in_flight_num,
            "google_flights_url": google_flights_url(
                origin_from_fare, dest_iata, out_date, in_date
            ),
            "skyscanner_url": skyscanner_url(
                origin_from_fare, dest_iata, out_date, in_date
            ),
        }
    except Exception as e:
        _reject_counts["missing_fields"] += 1
        print(f"  [warn] aer_lingus normalise failed: {e}", file=sys.stderr)
        return None


# ---------- Source registry ----------
# Each entry drives one pass through the main scan loop. `fetch`
# takes (origin, friday, sunday) and returns a dict with a `fares`
# list; `normalise` turns each fare into our common schema or returns
# None. Add a new entry here to plug in another airline.
SOURCES = [
    {
        "name": "ryanair",
        "label": "Ryanair (FR)",
        "fetch": _ryanair_fetch_fares,
        "normalise": _ryanair_normalise_fare,
        # None = all configured windows and weekends.
        "windows": None,
        "max_weekends": None,
    },
    {
        "name": "aer_lingus",
        "label": "Aer Lingus (EI)",
        "fetch": _aer_lingus_fetch_fares,
        "normalise": _aer_lingus_normalise_fare,
        # AL is expensive per-call (one HTTP call per destination) and
        # the fares are typically well above PRICE_CAP anyway, so we
        # trim the scan scope aggressively: classic Fri->Sun only, and
        # only the next 3 weekends. Total call count becomes ~9 dests
        # x 3 weekends = ~27 calls, bounding worst-case AL time at
        # ~27 x 8s = ~216s even if every single call has to fire the
        # watchdog. 3 weekends is enough to catch "this coming weekend
        # + the two after" which is the practical near-term horizon
        # for a last-minute getaway.
        "windows": ["fri_sun"],
        "max_weekends": 3,
    },
]


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
# Top-level scan wall-clock cap. If the scan hasn't finished in this
# many seconds, we raise TimeoutError from a SIGALRM handler and let
# the outer main() catch it and write whatever partial results we
# have. 10 minutes is deliberately generous -- a healthy scan lands
# at ~3-4 minutes; this exists purely so a catastrophic hang (e.g.
# a hung libcurl read that bypasses every other layer) can't take
# the scan past 10.
SCAN_WALL_CLOCK_SECONDS = 600


class _ScanTimeoutError(BaseException):
    """Raised by the SIGALRM handler when SCAN_WALL_CLOCK_SECONDS elapses.

    Intentionally extends BaseException (NOT Exception) so that the
    enrichment / history / notifier try/except Exception blocks in
    _run_impl() cannot silently swallow it. Only the outermost
    main() catch-all (which uses `except BaseException`) is allowed
    to catch this and fall back to prospects mode.

    If this inherited from Exception (via TimeoutError -> OSError ->
    Exception, the stdlib default), the first `except Exception:`
    block after the alarm fires would eat the timeout and the scan
    would keep running forever. That's exactly the bug we hit in
    build-2026-04-10.6 where the run sat past the 10-minute cap.
    """


def _install_scan_watchdog() -> bool:
    """Install a SIGALRM handler that raises _ScanTimeoutError after
    SCAN_WALL_CLOCK_SECONDS. Returns True if installed, False on
    platforms without SIGALRM (Windows). Idempotent."""
    if not hasattr(signal, "SIGALRM"):
        return False

    def _handler(signum: int, frame) -> None:  # noqa: ARG001
        raise _ScanTimeoutError(
            f"top-level scan watchdog: {SCAN_WALL_CLOCK_SECONDS}s exceeded"
        )

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(SCAN_WALL_CLOCK_SECONDS)
    return True


def _clear_scan_watchdog() -> None:
    if hasattr(signal, "SIGALRM"):
        signal.alarm(0)


# Build identifier -- printed prominently on every run so CI logs
# self-identify which version of the scanner is running. Bump this
# string whenever a meaningful behavioural change lands. If a log
# shows behaviour that doesn't match this ID's claimed features,
# the runner is executing stale code. Look for this exact string
# in the log to know which build is live.
SCANNER_BUILD_ID = "build-2026-04-10.8 (enrich budget=90s, AL=3w, SIGALRM=600s)"


def _run() -> int:
    print(
        "============================================================\n"
        f"  SCANNER {SCANNER_BUILD_ID}\n"
        "============================================================",
        file=sys.stderr,
    )

    if "--test-notification" in sys.argv[1:]:
        return send_test_notification()

    if FORCE_PROSPECTS:
        return write_prospects_mode("SCANNER_PROSPECTS_ONLY set")

    watchdog_installed = _install_scan_watchdog()
    if watchdog_installed:
        print(
            f"  [info] top-level scan watchdog armed: "
            f"{SCAN_WALL_CLOCK_SECONDS}s hard cap",
            file=sys.stderr,
        )

    try:
        return _run_impl()
    finally:
        _clear_scan_watchdog()


def _run_impl() -> int:
    all_deals: list[dict] = []
    weekends = list(next_weekends(WEEKENDS_AHEAD))
    http_client = (
        f"curl_cffi v{_CURL_CFFI_VERSION} impersonate={CURL_IMPERSONATE}"
        if _CURL_CFFI_AVAILABLE
        else f"plain python-requests (curl_cffi: {_CURL_CFFI_VERSION}, likely to 403)"
    )
    print(
        f"Scanning {len(weekends)} weekends from {weekends[0][0]} "
        f"to {weekends[-1][1]} for fares <= EUR {PRICE_CAP_EUR} "
        f"(sources: {', '.join(s['label'] for s in SOURCES)}, HTTP client: {http_client})..."
    )

    per_source_summary: dict[str, dict] = {}
    sample_dumped: dict[str, bool] = {s["name"]: False for s in SOURCES}

    # Base window-weekend plan used by any source that doesn't
    # override it. Trimmed per-source below if SOURCES entry
    # declares `windows` or `max_weekends`.
    base_window_specs = list(next_weekend_windows(len(weekends)))
    print(
        f"Base weekend windows: "
        + ", ".join(w[1] for w in WEEKEND_WINDOWS)
        + f"  ({len(base_window_specs)} total window-weekends)"
    )

    for source in SOURCES:
        source_name = source["name"]
        source_label = source["label"]
        fetch = source["fetch"]
        normalise = source["normalise"]

        # Per-source trimming: restrict to specific windows and/or
        # a shorter scanning horizon to keep expensive sources (like
        # Aer Lingus with its one-call-per-destination shape) from
        # blowing up scan runtime.
        source_windows_filter = source.get("windows")
        source_max_weekends = source.get("max_weekends") or len(weekends)
        source_window_spec_list = [
            WEEKEND_WINDOWS[i]
            for i, w in enumerate(WEEKEND_WINDOWS)
            if source_windows_filter is None or w[0] in source_windows_filter
        ]
        if not source_window_spec_list:
            source_window_spec_list = WEEKEND_WINDOWS  # fail-safe
        window_specs = list(
            next_weekend_windows(source_max_weekends, windows=source_window_spec_list)
        )
        if window_specs != base_window_specs:
            print(
                f"  ({source_name} scans {len(window_specs)} window-weekends: "
                f"{', '.join(w[1] for w in source_window_spec_list)} x {source_max_weekends} weekends)"
            )

        source_calls = 0
        source_failed = 0
        source_deals_added = 0
        source_error_summary: dict[str, int] = {}

        print(f"\n--- {source_label} ---")

        for origin in ORIGINS:
            for window_id, window_label, out_date, in_date in window_specs:
                source_calls += 1
                label = f"{source_name} {origin} {window_id} {out_date}->{in_date}"
                try:
                    data = fetch(origin, out_date, in_date)
                except requests.HTTPError as e:
                    source_failed += 1
                    code = e.response.status_code if e.response is not None else "?"
                    err_key = f"HTTP {code}"
                    source_error_summary[err_key] = source_error_summary.get(err_key, 0) + 1
                    if source_error_summary[err_key] == 1 and e.response is not None:
                        snippet = e.response.text[:200].replace("\n", " ")
                        print(
                            f"  [warn] {label}: HTTP {code}  body: {snippet!r}",
                            file=sys.stderr,
                        )
                    else:
                        print(f"  [warn] {label}: HTTP {code}", file=sys.stderr)
                    continue
                except requests.RequestException as e:
                    source_failed += 1
                    err_key = type(e).__name__
                    source_error_summary[err_key] = source_error_summary.get(err_key, 0) + 1
                    print(f"  [warn] {label}: {err_key}: {e}", file=sys.stderr)
                    continue
                except Exception as e:
                    # Catch-all for unexpected source-level failures so
                    # one carrier breaking can't stop the others.
                    source_failed += 1
                    err_key = type(e).__name__
                    source_error_summary[err_key] = source_error_summary.get(err_key, 0) + 1
                    print(f"  [warn] {label}: {err_key}: {e}", file=sys.stderr)
                    continue

                fares = data.get("fares") or []
                if not sample_dumped[source_name] and fares:
                    sample_dumped[source_name] = True
                    try:
                        dump = json.dumps(fares[0], indent=2, default=str)
                        if len(dump) > 3000:
                            dump = dump[:3000] + "\n  ...(truncated)"
                        print(
                            f"\n--- sample raw fare from {source_name} / {origin} {out_date}->{in_date} ---\n"
                            f"{dump}\n--- end sample ---\n",
                            file=sys.stderr,
                        )
                    except Exception as e:
                        print(f"  [debug] failed to dump sample fare: {e}", file=sys.stderr)

                kept = 0
                parsed = 0
                for fare in fares:
                    deal = normalise(fare, origin)
                    if deal is None:
                        continue
                    parsed += 1
                    # Tag every deal with the window it came from so the
                    # dashboard filter chip can distinguish Fri->Sun from
                    # Fri->Mon etc. Cheap to add since the loop already
                    # knows the current window.
                    deal["weekend_window"] = window_id
                    deal["weekend_window_label"] = window_label
                    if deal["flight_price_eur"] <= PRICE_CAP_EUR:
                        all_deals.append(deal)
                        kept += 1
                        source_deals_added += 1
                print(f"  {label}: {len(fares)} fares, {parsed} parsed, {kept} under cap")
                time.sleep(0.25)

        per_source_summary[source_name] = {
            "label": source_label,
            "calls": source_calls,
            "failed": source_failed,
            "deals": source_deals_added,
            "errors": source_error_summary,
        }

    print("\n=== Scan summary ===")
    total_calls = 0
    total_failed = 0
    for name, summary in per_source_summary.items():
        total_calls += summary["calls"]
        total_failed += summary["failed"]
        print(
            f"  {summary['label']}: {summary['calls']} calls, "
            f"{summary['calls'] - summary['failed']} ok, "
            f"{summary['failed']} failed, {summary['deals']} deals under cap."
        )
        if summary["errors"]:
            print(
                f"    error breakdown: "
                + ", ".join(f"{k}={v}" for k, v in sorted(summary["errors"].items()))
            )
    print(f"  TOTAL: {total_calls} calls, {len(all_deals)} raw deals under cap.")
    rejects = [(k, v) for k, v in _reject_counts.items() if v > 0]
    if rejects:
        print(
            "Parse rejections: "
            + ", ".join(f"{k}={v}" for k, v in sorted(rejects))
        )

    # If EVERY source failed every call, fall back to prospects mode so
    # the dashboard still has something to render.
    if total_calls > 0 and total_failed == total_calls:
        print(
            f"All {total_calls} upstream calls failed -- falling back to prospects mode.",
            file=sys.stderr,
        )
        return write_prospects_mode("All sources unreachable")

    # Dedupe on (carrier, origin, destination, weekend_window, outbound date).
    # Keeping the weekend window in the key means a Fri->Sun Berlin and a
    # Fri->Mon Berlin are separate deals (different trip lengths); multiple
    # flights on the same day/window/carrier collapse to the cheapest.
    dedup: dict[tuple[str, str, str, str, str], dict] = {}
    for d in all_deals:
        key = (
            d.get("carrier_code", "?"),
            d["origin"],
            d["destination_iata"],
            d.get("weekend_window", "fri_sun"),
            d["outbound_departure"][:10],
        )
        if key not in dedup or d["flight_price_eur"] < dedup[key]["flight_price_eur"]:
            dedup[key] = d

    deals = sorted(dedup.values(), key=lambda x: x["flight_price_eur"])

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "price_cap_eur": PRICE_CAP_EUR,
        "bus_return_cost_eur": BUS_RETURN_COST_EUR,
        "origins": ORIGINS,
        "weekends_scanned": len(weekends),
        "mode": "live",
        "sources": [s["name"] for s in SOURCES],
        "source": "multi",  # legacy compatibility
        "weekend_windows": [
            {"id": wid, "label": wlabel}
            for wid, wlabel, *_ in WEEKEND_WINDOWS
        ],
        "deals": deals,
    }

    # Annotate each deal with price-history fields (lowest_ever_eur,
    # price_delta_eur, is_lowest_ever) BEFORE we notify or write, so
    # the notifier can see is_lowest_ever and the dashboard can
    # render the "cheapest ever" badge.
    try:
        from history import update_history_file
        update_history_file(HISTORY_PATH, deals)
        lowest_ever_count = sum(1 for d in deals if d.get("is_lowest_ever"))
        price_drops = [
            d for d in deals
            if d.get("price_delta_eur") is not None and d["price_delta_eur"] < 0
        ]
        print(
            f"Price history: {lowest_ever_count} at-or-below lowest ever, "
            f"{len(price_drops)} price drops since last scan."
        )
    except Exception as e:
        print(f"  [history] error: {e}", file=sys.stderr)

    # Destination enrichments: Wikipedia thumbnails + open-meteo weather
    # forecast. Both are best-effort: any network failure logs a warning
    # and falls through with the deal unannotated. Photos are cached
    # permanently; weather has a 12h TTL.
    #
    # Historically these called _http_get directly, which meant:
    #   (a) a curl_cffi failure on a Wikipedia/open-meteo call would
    #       flip _CURL_CFFI_AVAILABLE=False and fall through to plain
    #       requests, which could then hang indefinitely on the next
    #       call (same failure mode as Aer Lingus);
    #   (b) no wall-clock cap on any single enrichment call.
    # Root cause of the line-460 scan hang: an enrichment call, not an
    # AL call, hung in plain-requests after curl_cffi fell back. The
    # DUB->LGW warnings in the log were a red herring -- they were just
    # the last loud event before the silent enrichment phase stalled.
    #
    # Fix: route enrichment calls through the same watchdog wrapper AL
    # uses, with allow_plain_fallback=False. Each enrichment call now
    # has a hard 8s wall-clock cap and cannot trigger the plain-requests
    # fallback. Failures are caught by the enrichment modules' own
    # try/except blocks and fall through with the deal unannotated.
    # Destination enrichments: Wikipedia thumbnails + open-meteo weather
    # forecast. Both are best-effort: any network failure logs a warning
    # and falls through with the deal unannotated. Photos are cached
    # permanently; weather has a 12h TTL.
    #
    # These are routed through the watchdog wrapper with a HARD TOTAL
    # BUDGET of ENRICHMENT_BUDGET_SECONDS. Rationale:
    #   * Photos only need ~30 HTTP calls (one per unique destination).
    #     At ~0.5s per call, photos finish in ~15s even cold-cache.
    #   * Weather caches by (iata, out_date, in_date) and deduplicates
    #     at that level. With 4 windows x 26 weekends x ~30 unique
    #     destinations = ~3120 unique tuples, a cold-cache weather
    #     enrichment would need ~26 minutes even with healthy plain
    #     requests. That's unacceptable given our 10-minute top-level
    #     watchdog.
    #   * So we cap the TOTAL enrichment phase at 90 seconds. Once the
    #     budget is blown, every subsequent _enrichment_http_get call
    #     raises immediately, each enrichment module's own try/except
    #     catches it, and the deal falls through unannotated. Deals
    #     already enriched keep their data.
    #
    # allow_plain_fallback=True for enrichment (unlike Aer Lingus):
    # Wikipedia and open-meteo both work fine with plain requests.
    # The previous hang I diagnosed was only on Aer Lingus's Cloudflare
    # layer; enrichment plain-requests calls are safe and fast.
    ENRICHMENT_BUDGET_SECONDS = 90.0
    enrichment_start = time.time()
    enrichment_skipped = {"count": 0}

    def _enrichment_http_get(url, **kwargs):
        elapsed = time.time() - enrichment_start
        if elapsed > ENRICHMENT_BUDGET_SECONDS:
            enrichment_skipped["count"] += 1
            raise TimeoutError(
                f"enrichment budget {ENRICHMENT_BUDGET_SECONDS:.0f}s exceeded "
                f"({elapsed:.0f}s elapsed); skipping this call"
            )
        return _http_get_with_watchdog(
            url,
            watchdog_seconds=8.0,
            # Enrichment endpoints (Wikipedia, open-meteo) work fine
            # with plain requests, so we DO allow the fallback here
            # (unlike Aer Lingus which must keep curl_cffi).
            **kwargs,
        )

    print(
        f"  [enrich] starting photo + weather enrichment "
        f"(curl_cffi_available={_CURL_CFFI_AVAILABLE}, {len(deals)} deals, "
        f"budget={ENRICHMENT_BUDGET_SECONDS:.0f}s)...",
        file=sys.stderr,
    )
    try:
        from enrichments import enrich_photos, enrich_weather
        enrich_photos(deals, PHOTO_CACHE_PATH, _enrichment_http_get)
        print(
            f"  [enrich] photos done at "
            f"+{time.time() - enrichment_start:.1f}s",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"  [photos] error: {e}", file=sys.stderr)
    try:
        from enrichments import enrich_weather
        enrich_weather(deals, WEATHER_CACHE_PATH, _enrichment_http_get)
        print(
            f"  [enrich] weather done at "
            f"+{time.time() - enrichment_start:.1f}s",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"  [weather] error: {e}", file=sys.stderr)
    enrichment_total = time.time() - enrichment_start
    if enrichment_skipped["count"] > 0:
        print(
            f"  [enrich] total time: {enrichment_total:.1f}s "
            f"({enrichment_skipped['count']} calls skipped due to budget)",
            file=sys.stderr,
        )
    else:
        print(
            f"  [enrich] total time: {enrichment_total:.1f}s "
            f"(all calls completed within budget)",
            file=sys.stderr,
        )

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


class _Tee:
    """Fan-out write() to several underlying streams.

    Lets us capture everything the scanner prints into an in-memory
    buffer AND still echo it to the real stdout/stderr for the GitHub
    Actions log. Duck-typed against `sys.stdout`; doesn't need the full
    TextIOWrapper interface.
    """

    def __init__(self, *streams) -> None:
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
            except Exception:
                pass
        return len(data)

    def flush(self) -> None:
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False


def main() -> int:
    """Crash-proof entry point that also captures output to a log file.

    Tees stdout/stderr into an in-memory buffer so the scanner's full
    output lands in dashboard/last_scan_log.txt next to deals.json.
    The commit step in the workflow adds that file to the auto-commit,
    which means every run leaves a readable breadcrumb on the branch
    -- no GitHub Actions log scraping required to debug.

    Top-level try/except catches any unhandled exception, dumps the
    traceback into the same log file, and falls back to prospects
    mode so the job still succeeds and the dashboard still updates.
    """
    from io import StringIO

    log_buffer = StringIO()
    real_stdout = sys.stdout
    real_stderr = sys.stderr
    sys.stdout = _Tee(real_stdout, log_buffer)  # type: ignore[assignment]
    sys.stderr = _Tee(real_stderr, log_buffer)  # type: ignore[assignment]

    rc = 1
    try:
        rc = _run()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 - last-ditch net by design
        import traceback
        print(f"\n::error::scanner crashed with {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print(
            "\nFalling back to prospects mode so deals.json still gets "
            "written and the workflow can continue.",
            file=sys.stderr,
        )
        try:
            rc = write_prospects_mode(f"crashed: {type(e).__name__}")
        except Exception as inner:
            print(
                f"Prospects-mode fallback ALSO crashed ({inner}); giving up.",
                file=sys.stderr,
            )
            rc = 1
    finally:
        sys.stdout = real_stdout
        sys.stderr = real_stderr
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            header = (
                f"# last_scan_log.txt\n"
                f"# Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}\n"
                f"# Scanner exit code: {rc}\n"
                f"# Python: {sys.version.split()[0]}\n"
                f"# curl_cffi: {_CURL_CFFI_VERSION} "
                f"(available={_CURL_CFFI_AVAILABLE})\n"
                f"# CURL_IMPERSONATE: {CURL_IMPERSONATE}\n"
                f"# ------------------------------------------------------------\n"
            )
            LOG_PATH.write_text(header + log_buffer.getvalue())
            print(f"Wrote scan log to {LOG_PATH}")
        except Exception as e:
            print(f"Failed to write scan log: {e}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
