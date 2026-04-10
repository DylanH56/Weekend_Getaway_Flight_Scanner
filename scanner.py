#!/usr/bin/env python3
"""Weekend Getaway Flight Scanner.

Scans Ryanair's public fare-finder API for cheap round-trip weekend flights
(Fri evening out, Sun evening back) from Shannon (preferred) and Dublin
into Europe, capped at EUR 100. Dublin fares are adjusted upward by the
cost of a return Limerick<->Dublin bus so the two origins can be compared
on an "effective price from Limerick" basis.

Writes the results to dashboard/deals.json for the front-end to render.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Iterator

import requests

# ---------- Config ----------
PRICE_CAP_EUR = 100.0
# Approx Limerick <-> Dublin return via Bus Eireann / Citylink / Dublin Coach.
BUS_RETURN_COST_EUR = 30.0
ORIGINS = ["SNN", "DUB"]  # Shannon first, Dublin as fallback.
WEEKENDS_AHEAD = 16       # Scan ~4 months of upcoming weekends.

# Friday evening departures and Sunday afternoon/evening returns.
OUTBOUND_FROM = "16:00"
OUTBOUND_TO = "23:59"
INBOUND_FROM = "15:00"
INBOUND_TO = "23:59"

RYANAIR_URL = "https://services-api.ryanair.com/farfnd/v4/roundTripFares"

OUTPUT_PATH = Path(__file__).parent / "dashboard" / "deals.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IE,en;q=0.9",
    "Origin": "https://www.ryanair.com",
    "Referer": "https://www.ryanair.com/",
}


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


# ---------- Ryanair fetch ----------
def fetch_fares(origin: str, friday: dt.date, sunday: dt.date) -> dict:
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
        # Ask for slightly more than our cap so Dublin fares that would fit
        # after subtracting the bus surcharge still come through.
        "priceValueTo": str(int(PRICE_CAP_EUR + BUS_RETURN_COST_EUR)),
        "currency": "EUR",
        "market": "en-ie",
        "adultPaxCount": "1",
        "limit": "50",
        "offset": "0",
    }
    resp = requests.get(RYANAIR_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------- Normalisation ----------
def _city_name(airport: dict) -> str:
    city = airport.get("city")
    if isinstance(city, dict):
        return city.get("name") or airport.get("name", "")
    return airport.get("cityName") or airport.get("name", "")


def normalise_fare(fare: dict, origin: str) -> dict | None:
    try:
        outbound = fare["outbound"]
        inbound = fare["inbound"]
        arr = outbound["arrivalAirport"]
        summary = fare["summary"]["price"]
    except KeyError:
        return None

    flight_price = float(summary["value"])
    bus_surcharge = 0.0 if origin == "SNN" else BUS_RETURN_COST_EUR
    effective_price = flight_price + bus_surcharge

    coords = arr.get("coordinates") or {}
    try:
        lat = float(coords["latitude"])
        lon = float(coords["longitude"])
    except (KeyError, TypeError, ValueError):
        return None

    out_date = outbound["departureDate"][:10]
    in_date = inbound["departureDate"][:10]
    dest_iata = arr["iataCode"]

    return {
        "origin": origin,
        "destination_iata": dest_iata,
        "destination_city": _city_name(arr),
        "destination_country": arr.get("countryName", ""),
        "destination_lat": lat,
        "destination_lon": lon,
        "flight_price_eur": round(flight_price, 2),
        "bus_surcharge_eur": round(bus_surcharge, 2),
        "effective_price_eur": round(effective_price, 2),
        "currency": summary.get("currencyCode", "EUR"),
        "outbound_departure": outbound["departureDate"],
        "outbound_arrival": outbound["arrivalDate"],
        "outbound_flight_number": outbound.get("flightNumber", ""),
        "inbound_departure": inbound["departureDate"],
        "inbound_arrival": inbound["arrivalDate"],
        "inbound_flight_number": inbound.get("flightNumber", ""),
        "booking_url": build_booking_url(origin, dest_iata, out_date, in_date),
    }


def build_booking_url(origin: str, dest: str, out_date: str, in_date: str) -> str:
    return (
        "https://www.ryanair.com/ie/en/trip/flights/select"
        f"?adults=1&teens=0&children=0&infants=0"
        f"&dateOut={out_date}&dateIn={in_date}"
        f"&originIata={origin}&destinationIata={dest}"
        f"&isReturn=true&discount=0&promoCode="
        f"&tpAdults=1&tpTeens=0&tpChildren=0&tpInfants=0"
        f"&tpStartDate={out_date}&tpEndDate={in_date}&tpDiscount=0"
        f"&tpOriginIata={origin}&tpDestinationIata={dest}"
    )


# ---------- Main ----------
def main() -> int:
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
        "deals": deals,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {len(deals)} deals to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
