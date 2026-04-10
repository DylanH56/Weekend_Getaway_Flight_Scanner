"""Optional notification sender for the flight scanner.

Compares the freshly-scanned deals against whatever deals.json existed
on disk *before* this run, and POSTs a push notification (Discord
webhook and/or ntfy.sh topic) for every deal that is now under the
alert price cap but wasn't under it in the previous scan.

Configured entirely by environment variables. If none are set, the
scanner runs silently. Notification failures are logged and swallowed
-- a missed ping should never break the scan itself.

Environment variables
---------------------
NOTIFY_DISCORD_WEBHOOK_URL
    Full Discord webhook URL. Create one in your own server via
    Server Settings -> Integrations -> Webhooks -> New Webhook.
    No bot, no OAuth -- just paste the URL as a repo secret.

NOTIFY_NTFY_URL
    Full ntfy.sh topic URL, e.g. https://ntfy.sh/my-secret-topic-xyz.
    Pick a random, hard-to-guess topic name (anyone who knows it
    can read your notifications -- it's essentially a shared secret).
    Install the ntfy app on your phone and subscribe to the same
    topic to receive push notifications.

NOTIFY_PRICE_CAP_EUR
    Price threshold for alerts, in EUR. Defaults to 80 -- i.e. we
    only ping you about deals under 80, leaving the 80-100 band
    visible on the dashboard but not noisy on your phone.

First-run behaviour
-------------------
On the very first scan (no prior deals.json to diff against) we skip
notifications entirely. Otherwise every run would ping you with the
full batch of whatever Ryanair happens to be selling that morning.
From the second scheduled run onward, alerts fire only on genuinely
new-or-dropped deals.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests


DEFAULT_ALERT_CAP_EUR = 80.0
MAX_NOTIFY_ITEMS = 10  # cap batch size so first real run isn't a flood


def load_previous_deals(path: Path) -> list[dict]:
    """Read the old deals.json if it exists, else return []."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return data.get("deals") or []


def _key(deal: dict) -> tuple[str, str, str]:
    return (
        deal.get("origin", ""),
        deal.get("destination_iata", ""),
        (deal.get("outbound_departure") or "")[:10],
    )


def _price(deal: dict) -> float | None:
    """Raw Ryanair flight price (not the effective-with-bus number).

    The dashboard renders flight_price_eur as the headline; the
    notifier compares on the same basis so Discord alerts match what
    the user sees in the UI.
    """
    p = deal.get("flight_price_eur")
    if p is None:
        # Backwards compatibility with very old deals.json files that
        # predate the flight_price/effective_price split.
        p = deal.get("effective_price_eur")
    return p


def find_newly_alertable(
    old_deals: list[dict],
    new_deals: list[dict],
    alert_cap: float,
) -> list[dict]:
    """Deals now <= alert_cap that were not <= alert_cap in the old scan.

    A drop from EUR 95 (not alerted yesterday) to EUR 70 (alerted today)
    is "newly alertable". A stable EUR 60 deal that was already <= cap
    yesterday is NOT re-alerted. A brand new deal that is under cap is
    alerted. A disappearing/more-expensive deal is ignored.

    Uses `flight_price_eur` as the comparison basis -- bus surcharge is
    informational only and doesn't affect whether we ping you.
    """
    old_alertable_keys: set[tuple[str, str, str]] = set()
    for d in old_deals:
        p = _price(d)
        if p is not None and p <= alert_cap:
            old_alertable_keys.add(_key(d))

    finds: list[dict] = []
    for d in new_deals:
        p = _price(d)
        if p is None or p > alert_cap:
            continue
        if _key(d) not in old_alertable_keys:
            finds.append(d)
    return finds


# ---------- Formatting helpers ----------
def _hhmm(iso: str) -> str:
    return iso[11:16] if iso and len(iso) >= 16 else ""


def _date_range(deal: dict) -> str:
    out = (deal.get("outbound_departure") or "")[:10]
    back = (deal.get("inbound_departure") or "")[:10]
    return f"{out} - {back}"


def _one_liner(deal: dict) -> str:
    price = _price(deal) or 0
    city = deal.get("destination_city") or deal.get("destination_iata", "?")
    iata = deal.get("destination_iata", "")
    origin = deal.get("origin", "?")
    return f"EUR {price:.0f}  {origin} -> {city} ({iata})  {_date_range(deal)}"


