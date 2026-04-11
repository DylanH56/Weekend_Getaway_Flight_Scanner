"""Irish public holiday detection for the Weekend Getaway Flight Scanner.

Used by the scanner to tag deals as "real long weekends" -- i.e. deals
whose date range contains a public holiday, so the user doesn't need
to take a work day off to make the trip work.

The dashboard surfaces this via a bank-holiday badge on each card and
an optional "bank holidays only" filter toggle.

Irish public holidays (as of 2023 reform):

  1. New Year's Day           (1 January)
  2. First Monday in February, OR 1 February if that date falls on a
     Friday. (Reform from 2023; commemorates St Brigid's Day.)
  3. Saint Patrick's Day      (17 March)
  4. Easter Monday            (Monday after Easter Sunday, computed)
  5. First Monday in May
  6. First Monday in June
  7. First Monday in August
  8. Last Monday in October
  9. Christmas Day            (25 December)
  10. Saint Stephen's Day     (26 December)

We do NOT currently handle "substitute day" rules (e.g. when Christmas
falls on a weekend, workers get a Monday substitute). That's a minor
edge case that only matters for the Dec 25/26 pair in some years --
all other Irish holidays are either fixed-Monday or St Patrick's Day
which shifts its own "substitute" handling (and most people treat
17 March as the fixed holiday anyway). If it becomes relevant we can
bolt on substitute-day logic here without touching callers.

Only Irish holidays are considered right now. UK-based users flying
from BHX would technically want UK bank holidays instead, but (a) the
user who's driving this app is based in Ireland, (b) the tag says
"long weekend opportunity" not "your employer will pay you", and
(c) Irish and UK bank holidays overlap on most of the key dates
(Easter Mon, May, August-ish, Christmas). Adding UK holidays as a
second ruleset is a follow-up if BHX users ask for it.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache


def _easter_monday(year: int) -> dt.date:
    """Compute Easter Monday for a given year using the
    Meeus/Jones/Butcher Gregorian algorithm.

    Easter Sunday = first Sunday after the first full moon on or
    after the vernal equinox. The algorithm below is the standard
    closed-form solution -- no lookup tables needed.

    Verified against the 2026/2027 cases in the user's brief:
      2026: Easter Sunday = Apr 5, Easter Monday = Apr 6  ✓
      2027: Easter Sunday = Mar 28, Easter Monday = Mar 29 ✓
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter_sunday = dt.date(year, month, day)
    return easter_sunday + dt.timedelta(days=1)


def _first_weekday_of_month(year: int, month: int, weekday: int) -> dt.date:
    """First occurrence of `weekday` (0=Mon, 6=Sun) in the given month."""
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> dt.date:
    """Last occurrence of `weekday` (0=Mon) in the given month."""
    if month == 12:
        next_first = dt.date(year + 1, 1, 1)
    else:
        next_first = dt.date(year, month + 1, 1)
    last = next_first - dt.timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - dt.timedelta(days=offset)


@lru_cache(maxsize=16)
def irish_public_holidays(year: int) -> tuple[tuple[dt.date, str], ...]:
    """Return a tuple of (date, name) pairs for Irish public holidays
    in the given year. Cached because the computation is pure and
    callers will hit the same year many times per scan."""
    holidays: list[tuple[dt.date, str]] = [
        (dt.date(year, 1, 1), "New Year's Day"),
        (dt.date(year, 3, 17), "Saint Patrick's Day"),
        (_easter_monday(year), "Easter Monday"),
        (_first_weekday_of_month(year, 5, 0), "May Bank Holiday"),
        (_first_weekday_of_month(year, 6, 0), "June Bank Holiday"),
        (_first_weekday_of_month(year, 8, 0), "August Bank Holiday"),
        (_last_weekday_of_month(year, 10, 0), "October Bank Holiday"),
        (dt.date(year, 12, 25), "Christmas Day"),
        (dt.date(year, 12, 26), "Saint Stephen's Day"),
    ]

    # St Brigid's Day / First Monday in February rule (from 2023):
    # If 1 February falls on a Friday, THAT is the bank holiday.
    # Otherwise, the first Monday in February is the holiday.
    feb1 = dt.date(year, 2, 1)
    if feb1.weekday() == 4:  # Friday
        feb_holiday = feb1
    else:
        feb_holiday = _first_weekday_of_month(year, 2, 0)
    holidays.append((feb_holiday, "First Monday in February"))

    holidays.sort(key=lambda x: x[0])
    return tuple(holidays)


def holidays_in_range(
    start_date: dt.date, end_date: dt.date
) -> list[tuple[dt.date, str]]:
    """Return Irish public holidays falling within [start, end] inclusive.

    Start/end can span multiple years (useful when a trip crosses a
    year boundary, though that's rare for weekend getaways).
    """
    result: list[tuple[dt.date, str]] = []
    for year in range(start_date.year, end_date.year + 1):
        for h_date, h_name in irish_public_holidays(year):
            if start_date <= h_date <= end_date:
                result.append((h_date, h_name))
    return result


def long_weekend_info(
    out_date: dt.date, in_date: dt.date
) -> dict | None:
    """Classify a trip's date range against Irish bank holidays.

    Returns a dict with:
      is_long_weekend:  bool   -- True iff a bank holiday falls within
                                   the trip range (inclusive)
      holiday_name:     str    -- name of the first matching holiday
      holiday_date:     str    -- ISO date of that holiday
      holiday_bonus:    str    -- short human label of the benefit,
                                   e.g. "Easter Monday -- no work day used"

    Returns None (NOT a dict with is_long_weekend=False) when no
    holiday falls in the range, so callers can use a simple
    `if info:` check to decide whether to attach the fields.
    """
    if out_date > in_date:
        return None
    holidays = holidays_in_range(out_date, in_date)
    if not holidays:
        return None
    # Use the FIRST holiday in the range as the tag. If a trip
    # somehow contains multiple bank holidays (e.g. Dec 25/26), the
    # first one shows up in the badge -- close enough for display.
    h_date, h_name = holidays[0]
    return {
        "is_long_weekend": True,
        "holiday_name": h_name,
        "holiday_date": h_date.isoformat(),
        "holiday_bonus": f"{h_name} -- no work day used",
    }
