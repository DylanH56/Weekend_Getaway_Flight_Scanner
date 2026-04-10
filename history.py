"""Price history tracking for the flight scanner.

Maintains a rolling file of `(timestamp, route_key, flight_price_eur)`
tuples in dashboard/history.json. After each scan we:

  1. Append every deal's observation (price at this moment in time).
  2. Prune anything older than HISTORY_RETENTION_DAYS.
  3. Annotate the new deals.json with, for each deal:
       - `lowest_ever_eur`        the lowest price we've ever seen
       - `lowest_ever_at`         timestamp of that observation
       - `is_lowest_ever`         bool -- true iff current == lowest
       - `last_seen_eur`          the most recent prior observation
       - `price_delta_eur`        current minus last_seen (None if new)

The dashboard reads these fields to show "cheapest ever" badges and
price-drop deltas. The notifier reads `is_lowest_ever` to fire a
"rock-bottom" Discord alert that bypasses the normal cap.

File format is a plain JSON dict keyed by route_key to keep
annotation O(1). Each value is a list of [iso_timestamp, price]
pairs, sorted oldest -> newest.

    {
      "FR|SNN|BHX|fri_sun|2026-04-17": [
        ["2026-04-10T12:00:00+00:00", 30.02],
        ["2026-04-10T18:00:00+00:00", 28.50],
        ...
      ],
      ...
    }

A route_key encodes everything we dedupe on so two weekends to the
same city get independent histories.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


HISTORY_RETENTION_DAYS = 60


def route_key(deal: dict) -> str:
    """Compact stable string identifying a single (route, window,
    outbound date, carrier) tuple."""
    return "|".join([
        deal.get("carrier_code", "?"),
        deal.get("origin", "?"),
        deal.get("destination_iata", "?"),
        deal.get("weekend_window", "fri_sun"),
        (deal.get("outbound_departure") or "")[:10],
    ])


def load_history(path: Path) -> dict[str, list[list]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Strip any non-list values defensively.
    return {k: v for k, v in data.items() if isinstance(v, list)}


def _prune(
    history: dict[str, list[list]],
    now: dt.datetime,
    retention_days: int = HISTORY_RETENTION_DAYS,
) -> dict[str, list[list]]:
    cutoff = now - dt.timedelta(days=retention_days)
    cutoff_iso = cutoff.isoformat()
    pruned: dict[str, list[list]] = {}
    for key, observations in history.items():
        kept = [
            obs for obs in observations
            if isinstance(obs, list)
            and len(obs) == 2
            and isinstance(obs[0], str)
            and obs[0] >= cutoff_iso
        ]
        if kept:
            pruned[key] = kept
    return pruned


def append_observations(
    history: dict[str, list[list]],
    deals: list[dict],
    now: dt.datetime,
) -> None:
    """Mutate `history` in place, appending one observation per deal."""
    ts = now.isoformat()
    for deal in deals:
        price = deal.get("flight_price_eur")
        if price is None:
            continue
        key = route_key(deal)
        obs_list = history.setdefault(key, [])
        # Skip no-op appends: if the price is identical to the most
        # recent observation within the last hour, don't bloat the
        # file. Still append if the price changed or enough time passed.
        if obs_list:
            last_ts, last_price = obs_list[-1]
            if float(last_price) == float(price):
                try:
                    last_dt = dt.datetime.fromisoformat(last_ts)
                    if (now - last_dt).total_seconds() < 3600:
                        continue
                except (ValueError, TypeError):
                    pass
        obs_list.append([ts, round(float(price), 2)])


def annotate_deals(deals: list[dict], history: dict[str, list[list]]) -> None:
    """Attach price-history fields to each deal in place.

    Added fields:
      lowest_ever_eur   float or None
      lowest_ever_at    iso timestamp or None
      is_lowest_ever    bool
      last_seen_eur     float or None  (most recent PRIOR observation)
      price_delta_eur   float or None  (current minus last_seen)
    """
    for deal in deals:
        price = deal.get("flight_price_eur")
        if price is None:
            continue
        key = route_key(deal)
        obs_list = history.get(key) or []
        if not obs_list:
            deal["lowest_ever_eur"] = round(float(price), 2)
            deal["lowest_ever_at"] = None
            deal["is_lowest_ever"] = True   # first sighting is trivially lowest
            deal["last_seen_eur"] = None
            deal["price_delta_eur"] = None
            continue

        lowest_ts = None
        lowest_val = float("inf")
        for ts, p in obs_list:
            try:
                pv = float(p)
            except (TypeError, ValueError):
                continue
            if pv < lowest_val:
                lowest_val = pv
                lowest_ts = ts

        # "Last seen" is the most recent observation BEFORE the one
        # we're about to append (the current scan). Since
        # append_observations runs after annotate_deals, obs_list at
        # this point is still pre-current-scan.
        last_ts, last_price_raw = obs_list[-1]
        try:
            last_price = float(last_price_raw)
        except (TypeError, ValueError):
            last_price = None

        deal["lowest_ever_eur"] = round(lowest_val, 2) if lowest_val != float("inf") else None
        deal["lowest_ever_at"] = lowest_ts
        deal["is_lowest_ever"] = (
            deal["lowest_ever_eur"] is not None
            and float(price) <= deal["lowest_ever_eur"]
        )
        deal["last_seen_eur"] = round(last_price, 2) if last_price is not None else None
        deal["price_delta_eur"] = (
            round(float(price) - last_price, 2) if last_price is not None else None
        )


def update_history_file(
    history_path: Path,
    deals: list[dict],
    now: dt.datetime | None = None,
) -> dict[str, list[list]]:
    """Load, annotate, append, prune, save, return the updated history.

    Ordering matters:
      1. Load history from disk
      2. Annotate deals (uses history BEFORE this scan's observations
         so is_lowest_ever compares against the true baseline)
      3. Append this scan's observations
      4. Prune old entries
      5. Save to disk
    """
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    history = load_history(history_path)
    annotate_deals(deals, history)
    append_observations(history, deals, now)
    history = _prune(history, now)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, indent=0, separators=(",", ":")))
    return history
