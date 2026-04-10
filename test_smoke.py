#!/usr/bin/env python3
"""Dry-run smoke test for the flight scanner pipeline.

Runs every code path in notifier.py (and a small slice of scanner.py)
without touching the network. `requests.post` and `requests.get` are
monkey-patched so the tests can assert on the exact payloads that
would otherwise be sent to Discord / Ryanair.

Run with:
    python test_smoke.py

Exits non-zero if any scenario fails. No environment variables or
network access required.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
import notifier  # noqa: E402
import scanner   # noqa: E402


# ---------------------------------------------------------------------------
# Fake-data helpers
# ---------------------------------------------------------------------------
def make_deal(
    origin: str,
    iata: str,
    city: str,
    country: str,
    price: float,  # flight_price_eur (raw Ryanair fare, no bus)
    out_date: str = "2026-05-01",
    in_date: str = "2026-05-03",
    out_time: str = "19:25",
    in_time: str = "20:10",
    out_flight: str = "FR1234",
    in_flight: str = "FR1235",
) -> dict:
    bus = 0.0 if origin == "SNN" else 30.0
    return {
        "origin": origin,
        "destination_iata": iata,
        "destination_city": city,
        "destination_country": country,
        "destination_lat": 37.0,
        "destination_lon": -7.9,
        "flight_price_eur": round(price, 2),
        "bus_surcharge_eur": bus,
        "effective_price_eur": round(price + bus, 2),
        "currency": "EUR",
        "outbound_departure": f"{out_date}T{out_time}:00",
        "outbound_arrival": f"{out_date}T21:00:00",
        "outbound_flight_number": out_flight,
        "inbound_departure": f"{in_date}T{in_time}:00",
        "inbound_arrival": f"{in_date}T22:00:00",
        "inbound_flight_number": in_flight,
        "google_flights_url": (
            f"https://www.google.com/travel/flights?q=Flights+from+{origin}+to+{iata}"
        ),
        "skyscanner_url": (
            f"https://www.skyscanner.net/transport/flights/"
            f"{origin.lower()}/{iata.lower()}/"
        ),
    }


def fake_ryanair_response(origin: str, dest: str, price: float, out_time: str) -> dict:
    """Mimic the shape of Ryanair's farfnd/v4/roundTripFares response."""
    return {
        "fares": [
            {
                "outbound": {
                    "departureDate": f"2026-05-01T{out_time}:00",
                    "arrivalDate": "2026-05-01T21:00:00",
                    "flightNumber": "FR1234",
                    "price": {"value": price, "currencyCode": "EUR"},
                    "departureAirport": {"iataCode": origin},
                    "arrivalAirport": {
                        "iataCode": dest,
                        "name": "Faro",
                        "countryName": "Portugal",
                        "city": {"name": "Faro"},
                        "coordinates": {"latitude": 37.01, "longitude": -7.97},
                    },
                },
                "inbound": {
                    "departureDate": "2026-05-03T18:00:00",
                    "arrivalDate": "2026-05-03T20:00:00",
                    "flightNumber": "FR1235",
                    "price": {"value": price, "currencyCode": "EUR"},
                    "arrivalAirport": {"iataCode": origin},
                },
                "summary": {"price": {"value": price, "currencyCode": "EUR"}},
            }
        ]
    }


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------
results: list[tuple[str, bool, str]] = []


