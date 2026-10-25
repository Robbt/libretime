"""
The PHP date semantics the schedule builder depends on.

PHP ``DateTime`` is not ``datetime.datetime``: mutating the month or the day
changes the wall clock and re-resolves the offset, comparisons happen on the
absolute instant, and a local time that does not exist is normalised forward.
All three decide where instances land on the calendar, so they are pinned here
explicitly.

Most expectations are the ones the legacy suite asserts against the PHP
functions this module replaces, namely
``legacy/tests/application/services/unit/ShowServiceUnitTest.php``. A failure
therefore means the port and the original disagree, which is the only
comparison worth having in CI. The cases PHP never tested - the transition
itself and instant based comparison - were derived from the probes under
``tools/schedule-equivalence/`` that ran against the legacy service.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from ...services._datetime import Wall, get_zone
from ...services.populate import (
    monthly_weekly_interval,
    next_monthly_monthly_date,
    next_monthly_weekly_date,
    parse_duration,
    to_microseconds,
    utc_start_end,
)

UTC = get_zone("UTC")
BERLIN = get_zone("Europe/Berlin")
NEW_YORK = get_zone("America/New_York")
TORONTO = get_zone("America/Toronto")
BRISBANE = get_zone("Australia/Brisbane")
VANCOUVER = get_zone("America/Vancouver")

# The legacy interval helper answers with names; the port answers with the
# numbers cc_show_days.day uses, where Monday is 0.
ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
WEEKDAY = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


# The signature deliberately mirrors datetime(), which takes the same seven values
# positionally; callers below read as dates, and making the time keyword-only would have
# meant rewriting forty call sites to satisfy a clarity rule that datetime() itself
# breaks.
# pylint: disable-next=too-many-positional-arguments
def utc_moment(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    microsecond: int = 0,
) -> datetime:
    """Timezone aware UTC, the way the port answers."""
    return datetime(
        year, month, day, hour, minute, second, microsecond, tzinfo=timezone.utc
    )


# --------------------------------------------------------------------------
# getMonthlyWeeklyRepeatInterval()
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "interval"),
    [
        (date(2044, 1, 1), ("first", "Friday")),
        (date(2044, 1, 12), ("second", "Tuesday")),
        (date(2044, 1, 18), ("third", "Monday")),
        (date(2044, 1, 28), ("fourth", "Thursday")),
        (date(2044, 1, 30), ("fifth", "Saturday")),
    ],
)
def test_monthly_weekly_interval(day, interval):
    """ShowServiceUnitTest::testGetMonthlyWeeklyRepeatInterval."""
    ordinal, weekday = interval
    start = Wall.from_fields(day, time(0, 0), UTC)

    assert monthly_weekly_interval(start) == (ORDINAL[ordinal], WEEKDAY[weekday])


# --------------------------------------------------------------------------
# getNextMonthlyMonthlyRepeatDate()
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        pytest.param(date(2044, 1, 1), datetime(2044, 2, 1), id="plain-month"),
        # 2044 is a leap year, so February has 29 days and the 30th does not
        # exist: the month is skipped. This is also the case that hangs an
        # implementation which does not advance the cursor on every iteration.
        pytest.param(date(2044, 1, 30), datetime(2044, 3, 30), id="leap-february"),
        pytest.param(date(2026, 1, 31), datetime(2026, 3, 31), id="february-31st"),
        pytest.param(date(2026, 4, 30), datetime(2026, 5, 30), id="thirty-days"),
    ],
)
def test_next_monthly_monthly_date(start, expected):
    """ShowServiceUnitTest::testGetNextMonthlyMonthlyRepeatDate."""
    current = next_monthly_monthly_date(
        Wall.from_fields(start, time(0, 0), UTC), UTC, time(0, 0)
    )

    assert current.wall == expected


def test_monthly_monthly_keeps_only_months_with_that_day():
    """A show on the 31st skips every shorter month, as the legacy one does."""
    current = Wall.from_fields(date(2026, 1, 31), time(17, 0), UTC)

    days = []
    for _ in range(6):
        current = next_monthly_monthly_date(current, UTC, time(17, 0))
        days.append(current.wall)

    assert days == [
        datetime(2026, 3, 31, 17),
        datetime(2026, 5, 31, 17),
        datetime(2026, 7, 31, 17),
        datetime(2026, 8, 31, 17),
        datetime(2026, 10, 31, 17),
        datetime(2026, 12, 31, 17),
    ]


# --------------------------------------------------------------------------
# getNextMonthlyWeeklyRepeatDate()
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ordinal", "weekday", "expected"),
    [
        ("first", "Friday", datetime(2044, 2, 5)),
        # No fifth Saturday in February or March 2044, so both are skipped.
        ("fifth", "Saturday", datetime(2044, 4, 30)),
        ("fourth", "Monday", datetime(2044, 2, 22)),
    ],
)
def test_next_monthly_weekly_date(ordinal, weekday, expected):
    """ShowServiceUnitTest::testGetNextMonthlyWeeklyRepeatDate."""
    base = Wall.from_wall(datetime(2044, 2, 1), UTC)

    current = next_monthly_weekly_date(
        base, UTC, time(0, 0), ORDINAL[ordinal], WEEKDAY[weekday]
    )

    assert current.wall == expected


def test_monthly_weekly_occurrence_is_counted_in_utc():
    """
    The weekday occurrence is looked up in UTC, not in the show timezone.

    PHP calls ``strtotime("first Monday 2026-02")``, which answers in the
    default timezone of the legacy service, and only then moves the result into
    the timezone of the show. For a New York show at 20:00 the first Monday of
    February 2026 therefore airs on Sunday evening local time - which is what
    the legacy service does and what the calendar has to keep showing.
    """
    base = Wall.from_wall(datetime(2026, 2, 1), UTC)

    current = next_monthly_weekly_date(
        base, NEW_YORK, time(20, 0), 1, WEEKDAY["Monday"]
    )

    assert current.wall == datetime(2026, 2, 1, 20)
    assert current.wall.strftime("%A") == "Sunday"
    assert current.utc == utc_moment(2026, 2, 2, 1, 0)


# --------------------------------------------------------------------------
# createUTCStartEndDateTime()
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "local", "first_utc", "last_utc"),
    [
        pytest.param(
            TORONTO,
            datetime(2044, 1, 1, 6, 30),
            utc_moment(2044, 1, 1, 11, 30),
            utc_moment(2044, 1, 1, 12, 30),
            id="toronto",
        ),
        pytest.param(
            BRISBANE,
            datetime(2044, 1, 1, 6, 30),
            utc_moment(2043, 12, 31, 20, 30),
            utc_moment(2043, 12, 31, 21, 30),
            id="brisbane",
        ),
        # Not 2044 as in the legacy unit test: the timezone database PHP was
        # built with (2024.2, bundled) and the one Python reads (2026c) disagree
        # about America/Vancouver from 2027 on, so an absolute instant that far
        # out is an assertion about the tzdata version rather than about the
        # code. 2026 is answered the same by both. See the timezone database
        # note in tools/schedule-equivalence/README.md.
        pytest.param(
            VANCOUVER,
            datetime(2026, 1, 1, 6, 30),
            utc_moment(2026, 1, 1, 14, 30),
            utc_moment(2026, 1, 1, 15, 30),
            id="vancouver",
        ),
    ],
)
def test_utc_start_end(zone, local, first_utc, last_utc):
    """ShowServiceUnitTest::testCreateUTCStartEndDateTime."""
    start = Wall.from_wall(local, zone)

    first, last = utc_start_end(start, "01:00")

    assert (first.utc, last.utc) == (first_utc, last_utc)


def test_utc_start_end_rebroadcast_offset_replaces_the_time_of_day():
    """
    A rebroadcast does not shift the original: the offset carries the day and
    the clock time of the rebroadcast itself.

    ``createUTCStartEndDateTime()`` takes the *date* of the original and the
    hours and minutes of the rebroadcast, so the minutes of the original show
    are dropped on the way. Same case as the legacy unit test.
    """
    start = Wall.from_wall(datetime(2044, 1, 1, 6, 30), TORONTO)

    first, last = utc_start_end(start, "01:00", offset=(1, 6, 30))

    assert first.utc == utc_moment(2044, 1, 2, 11, 30)
    assert last.utc == utc_moment(2044, 1, 2, 12, 30)


def test_parse_duration_drops_the_seconds_part():
    """The legacy helper splits on ':' and ignores everything after minutes."""
    assert parse_duration("01:00") == (1, 0)
    assert parse_duration("02:30:30") == (2, 30)
    assert parse_duration("45") == (45, 0)


@pytest.mark.parametrize(
    ("value", "microseconds"),
    [
        ("1.5", 1_500_000),
        ("0", 0),
        ("", 0),
        (None, 0),
        ("nonsense", 0),
        (timedelta(seconds=2, microseconds=500_000), 2_500_000),
    ],
)
def test_to_microseconds(value, microseconds):
    """Seconds as a preference string, or an interval read through the ORM."""
    assert to_microseconds(value) == microseconds


# --------------------------------------------------------------------------
# Wall
# --------------------------------------------------------------------------


def test_weekly_repeat_holds_the_wall_clock_across_a_transition():
    """
    Weekly instances keep the local time, so their absolute moment moves.

    A 21:00 show in Europe/Berlin loses an hour of absolute duration when the
    transition passes, which is exactly what the legacy DatePeriod iteration
    does and what the playout service has to keep receiving.
    """
    current = Wall.from_fields(date(2026, 3, 21), time(21, 0), BERLIN)

    seen = []
    for _ in range(3):
        seen.append((current.wall, current.utc))
        current = current.add_days(7)

    assert seen == [
        (datetime(2026, 3, 21, 21), utc_moment(2026, 3, 21, 20)),
        (datetime(2026, 3, 28, 21), utc_moment(2026, 3, 28, 20)),
        (datetime(2026, 4, 4, 21), utc_moment(2026, 4, 4, 19)),
    ]


def test_missing_local_time_is_normalised_forward():
    """2026-03-29 02:30 does not exist in Berlin: PHP answers 03:30."""
    gap = Wall.from_fields(date(2026, 3, 29), time(2, 30), BERLIN)

    assert gap.wall == datetime(2026, 3, 29, 3, 30)
    assert gap.utc == utc_moment(2026, 3, 29, 1, 30)


def test_ambiguous_local_time_is_the_first_one():
    """2026-10-25 02:30 happens twice in Berlin: PHP answers the summer one."""
    fold = Wall.from_fields(date(2026, 10, 25), time(2, 30), BERLIN)

    assert fold.wall == datetime(2026, 10, 25, 2, 30)
    assert fold.utc == utc_moment(2026, 10, 25, 0, 30)


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        pytest.param(
            datetime(2026, 1, 31), 1, datetime(2026, 3, 3), id="jan-31-overflow"
        ),
        # Not twice the single step: PHP jumps the whole interval and overflows
        # only once, and March has 31 days.
        pytest.param(datetime(2026, 1, 31), 2, datetime(2026, 3, 31), id="two-months"),
        pytest.param(
            datetime(2026, 5, 31), 1, datetime(2026, 7, 1), id="may-31-overflow"
        ),
        pytest.param(datetime(2026, 1, 15), 1, datetime(2026, 2, 15), id="no-overflow"),
    ],
)
def test_add_months_rolls_over_like_php(start, months, expected):
    """``modify('+1 month')`` overflows into the next month, it does not clamp."""
    assert Wall.from_wall(start, UTC).add_months(months).wall == expected


def test_walls_are_compared_by_instant_not_by_wall_clock():
    """Two show days in different zones can be the same moment."""
    moment = utc_moment(2026, 7, 1, 18)

    berlin = Wall.from_instant(moment, BERLIN)
    tokyo = Wall.from_instant(moment, get_zone("Asia/Tokyo"))
    earlier = Wall.from_instant(moment - timedelta(minutes=1), BERLIN)

    assert berlin == tokyo
    assert berlin.wall != tokyo.wall
    assert earlier < berlin
    assert berlin >= earlier
