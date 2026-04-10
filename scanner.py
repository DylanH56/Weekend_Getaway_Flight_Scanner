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
WEEKENDS_AHEAD = 16       # Scan ~4 months of upcoming weekends.

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


# ---------- Date helpers ----------
def next_weekends(n: int) -> Iterator[tuple[dt.date, dt.date]]:
    """Yield (friday, sunday) pairs for the next `n` weekends."""
    today = dt.date.today()
    days_to_friday = (4 - today.weekday()) % 7  # 4 == Friday
    # If it's already Friday afternoon, skip to next Friday.
    if days_to_friday == 0 and dt.datetime.now().hour >= 15:
        days_to_friday = 7
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


# ---------- Main ----------
def main() -> int:
    if not API_KEY:
        print(
            "ERROR: KIWI_API_KEY environment variable is not set.\n"
            "\n"
            "Get a free Tequila API key at https://tequila.kiwi.com, then:\n"
            "    export KIWI_API_KEY='your-key-here'\n"
            "    python scanner.py\n",
            file=sys.stderr,
        )
        return 1

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
        "source": "kiwi-tequila",
        "deals": deals,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {len(deals)} deals to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