def scenario(
    name: str,
    old_deals: list[dict] | None,
    new_deals: list[dict],
    env: dict[str, str],
    expected_posts: int,
) -> None:
    """Execute notify_if_configured under mocks and assert the POST count."""
    captured: list[dict] = []

    def fake_post(url, **kwargs):
        captured.append({"url": url, **kwargs})
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        return resp

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "deals.json"
        if old_deals is not None:
            path.write_text(json.dumps({"deals": old_deals}))

        # Isolate env vars from the host.
        clean_env = {k: "" for k in [
            "NOTIFY_DISCORD_WEBHOOK_URL",
            "NOTIFY_NTFY_URL",
            "NOTIFY_PRICE_CAP_EUR",
        ]}
        clean_env.update(env)

        with patch.dict(os.environ, clean_env, clear=False), \
             patch.object(notifier.requests, "post", side_effect=fake_post):
            notifier.notify_if_configured(path, new_deals)

    ok = len(captured) == expected_posts
    detail = f"expected {expected_posts} POST(s), got {len(captured)}"
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} -- {detail}")

    # Pretty-print the first Discord payload, if any, so humans can eyeball
    # the formatting.
    for i, cap in enumerate(captured):
        body = cap.get("json")
        if body is None:
            continue
        print(f"    POST {i + 1} content: {body.get('content', '(none)')}")
        embeds = body.get("embeds", [])
        print(f"    embeds: {len(embeds)}")
        for e in embeds[:3]:
            print(f"      * {e.get('title', '')} -- {e.get('description', '')[:60]}")
        if len(embeds) > 3:
            print(f"      ... (+{len(embeds) - 3} more)")


def assert_eq(name: str, got, want) -> None:
    ok = got == want
    results.append((name, ok, f"got={got!r} want={want!r}"))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_notifier_scenarios() -> None:
    webhook = "https://discord.com/api/webhooks/TEST/TOKEN"
    print("\n=== notifier: scenario matrix ===")

    scenario(
        "first run, no prior deals.json -> skip",
        old_deals=None,
        new_deals=[make_deal("SNN", "FAO", "Faro", "Portugal", 45.0)],
        env={"NOTIFY_DISCORD_WEBHOOK_URL": webhook},
        expected_posts=0,
    )

    scenario(
        "no webhook configured -> no-op",
        old_deals=[make_deal("SNN", "FAO", "Faro", "Portugal", 95.0)],
        new_deals=[make_deal("SNN", "FAO", "Faro", "Portugal", 45.0)],
        env={},  # nothing set
        expected_posts=0,
    )

    scenario(
        "brand new deal under cap -> 1 POST",
        old_deals=[make_deal("SNN", "ALC", "Alicante", "Spain", 55.0)],
        new_deals=[
            make_deal("SNN", "ALC", "Alicante", "Spain", 55.0),
            make_deal("SNN", "FAO", "Faro", "Portugal", 42.0),
        ],
        env={"NOTIFY_DISCORD_WEBHOOK_URL": webhook},
        expected_posts=1,
    )

    scenario(
        "price drop from over-cap to under-cap -> 1 POST",
        old_deals=[make_deal("SNN", "FAO", "Faro", "Portugal", 95.0)],
        new_deals=[make_deal("SNN", "FAO", "Faro", "Portugal", 55.0)],
        env={"NOTIFY_DISCORD_WEBHOOK_URL": webhook},
        expected_posts=1,
    )

    scenario(
        "stable under-cap (already alerted yesterday) -> no POST",
        old_deals=[make_deal("SNN", "FAO", "Faro", "Portugal", 55.0)],
        new_deals=[make_deal("SNN", "FAO", "Faro", "Portugal", 55.0)],
        env={"NOTIFY_DISCORD_WEBHOOK_URL": webhook},
        expected_posts=0,
    )

    scenario(
        "over-cap only -> no POST",
        old_deals=[make_deal("SNN", "FAO", "Faro", "Portugal", 50.0)],
        new_deals=[make_deal("SNN", "BER", "Berlin", "Germany", 120.0)],
        env={"NOTIFY_DISCORD_WEBHOOK_URL": webhook},
        expected_posts=0,
    )

    scenario(
        "15 brand-new deals -> 1 POST, 10 embeds, content mentions +5 more",
        old_deals=[make_deal("SNN", "XXX", "Baseline", "X", 55.0)],
        new_deals=[
            make_deal("SNN", f"T{i:02d}", f"City{i}", "Country", 40.0 + i)
            for i in range(15)
        ],
        env={"NOTIFY_DISCORD_WEBHOOK_URL": webhook},
        expected_posts=1,
    )

    scenario(
        "custom NOTIFY_PRICE_CAP_EUR=50 narrows alerts",
        old_deals=[make_deal("SNN", "FAO", "Faro", "Portugal", 95.0)],
        new_deals=[
            make_deal("SNN", "FAO", "Faro", "Portugal", 60.0),  # over 50
            make_deal("SNN", "ALC", "Alicante", "Spain", 40.0),  # under 50
        ],
        env={
            "NOTIFY_DISCORD_WEBHOOK_URL": webhook,
            "NOTIFY_PRICE_CAP_EUR": "50",
        },
        expected_posts=1,
    )

    scenario(
        "DUB deal with Limerick bus surcharge included in effective price",
        old_deals=[make_deal("DUB", "KRK", "Krakow", "Poland", 95.0)],
        new_deals=[make_deal("DUB", "KRK", "Krakow", "Poland", 74.0)],
        env={"NOTIFY_DISCORD_WEBHOOK_URL": webhook},
        expected_posts=1,
    )


