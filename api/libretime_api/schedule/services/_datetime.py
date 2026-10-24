"""Helpers reproducing the PHP ``DateTime`` semantics the legacy scheduler relies on.

The show instance builder being ported is written against PHP's ``DateTime``,
which differs from :class:`datetime.datetime` in two ways that decide whether
``cc_show_instances`` rows land at the right instant:

*   Month, day and time mutations (``DateTime::add()``, ``setDate()``,
    ``setTime()``) change the **wall clock** first and re-resolve the UTC
    offset afterwards.  Adding seven days to ``21:00`` in ``Europe/Berlin``
    across the spring transition stays at ``21:00`` local (two hours shorter in
    absolute time), and a wall clock time inside the DST gap is pushed forward
    to the first existing time of that day.
*   Comparisons between two ``DateTime`` objects are made on the **absolute**
    instant, whatever timezone each object happens to be displayed in.

:class:`Wall` stores a naive wall clock together with the timezone it is
displayed in, which is exactly the state a PHP ``DateTime`` object keeps, and
implements both behaviours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import total_ordering
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

#: A resolved timezone, as stored in a ``cc_show_days.timezone`` column.
Zone = ZoneInfo | timezone


def get_zone(name: str | None) -> Zone:
    """
    Resolve a timezone name as stored in ``cc_show_days.timezone``.

    Args:
        name: Timezone name, e.g. ``Europe/Zurich``. Empty means UTC.

    Returns:
        The matching zone info object.

    Raises:
        ValueError: If the timezone is unknown to the system tz database.
    """
    if not name:
        return UTC

    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError, OSError) as exc:
        raise ValueError(f"unknown timezone: {name}") from exc


@total_ordering
@dataclass(frozen=True)
class Wall:
    """
    A wall clock time inside a timezone, matching PHP's ``DateTime``.

    Attributes:
        wall: Naive wall clock time, as PHP would format it.
        zone: Timezone the wall clock is displayed in.
    """

    wall: datetime
    zone: Zone

    @classmethod
    def from_wall(cls, wall: datetime, zone: Zone) -> Wall:
        """
        Build a wall clock in ``zone``, normalising DST gaps.

        Mirrors ``new DateTime($wall, $zone)``: a time inside the spring
        transition gap is moved forward by the length of the gap, an ambiguous
        time in the autumn transition keeps its first occurrence.
        """
        naive = wall.replace(tzinfo=None)
        instant = naive.replace(tzinfo=zone).astimezone(UTC)

        return cls(instant.astimezone(zone).replace(tzinfo=None), zone)

    @classmethod
    def from_instant(cls, instant: datetime, zone: Zone) -> Wall:
        """Build a wall clock from an absolute moment (``setTimezone()``)."""
        local = instant.astimezone(zone)

        return cls(local.replace(tzinfo=None), zone)

    @classmethod
    def from_fields(cls, day: date, clock: time, zone: Zone) -> Wall:
        """Build a wall clock from a ``date`` and a ``time`` column pair."""
        return cls.from_wall(datetime.combine(day, clock), zone)

    @property
    def instant(self) -> datetime:
        """Absolute moment of this wall clock, as timezone aware UTC."""
        return self.wall.replace(tzinfo=self.zone).astimezone(UTC)

    @property
    def utc(self) -> datetime:
        """Absolute moment, shown the way ``setTimezone(UTC)`` would show it."""
        return self.instant

    @property
    def date(self) -> date:
        """Date part of the wall clock."""
        return self.wall.date()

    def in_zone(self, zone: Zone) -> Wall:
        """Convert to another timezone, keeping the absolute moment."""
        return Wall.from_instant(self.instant, zone)

    def add_days(self, days: int) -> Wall:
        """Add days to the wall clock (PHP ``DateInterval`` of ``PnD``)."""
        return Wall.from_wall(self.wall + timedelta(days=days), self.zone)

    def add_months(self, months: int) -> Wall:
        """
        Add months to the wall clock (PHP ``DateInterval`` of ``PnM``).

        PHP keeps the day *number*, so overflowing days roll into the next
        month: ``2026-01-31 + P1M`` is ``2026-03-03``.
        """
        return Wall.from_wall(shift_months(self.wall, months), self.zone)

    def add_wall_time(
        self,
        hours: int = 0,
        minutes: int = 0,
        seconds: int = 0,
    ) -> Wall:
        """Add a duration to the wall clock (PHP ``DateInterval`` of ``PT..``)."""
        return Wall.from_wall(
            self.wall + timedelta(hours=hours, minutes=minutes, seconds=seconds),
            self.zone,
        )

    def set_time(self, hour: int, minute: int, second: int = 0) -> Wall:
        """Set the wall clock time (PHP ``DateTime::setTime``)."""
        return Wall.from_wall(
            self.wall.replace(hour=hour, minute=minute, second=second, microsecond=0),
            self.zone,
        )

    def set_date(self, year: int, month: int, day: int) -> Wall:
        """Set the wall clock date (PHP ``DateTime::setDate``)."""
        first = self.wall.replace(year=year, month=month, day=1)

        return Wall.from_wall(first + timedelta(days=day - 1), self.zone)

    def __str__(self) -> str:
        return self.wall.strftime("%Y-%m-%d %H:%M:%S")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Wall):
            return self.instant == other.instant

        return NotImplemented

    def __lt__(self, other: object) -> bool:
        if isinstance(other, Wall):
            return self.instant < other.instant

        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.instant)


def shift_months(wall: datetime, months: int) -> datetime:
    """Shift a naive ``wall`` by whole months, keeping the day number like PHP."""
    month = wall.month - 1 + months
    year = wall.year + month // 12
    month = month % 12 + 1
    first = wall.replace(year=year, month=month, day=1)

    return first + timedelta(days=wall.day - 1)


def valid_date(year: int, month: int, day: int) -> bool:
    """PHP ``checkdate()``: whether the year/month/day triple exists."""
    try:
        date(year, month, day)
    except ValueError:
        return False

    return True


def count_week_occurrence(day_of_month: int, first_occurrence: int) -> int:
    """
    Which occurrence of its weekday a day of month is.

    Mirrors the loop in
    ``Application_Service_ShowService::getMonthlyWeeklyRepeatInterval``:
    ``day_of_month`` counts down by weeks until it is before ``first_occurrence``,
    the day of the month of the weekday's first occurrence.
    """
    count = 0
    remaining = day_of_month
    while remaining >= first_occurrence:
        count += 1
        remaining -= 7

    return count


def nth_weekday(nth: int, weekday: int, year: int, month: int) -> date:
    """
    Date of the ``nth`` ``weekday`` of a month.

    Mirrors ``strtotime("$ordinal $weekday of $Month $Year")`` evaluated in UTC:
    when the month has no ``nth`` weekday the count rolls into the next month,
    exactly like PHP does.
    """
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7

    return first + timedelta(days=offset + (nth - 1) * 7)