# ---------- Discord ----------
def _notify_discord(webhook_url: str, deals: list[dict], alert_cap: float) -> None:
    """Post a rich-embed Discord message. Up to 10 embeds per message."""
    batch = deals[:MAX_NOTIFY_ITEMS]
    embeds: list[dict] = []
    for d in batch:
        price = _price(d)
        if price is None:
            continue
        city = d.get("destination_city") or d.get("destination_iata", "?")
        country = d.get("destination_country", "")
        origin = d.get("origin", "?")
        bus = d.get("bus_surcharge_eur", 0) or 0
        bus_note = (
            f"+ EUR {bus:.0f} Limerick bus (not incl.)"
            if bus
            else "direct from Shannon"
        )
        out_dep = _hhmm(d.get("outbound_departure") or "")
        out_arr = _hhmm(d.get("outbound_arrival") or "")
        in_dep = _hhmm(d.get("inbound_departure") or "")
        in_arr = _hhmm(d.get("inbound_arrival") or "")
        flights = (
            f"**OUT** {d.get('outbound_flight_number', '')} "
            f"{out_dep} \u2192 {out_arr}\n"
            f"**RET** {d.get('inbound_flight_number', '')} "
            f"{in_dep} \u2192 {in_arr}"
        )
        embeds.append({
            "title": f"\u20AC{price:.0f}  |  {city}, {country}",
            "description": (
                f"**{origin}** \u2192 {d.get('destination_iata', '')}  "
                f"|  {_date_range(d)}  |  _{bus_note}_"
            ),
            "color": 0x4ADE80,
            "fields": [
                {"name": "Flights", "value": flights, "inline": False},
                {
                    "name": "Book",
                    "value": (
                        f"[Google Flights]({d.get('google_flights_url', '')}) "
                        f"\u00b7 [Skyscanner]({d.get('skyscanner_url', '')})"
                    ),
                    "inline": False,
                },
            ],
        })

    if not embeds:
        return

    extra = ""
    if len(deals) > MAX_NOTIFY_ITEMS:
        extra = f"  (+{len(deals) - MAX_NOTIFY_ITEMS} more on the dashboard)"
    content = (
        f"**{len(deals)} new weekend flight"
        f"{'' if len(deals) == 1 else 's'} under \u20AC{alert_cap:.0f}**"
        f"{extra}"
    )
    body = {
        "username": "Weekend Getaway Scanner",
        "content": content,
        "embeds": embeds,
    }
    try:
        r = requests.post(webhook_url, json=body, timeout=15)
        r.raise_for_status()
        print(f"  [notify] Discord: posted {len(embeds)} deal(s)")
    except requests.RequestException as e:
        print(f"  [notify] Discord failed: {e}", file=sys.stderr)


# ---------- ntfy.sh ----------
def _notify_ntfy(topic_url: str, deals: list[dict], alert_cap: float) -> None:
    """Post a plain-text ntfy message with title/priority/click headers."""
    lines = [_one_liner(d) for d in deals[:MAX_NOTIFY_ITEMS]]
    if len(deals) > MAX_NOTIFY_ITEMS:
        lines.append(f"...and {len(deals) - MAX_NOTIFY_ITEMS} more on the dashboard")
    body = "\n".join(lines).encode("utf-8")

    plural = "" if len(deals) == 1 else "s"
    title = f"{len(deals)} new flight{plural} under EUR {alert_cap:.0f}"
    headers = {
        "Title": title,
        "Priority": "high",
        "Tags": "airplane,money_with_wings",
    }
    # Tap the notification -> jump to the cheapest deal's booking link.
    click_url = (deals[0].get("google_flights_url") or "") if deals else ""
    if click_url:
        headers["Click"] = click_url

    try:
        r = requests.post(topic_url, data=body, headers=headers, timeout=15)
        r.raise_for_status()
        print(f"  [notify] ntfy: posted {len(deals)} deal(s)")
    except requests.RequestException as e:
        print(f"  [notify] ntfy failed: {e}", file=sys.stderr)


# ---------- Orchestrator ----------
def notify_if_configured(previous_path: Path, new_deals: list[dict]) -> None:
    """Main entry point. Safe to call with no channels configured."""
    discord = os.environ.get("NOTIFY_DISCORD_WEBHOOK_URL", "").strip()
    ntfy = os.environ.get("NOTIFY_NTFY_URL", "").strip()
    if not (discord or ntfy):
        return

    try:
        alert_cap = float(
            os.environ.get("NOTIFY_PRICE_CAP_EUR") or DEFAULT_ALERT_CAP_EUR
        )
    except ValueError:
        alert_cap = DEFAULT_ALERT_CAP_EUR

    old_deals = load_previous_deals(previous_path)
    if not old_deals:
        print(
            "  [notify] no prior deals.json -- treating this as the baseline, "
            "no notifications will be sent this run"
        )
        return

    newly = find_newly_alertable(old_deals, new_deals, alert_cap)
    if not newly:
        print(
            f"  [notify] no newly-alertable deals <= EUR {alert_cap:.0f} "
            f"(compared against {len(old_deals)} prior deals)"
        )
        return

    # Cheapest first in the notification body.
    newly.sort(key=lambda d: d.get("effective_price_eur") or 9999)

    if discord:
        _notify_discord(discord, newly, alert_cap)
    if ntfy:
        _notify_ntfy(ntfy, newly, alert_cap)