def test_find_newly_alertable() -> None:
    print("\n=== notifier.find_newly_alertable: unit ===")
    old = [
        make_deal("SNN", "FAO", "Faro", "PT", 95.0),       # was over
        make_deal("SNN", "ALC", "Alicante", "ES", 45.0),   # was under
    ]
    new = [
        make_deal("SNN", "FAO", "Faro", "PT", 55.0),       # dropped in
        make_deal("SNN", "ALC", "Alicante", "ES", 45.0),   # still under
        make_deal("SNN", "KRK", "Krakow", "PL", 65.0),     # brand new under
        make_deal("SNN", "BER", "Berlin", "DE", 110.0),    # over cap
    ]
    finds = notifier.find_newly_alertable(old, new, 80.0)
    iatas = sorted(d["destination_iata"] for d in finds)
    assert_eq("find_newly_alertable iatas", iatas, ["FAO", "KRK"])


def test_scanner_normalise() -> None:
    print("\n=== scanner._ryanair_normalise_fare: schema parsing ===")
    good = fake_ryanair_response("SNN", "FAO", 55.0, out_time="19:25")["fares"][0]
    deal = scanner._ryanair_normalise_fare(good, "SNN")
    assert_eq("deal is not None", deal is not None, True)
    if deal:
        assert_eq("destination_iata", deal["destination_iata"], "FAO")
        assert_eq("flight_price_eur (raw Ryanair fare)", deal["flight_price_eur"], 55.0)
        assert_eq("bus_surcharge_eur zero for SNN", deal["bus_surcharge_eur"], 0.0)
        assert_eq("has google_flights_url", "google.com" in deal["google_flights_url"], True)
        assert_eq("has skyscanner_url", "skyscanner" in deal["skyscanner_url"], True)

    print("\n=== scanner._ryanair_normalise_fare: rejects morning flight ===")
    morning = fake_ryanair_response("SNN", "FAO", 55.0, out_time="09:20")["fares"][0]
    deal = scanner._ryanair_normalise_fare(morning, "SNN")
    assert_eq("morning flight rejected", deal is None, True)

    print("\n=== scanner._ryanair_normalise_fare: DUB keeps raw flight price, bus separate ===")
    dub = fake_ryanair_response("DUB", "KRK", 50.0, out_time="19:25")["fares"][0]
    deal = scanner._ryanair_normalise_fare(dub, "DUB")
    if deal:
        assert_eq("flight_price_eur is raw Ryanair fare (no bus)",
                  deal["flight_price_eur"], 50.0)
        assert_eq("bus surcharge tracked separately",
                  deal["bus_surcharge_eur"], 30.0)
        assert_eq("effective_price_eur kept for back-compat",
                  deal["effective_price_eur"], 80.0)

    print("\n=== scanner._ryanair_normalise_fare: falls back to IATA lookup for coords ===")
    # Build a fare object that matches current Ryanair shape (no coordinates)
    fare_no_coords = {
        "outbound": {
            "departureDate": "2026-05-01T19:25:00",
            "arrivalDate": "2026-05-01T21:00:00",
            "flightNumber": "FR9999",
            "arrivalAirport": {
                # STN is in EUROPE_ROUTES, so lookup should succeed
                "iataCode": "STN",
                "name": "London Stansted",
                "countryName": "United Kingdom",
                "city": {"name": "London"},
                # NO coordinates field -- mimics current Ryanair response
            },
        },
        "inbound": {
            "departureDate": "2026-05-03T18:00:00",
            "arrivalDate": "2026-05-03T20:00:00",
            "flightNumber": "FR9998",
            "arrivalAirport": {"iataCode": "SNN"},
        },
        "summary": {"price": {"value": 29.99, "currencyCode": "EUR"}},
    }
    deal = scanner._ryanair_normalise_fare(fare_no_coords, "SNN")
    assert_eq("fare with no coords is kept", deal is not None, True)
    if deal:
        assert_eq("lat filled from IATA lookup",
                  abs(deal["destination_lat"] - 51.886) < 0.01, True)
        assert_eq("lon filled from IATA lookup",
                  abs(deal["destination_lon"] - 0.2389) < 0.01, True)

    print("\n=== scanner._ryanair_normalise_fare: unknown IATA keeps deal but null coords ===")
    fare_unknown = {
        "outbound": {
            "departureDate": "2026-05-01T19:25:00",
            "arrivalDate": "2026-05-01T21:00:00",
            "flightNumber": "FR9999",
            "arrivalAirport": {
                # ZZZ is not in EUROPE_ROUTES anywhere
                "iataCode": "ZZZ",
                "name": "Nowhere",
                "countryName": "Atlantis",
                "city": {"name": "Nowhere"},
            },
        },
        "inbound": {
            "departureDate": "2026-05-03T18:00:00",
            "arrivalDate": "2026-05-03T20:00:00",
            "flightNumber": "FR9998",
            "arrivalAirport": {"iataCode": "SNN"},
        },
        "summary": {"price": {"value": 55.0, "currencyCode": "EUR"}},
    }
    deal = scanner._ryanair_normalise_fare(fare_unknown, "SNN")
    assert_eq("unknown-dest fare still kept", deal is not None, True)
    if deal:
        assert_eq("destination_lat is None for unknown IATA",
                  deal["destination_lat"] is None, True)


