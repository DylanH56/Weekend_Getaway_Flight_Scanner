"""Destination enrichment: Wikipedia thumbnails + open-meteo weather.

Both enrichments are cached on disk so the scan doesn't hit the
network for every deal on every run. The cache files live next to
deals.json and are committed by the CI workflow, so the cached data
travels with the branch and the dashboard doesn't need to re-fetch on
load.

Photos (dashboard/city_photos.json):
  Keyed by destination_iata. Once we've fetched a Wikipedia summary
  for Birmingham, the URL is stable and we never refetch. Old entries
  are kept forever.

Weather (dashboard/weather.json):
  Keyed by "iata|YYYY-MM-DD" where the date is the outbound date.
  Entries are rebuilt from scratch on every scan because the
  forecast changes daily. To stay under open-meteo's (very generous)
  rate limit we dedup queries by (lat, lon) across deals -- multiple
  deals to the same weekend destination share one API call.

Both enrichments are wrapped in broad try/except so a network hiccup
never tanks the scan. If a lookup fails the deal just gets no photo /
no weather, and the dashboard renders without that element.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Callable
from urllib.parse import quote_plus


# ---------- Wikipedia photos ----------
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIPEDIA_HEADERS = {
    "User-Agent": "WeekendGetawayFlightScanner/1.0 (+https://github.com/DylanH56/Weekend_Getaway_Flight_Scanner)",
    "Accept": "application/json",
}


def load_photo_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_photo_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=0, separators=(",", ":"), sort_keys=True))


def _wikipedia_thumbnail(
    city_name: str,
    country_name: str,
    http_get: Callable,
) -> dict | None:
    """Ask Wikipedia for the summary of a city and extract the thumbnail.

    Tries "City, Country" first to disambiguate (e.g. "Paris, France"
    picks the French capital, not the Paris in Texas). Falls back to
    just the city name if that 404s.
    """
    if not city_name:
        return None
    candidates = []
    if country_name:
        candidates.append(f"{city_name}, {country_name}")
    candidates.append(city_name)
    for title in candidates:
        url = WIKIPEDIA_SUMMARY_URL + quote_plus(title.replace(" ", "_"))
        try:
            resp = http_get(url, headers=WIKIPEDIA_HEADERS, timeout=10)
        except Exception:
            continue
        status = getattr(resp, "status_code", None)
        if status != 200:
            continue
        try:
            data = resp.json()
        except Exception:
            continue
        thumb = data.get("thumbnail") or {}
        original = data.get("originalimage") or {}
        thumb_url = thumb.get("source") or original.get("source")
        if not thumb_url:
            continue
        return {
            "url": thumb_url,
            "width": thumb.get("width") or original.get("width"),
            "height": thumb.get("height") or original.get("height"),
            "attribution": "Wikipedia",
            "page_url": (data.get("content_urls") or {}).get("desktop", {}).get("page"),
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    return None


def enrich_photos(
    deals: list[dict],
    cache_path: Path,
    http_get: Callable,
) -> None:
    """Populate deal['photo_*'] fields in place, using a persistent cache.

    Only the first deal per (iata, city) triggers a Wikipedia call;
    every subsequent deal for the same destination reuses the cached
    thumbnail. Cache is saved back to disk when done.
    """
    cache = load_photo_cache(cache_path)
    fetched_count = 0
    for deal in deals:
        iata = deal.get("destination_iata", "")
        if not iata:
            continue
        cached = cache.get(iata)
        if cached is None:
            # Fetch and cache (even a null result, so we don't keep
            # re-trying Wikipedia for airports that have no page).
            city = deal.get("destination_city") or iata
            country = deal.get("destination_country") or ""
            cached = _wikipedia_thumbnail(city, country, http_get)
            cache[iata] = cached or {"_no_photo": True}
            if cached:
                fetched_count += 1
        # Only attach fields if we actually have a real entry.
        if cached and not cached.get("_no_photo"):
            deal["photo_url"] = cached.get("url")
            deal["photo_attribution"] = cached.get("attribution")
            deal["photo_page_url"] = cached.get("page_url")
    save_photo_cache(cache_path, cache)
    if fetched_count:
        print(f"Photos: fetched {fetched_count} new Wikipedia thumbnails.")


# ---------- open-meteo weather ----------
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def load_weather_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_weather_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=0, separators=(",", ":"), sort_keys=True))


# WMO weather code -> (emoji, short text). open-meteo returns a numeric
# `weathercode` per day that maps to this table.
WMO_CODE_MAP: dict[int, tuple[str, str]] = {
    0: ("\u2600\ufe0f", "Clear"),
    1: ("\U0001F324\ufe0f", "Mainly clear"),
    2: ("\u26C5", "Partly cloudy"),
    3: ("\u2601\ufe0f", "Overcast"),
    45: ("\U0001F32B\ufe0f", "Fog"),
    48: ("\U0001F32B\ufe0f", "Rime fog"),
    51: ("\U0001F327\ufe0f", "Light drizzle"),
    53: ("\U0001F327\ufe0f", "Drizzle"),
    55: ("\U0001F327\ufe0f", "Dense drizzle"),
    61: ("\U0001F327\ufe0f", "Light rain"),
    63: ("\U0001F327\ufe0f", "Rain"),
    65: ("\U0001F327\ufe0f", "Heavy rain"),
    66: ("\U0001F327\ufe0f", "Freezing rain"),
    67: ("\U0001F327\ufe0f", "Freezing rain"),
    71: ("\U0001F328\ufe0f", "Light snow"),
    73: ("\U0001F328\ufe0f", "Snow"),
    75: ("\U0001F328\ufe0f", "Heavy snow"),
    77: ("\U0001F328\ufe0f", "Snow grains"),
    80: ("\U0001F326\ufe0f", "Showers"),
    81: ("\U0001F326\ufe0f", "Showers"),
    82: ("\u26C8\ufe0f", "Violent showers"),
    85: ("\U0001F328\ufe0f", "Snow showers"),
    86: ("\U0001F328\ufe0f", "Snow showers"),
    95: ("\u26C8\ufe0f", "Thunderstorm"),
    96: ("\u26C8\ufe0f", "Thunderstorm, hail"),
    99: ("\u26C8\ufe0f", "Thunderstorm, hail"),
}


def _fetch_weather(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
    http_get: Callable,
) -> dict | None:
    """Hit open-meteo for a daily forecast over (start_date..end_date).

    Returns a dict with `weathercode`, `high_c`, `low_c` or None on
    any error. Picks the worst weather code across the weekend
    (thunder > rain > overcast > clear) so a sunny Saturday sandwiched
    between two rainy days doesn't misrepresent the trip.
    """
    try:
        resp = http_get(
            OPEN_METEO_URL,
            params={
                "latitude": str(lat),
                "longitude": str(lon),
                "start_date": start_date,
                "end_date": end_date,
                "daily": "weathercode,temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
            },
            timeout=10,
        )
        if getattr(resp, "status_code", None) != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    daily = data.get("daily") or {}
    codes = daily.get("weathercode") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    if not codes or not highs or not lows:
        return None

    # Severity ranking for picking the "representative" day's code.
    severity = {
        0: 0, 1: 1, 2: 2, 3: 3,
        45: 4, 48: 4,
        51: 5, 53: 5, 55: 5,
        61: 6, 63: 7, 65: 8,
        66: 8, 67: 8,
        71: 7, 73: 8, 75: 9, 77: 7,
        80: 6, 81: 7, 82: 9,
        85: 7, 86: 8,
        95: 10, 96: 10, 99: 10,
    }
    worst = max(codes, key=lambda c: severity.get(int(c), 5))
    emoji, text = WMO_CODE_MAP.get(int(worst), ("\U0001F321\ufe0f", "Mixed"))
    return {
        "code": int(worst),
        "emoji": emoji,
        "text": text,
        "high_c": round(max(highs), 1),
        "low_c": round(min(lows), 1),
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def enrich_weather(
    deals: list[dict],
    cache_path: Path,
    http_get: Callable,
    cache_ttl_hours: int = 12,
) -> None:
    """Populate deal['weather_*'] fields in place, with a short-lived cache.

    Unlike photos, weather forecasts go stale quickly (the forecast
    for next Friday changes every few hours). We cache by
    "iata|outbound_date" but invalidate anything older than
    cache_ttl_hours.
    """
    cache = load_weather_cache(cache_path)
    now = dt.datetime.now(dt.timezone.utc)
    ttl = dt.timedelta(hours=cache_ttl_hours)
    fetched_count = 0

    # Dedup queries: group deals by (iata, out_date, in_date) so we
    # only call open-meteo once per unique (destination, weekend).
    query_groups: dict[tuple[str, str, str], list[dict]] = {}
    for deal in deals:
        iata = deal.get("destination_iata", "")
        out_date = (deal.get("outbound_departure") or "")[:10]
        in_date = (deal.get("inbound_departure") or "")[:10]
        lat = deal.get("destination_lat")
        lon = deal.get("destination_lon")
        if not iata or not out_date or not in_date or lat is None or lon is None:
            continue
        query_groups.setdefault((iata, out_date, in_date), []).append(deal)

    for (iata, out_date, in_date), group in query_groups.items():
        cache_key = f"{iata}|{out_date}|{in_date}"
        cached = cache.get(cache_key)
        # Honour the TTL.
        if cached:
            try:
                fetched = dt.datetime.fromisoformat(cached.get("fetched_at", ""))
                if now - fetched > ttl:
                    cached = None
            except (TypeError, ValueError):
                cached = None

        if cached is None:
            # Pick coords from the first deal in the group (they all
            # have the same destination so coords match).
            first = group[0]
            lat = first.get("destination_lat")
            lon = first.get("destination_lon")
            if lat is None or lon is None:
                continue
            cached = _fetch_weather(lat, lon, out_date, in_date, http_get)
            if cached:
                cache[cache_key] = cached
                fetched_count += 1
            else:
                # Remember the failure briefly so we don't re-hit
                # open-meteo for the same missing key within the TTL.
                cache[cache_key] = {
                    "_no_weather": True,
                    "fetched_at": now.isoformat(),
                }

        if cached and not cached.get("_no_weather"):
            for deal in group:
                deal["weather_emoji"] = cached.get("emoji")
                deal["weather_text"] = cached.get("text")
                deal["weather_high_c"] = cached.get("high_c")
                deal["weather_low_c"] = cached.get("low_c")

    save_weather_cache(cache_path, cache)
    if fetched_count:
        print(f"Weather: fetched {fetched_count} new open-meteo forecasts.")