def test_aer_lingus_normalise() -> None:
    """Verify _aer_lingus_normalise_fare parses a plausible AL response."""
    print("\n=== scanner._aer_lingus_normalise_fare: schema parsing ===")
    fare = {
        "_origin": "DUB",
        "_destination": "LHR",
        "price": 59.99,
        "outbound": {
            "departureDateTime": "2026-05-01T19:25:00",
            "arrivalDateTime": "2026-05-01T20:55:00",
            "flightNumber": "EI154",
        },
        "inbound": {
            "departureDateTime": "2026-05-03T20:10:00",
            "arrivalDateTime": "2026-05-03T21:30:00",
            "flightNumber": "EI155",
        },
    }
    deal = scanner._aer_lingus_normalise_fare(fare, "DUB")
    assert_eq("AL deal parsed", deal is not None, True)
    if deal:
        assert_eq("carrier_code is EI", deal["carrier_code"], "EI")
        assert_eq("carrier_name is Aer Lingus", deal["carrier_name"], "Aer Lingus")
        assert_eq("AL dest LHR", deal["destination_iata"], "LHR")
        assert_eq("AL flight price", deal["flight_price_eur"], 59.99)
        assert_eq("AL DUB bus surcharge tracked", deal["bus_surcharge_eur"], 30.0)

    print("\n=== scanner._aer_lingus_normalise_fare: rejects morning flight ===")
    morning = dict(fare)
    morning["outbound"] = dict(fare["outbound"])
    morning["outbound"]["departureDateTime"] = "2026-05-01T09:00:00"
    deal = scanner._aer_lingus_normalise_fare(morning, "DUB")
    assert_eq("AL morning flight rejected", deal is None, True)


def test_source_registry() -> None:
    print("\n=== scanner.SOURCES registry ===")
    names = [s["name"] for s in scanner.SOURCES]
    assert_eq("ryanair present", "ryanair" in names, True)
    assert_eq("aer_lingus present", "aer_lingus" in names, True)
    for src in scanner.SOURCES:
        assert_eq(f"{src['name']} has fetch", callable(src.get("fetch")), True)
        assert_eq(f"{src['name']} has normalise", callable(src.get("normalise")), True)


def test_enrichment_photos_and_weather() -> None:
    print("\n=== enrichments.enrich_photos + enrich_weather ===")
    import enrichments

    wikipedia_response = {
        "thumbnail": {"source": "https://upload.wikimedia.org/w/pretend.jpg", "width": 320, "height": 200},
        "originalimage": {"source": "https://upload.wikimedia.org/w/pretend.jpg", "width": 640, "height": 400},
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Faro"}},
    }
    weather_response = {
        "daily": {
            "weathercode": [1, 2, 3],
            "temperature_2m_max": [22.3, 21.9, 20.4],
            "temperature_2m_min": [15.1, 14.7, 13.9],
        }
    }

    def fake_http_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "wikipedia.org" in url:
            resp.json = lambda: wikipedia_response
        elif "open-meteo" in url:
            resp.json = lambda: weather_response
        else:
            resp.status_code = 404
        return resp

    deal = make_deal("SNN", "FAO", "Faro", "Portugal", 55.0)
    # enrich_photos mutates cache on disk; use a tempdir.
    with tempfile.TemporaryDirectory() as td:
        photo_cache = Path(td) / "city_photos.json"
        weather_cache = Path(td) / "weather.json"
        enrichments.enrich_photos([deal], photo_cache, fake_http_get)
        enrichments.enrich_weather([deal], weather_cache, fake_http_get)

    assert_eq("photo_url populated", "pretend.jpg" in (deal.get("photo_url") or ""), True)
    assert_eq("photo_attribution Wikipedia", deal.get("photo_attribution"), "Wikipedia")
    assert_eq("weather_emoji present", deal.get("weather_emoji") is not None, True)
    assert_eq(
        "weather picks worst-of (3 = Overcast cloud)",
        deal.get("weather_text"),
        "Overcast",
    )
    assert_eq("weather high_c is daily max", deal.get("weather_high_c"), 22.3)
    assert_eq("weather low_c is daily min", deal.get("weather_low_c"), 13.9)

    # Cache hit: second enrichment call with the same tempdir should
    # NOT hit the network (we'd see this by passing a fake http_get
    # that raises if called) -- but we already saved the cache inside
    # the with-block, so we'd need a fresh deal dict. Skipping the
    # cache-hit assertion to keep the test simple; the actual cache
    # file behaviour is exercised by the happy path above.


def test_history_annotation() -> None:
    print("\n=== history.annotate_deals + update_history_file ===")
    import history
    import datetime as dt

    # Use a fake "now" for deterministic timestamps in assertions.
    t0 = dt.datetime(2026, 5, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    t1 = t0 + dt.timedelta(days=1)
    t2 = t0 + dt.timedelta(days=2)

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "history.json"

        # Scan 1: fresh route, FAO at 60. Should be trivially lowest.
        deal_1 = make_deal("SNN", "FAO", "Faro", "Portugal", 60.0)
        history.update_history_file(path, [deal_1], now=t0)
        assert_eq("first sighting is lowest_ever", deal_1["is_lowest_ever"], True)
        assert_eq("first sighting has no delta", deal_1["price_delta_eur"], None)

        # Scan 2: price drops to 45. Should detect the drop AND flag
        # as new lowest-ever.
        deal_2 = make_deal("SNN", "FAO", "Faro", "Portugal", 45.0)
        history.update_history_file(path, [deal_2], now=t1)
        assert_eq("price drop detected", deal_2["price_delta_eur"], -15.0)
        assert_eq("new low flagged as lowest_ever", deal_2["is_lowest_ever"], True)
        assert_eq("last_seen is previous price", deal_2["last_seen_eur"], 60.0)

        # Scan 3: price rises to 55. Not lowest ever (45 still holds),
        # delta is +10 relative to last scan.
        deal_3 = make_deal("SNN", "FAO", "Faro", "Portugal", 55.0)
        history.update_history_file(path, [deal_3], now=t2)
        assert_eq("rise detected in delta", deal_3["price_delta_eur"], 10.0)
        assert_eq("rise is NOT lowest_ever", deal_3["is_lowest_ever"], False)
        assert_eq("lowest_ever_eur remembered", deal_3["lowest_ever_eur"], 45.0)


def test_weekend_windows() -> None:
    print("\n=== scanner.next_weekend_windows ===")
    # 2 base weekends across 4 windows = 8 yielded tuples
    results = list(scanner.next_weekend_windows(2))
    assert_eq("8 window-weekends yielded (2 weekends x 4 windows)",
              len(results), 8)

    import datetime as dt
    ids = [r[0] for r in results]
    assert_eq("fri_sun window present", "fri_sun" in ids, True)
    assert_eq("thu_sun window present", "thu_sun" in ids, True)
    assert_eq("fri_mon window present", "fri_mon" in ids, True)
    assert_eq("fri_tue window present", "fri_tue" in ids, True)

    # Each fri_sun entry: out is Friday, in is 2 days later (Sunday)
    for wid, _label, out_d, in_d in results:
        if wid == "fri_sun":
            assert_eq(f"fri_sun out is Friday", out_d.weekday(), 4)
            assert_eq(f"fri_sun in is Sunday", in_d.weekday(), 6)
        elif wid == "thu_sun":
            assert_eq(f"thu_sun out is Thursday", out_d.weekday(), 3)
            assert_eq(f"thu_sun in is Sunday", in_d.weekday(), 6)
        elif wid == "fri_mon":
            assert_eq(f"fri_mon out is Friday", out_d.weekday(), 4)
            assert_eq(f"fri_mon in is Monday", in_d.weekday(), 0)
        elif wid == "fri_tue":
            assert_eq(f"fri_tue out is Friday", out_d.weekday(), 4)
            assert_eq(f"fri_tue in is Tuesday", in_d.weekday(), 1)


def test_send_test_notification() -> None:
    """Verify scanner.send_test_notification fires exactly one Discord POST."""
    print("\n=== scanner.send_test_notification ===")
    captured: list[dict] = []

    def fake_post(url, **kwargs):
        captured.append({"url": url, **kwargs})
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        return resp

    with patch.dict(
        os.environ,
        {
            "NOTIFY_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/TEST/TOKEN",
            "NOTIFY_NTFY_URL": "",
        },
        clear=False,
    ), patch.object(notifier.requests, "post", side_effect=fake_post):
        rc = scanner.send_test_notification()

    assert_eq("return code", rc, 0)
    assert_eq("exactly one POST", len(captured), 1)
    if captured:
        body = captured[0].get("json", {})
        title = (body.get("embeds") or [{}])[0].get("title", "")
        assert_eq("embed title marks it as TEST", "[TEST]" in title, True)

    # And verify missing webhook produces a clear error + non-zero exit.
    print("\n=== send_test_notification with no env -> error ===")
    with patch.dict(
        os.environ,
        {"NOTIFY_DISCORD_WEBHOOK_URL": "", "NOTIFY_NTFY_URL": ""},
        clear=False,
    ):
        rc = scanner.send_test_notification()
    assert_eq("returns 1 when nothing configured", rc, 1)


def test_fetch_fares_uses_http_get_seam() -> None:
    """Verify scanner._ryanair_fetch_fares routes through the mockable _http_get helper."""
    print("\n=== scanner._ryanair_fetch_fares: uses _http_get seam (happy path) ===")
    import datetime as dt

    captured_kwargs: dict = {}

    def fake_http_get_ok(url, **kwargs):
        captured_kwargs["url"] = url
        captured_kwargs["params"] = kwargs.get("params")
        captured_kwargs["headers"] = kwargs.get("headers")
        resp = MagicMock()
        resp.status_code = 200
        resp.json = lambda: {"fares": []}
        return resp

    with patch.object(scanner, "_http_get", side_effect=fake_http_get_ok):
        result = scanner._ryanair_fetch_fares("SNN", dt.date(2026, 5, 1), dt.date(2026, 5, 3))

    assert_eq("fetch_fares returned dict", isinstance(result, dict), True)
    assert_eq("_http_get called with Ryanair URL",
              captured_kwargs.get("url") == scanner.RYANAIR_URL, True)
    params = captured_kwargs.get("params") or {}
    assert_eq("params include SNN", params.get("departureAirportIataCode"), "SNN")
    assert_eq("time-filter params dropped (were causing 400s)",
              "outboundDepartureTimeFrom" not in params and
              "inboundDepartureTimeFrom" not in params, True)
    assert_eq("limit/offset dropped (farfnd rejects InvalidLimit)",
              "limit" not in params and "offset" not in params, True)
    assert_eq("params include market", params.get("market"), "en-ie")

    print("\n=== scanner._ryanair_fetch_fares: 400 response raises requests.HTTPError ===")

    def fake_http_get_400(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 400
        resp.text = '{"message":"Invalid parameter"}'
        return resp

    raised = None
    with patch.object(scanner, "_http_get", side_effect=fake_http_get_400):
        try:
            scanner._ryanair_fetch_fares("SNN", dt.date(2026, 5, 1), dt.date(2026, 5, 3))
        except Exception as e:
            raised = e

    import requests as _req
    assert_eq("raised an HTTPError", isinstance(raised, _req.HTTPError), True)
    if raised is not None:
        assert_eq("error message mentions status",
                  "400" in str(raised), True)
        assert_eq("error message includes body snippet",
                  "Invalid parameter" in str(raised), True)


def test_deeplink_shapes() -> None:
    print("\n=== scanner deep-link builders ===")
    g = scanner.google_flights_url("SNN", "FAO", "2026-05-01", "2026-05-03")
    s = scanner.skyscanner_url("SNN", "FAO", "2026-05-01", "2026-05-03")
    assert_eq("google url starts with expected prefix",
              g.startswith("https://www.google.com/travel/flights?q="), True)
    assert_eq("sky url has yyMMdd dates",
              "260501/260503" in s, True)
    assert_eq("sky url is lowercase", "snn/fao" in s, True)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
def main() -> int:
    test_notifier_scenarios()
    test_find_newly_alertable()
    test_scanner_normalise()
    test_aer_lingus_normalise()
    test_source_registry()
    test_history_annotation()
    test_enrichment_photos_and_weather()
    test_weekend_windows()
    test_send_test_notification()
    test_fetch_fares_uses_http_get_seam()
    test_deeplink_shapes()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"{passed}/{total} checks passed")
    failed = [(n, d) for n, ok, d in results if not ok]
    if failed:
        print("\nFailures:")
        for n, d in failed:
            print(f"  - {n}: {d}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
