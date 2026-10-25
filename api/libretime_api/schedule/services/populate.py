"""
Show instance generation, ported from the legacy PHP show scheduler.

A *show* (``cc_show``) is defined by one or more *show days* (``cc_show_days``),
each holding a repeat rule, a wall clock start time and the timezone the show
was created in.  The concrete air times on the calendar are *show instances*
(``cc_show_instances``) and they are generated lazily: whenever something asks
for shows further out than the ``shows_populated_until`` preference - the
calendar UI, a playout schedule request, the autoplaylist builder - instances
are created up to that date.

This module is the port of that engine:

*   ``Application_Model_Show::createAndFillShowInstancesPastPopulatedUntilDate()``
    -> :func:`populate_show_instances`
*   ``Application_Service_ShowService::delegateInstanceCreation()``
    -> :func:`delegate_instance_creation`
*   ``Application_Service_ShowService::createWeeklyRepeatInstances()`` and
    ``createMonthlyRepeatInstances()`` -> :class:`InstanceBuilder`
*   ``Application_Service_SchedulerService::fillLinkedInstances()``
    -> :func:`fill_linked_instances`

The show *editing* paths of the legacy service (``updateShow``, which reuses
the same helpers to rewrite the instances of an existing show) are out of scope
here: they belong to the show editor migration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import IntEnum
from typing import Iterable

from django.db import connection, models, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from ...core.models import Preference
from ..models import Schedule, Show, ShowDays, ShowInstance, ShowRebroadcast
from ._datetime import (
    UTC,
    Wall,
    Zone,
    count_week_occurrence,
    get_zone,
    nth_weekday,
    valid_date,
)

logger = logging.getLogger(__name__)

# cc_pref keys the engine reads and writes.
SHOWS_POPULATED_UNTIL = "shows_populated_until"
DEFAULT_CROSSFADE_DURATION = "default_crossfade_duration"

# Repeat types as stored in cc_show_days.repeat_type, matching the legacy
# constants of legacy/application/services/ShowService.php.
NO_REPEAT = -1
REPEAT_WEEKLY = 0
REPEAT_BI_WEEKLY = 1
REPEAT_MONTHLY_MONTHLY = 2
REPEAT_MONTHLY_WEEKLY = 3
REPEAT_TRI_WEEKLY = 4
REPEAT_QUAD_WEEKLY = 5

#: The repeating every N days rules, with their interval in days.  In PHP these
#: are the ``DateInterval`` objects P7D, P14D, P21D and P28D.
WEEKLY_INTERVALS: dict[int, int] = {
    REPEAT_WEEKLY: 7,
    REPEAT_BI_WEEKLY: 14,
    REPEAT_TRI_WEEKLY: 21,
    REPEAT_QUAD_WEEKLY: 28,
}

MONTHLY_REPEATS = (REPEAT_MONTHLY_MONTHLY, REPEAT_MONTHLY_WEEKLY)

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class RepeatType(IntEnum):
    """Repeat type of a show day, mirroring the legacy constants."""

    NONE = NO_REPEAT
    WEEKLY = REPEAT_WEEKLY
    BI_WEEKLY = REPEAT_BI_WEEKLY
    MONTHLY_MONTHLY = REPEAT_MONTHLY_MONTHLY
    MONTHLY_WEEKLY = REPEAT_MONTHLY_WEEKLY
    TRI_WEEKLY = REPEAT_TRI_WEEKLY
    QUAD_WEEKLY = REPEAT_QUAD_WEEKLY


class ShowDayNotFound(Exception):
    """No show day row left to store the next populate date on."""


def parse_duration(value: str) -> tuple[int, int]:
    """
    Read a ``cc_show_days.duration`` value as hours and minutes.

    Legacy ``createUTCStartEndDateTime()`` splits on ``:`` and drops everything
    after the minutes part, so ``"02:30:30"`` means two hours thirty minutes.
    """
    parts = value.split(":")
    hours = int(parts[0]) if len(parts) > 0 and parts[0] else 0
    minutes = int(parts[1]) if len(parts) > 1 and parts[1] else 0

    return hours, minutes


def to_microseconds(value: str | timedelta | None) -> int:
    """
    Convert a duration into whole microseconds.

    Handles both the ``default_crossfade_duration`` preference - a number of
    seconds as a string - and the interval columns read through the ORM.
    Unparseable input becomes zero, which is what ``bcsub()`` makes of a non
    numeric operand.
    """
    if value is None:
        return 0

    if isinstance(value, timedelta):
        return (
            value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds
        )

    try:
        return int(Decimal(value) * 1_000_000)
    except (InvalidOperation, ValueError):
        return 0


def truncate_second(value: datetime) -> datetime:
    """
    Drop sub second precision.

    The legacy builder computes linked show item boundaries with microsecond
    precision but stores them through ``DEFAULT_TIMESTAMP_FORMAT``
    (``Y-m-d H:i:s``), which truncates.  The precision is kept in the carry
    over from one item to the next, only the stored row is truncated.
    """
    return value.replace(microsecond=0)


def to_instant(value: datetime) -> datetime:
    """
    ``value`` read from a ``cc_*`` timestamp column, as an instant.

    Those columns carry no time zone, so the ORM returns naive datetimes that
    are to be read as UTC - the legacy service wrote them with
    ``gmdate('Y-m-d H:i:s')``. Anything taken from them and used for
    arithmetic or written back gets its offset back here.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


class PreferenceStore:
    """Site wide ``cc_pref`` values the builder needs."""

    @staticmethod
    def get(key: str) -> str:
        value = (
            Preference.objects.filter(key=key, user__isnull=True)
            .values_list("value", flat=True)
            .first()
        )

        return value or ""

    @staticmethod
    def set(key: str, value: str) -> None:
        Preference.objects.update_or_create(
            key=key,
            user=None,
            defaults={"value": value},
        )

    @classmethod
    def shows_populated_until(cls) -> datetime | None:
        """Furthest instant instances were generated until, in UTC."""
        raw = cls.get(SHOWS_POPULATED_UNTIL)
        if not raw:
            return None

        try:
            parsed = datetime.strptime(raw, TIMESTAMP_FORMAT)
        except ValueError:
            logger.warning(
                "preference %s has unparsable value %r", SHOWS_POPULATED_UNTIL, raw
            )

            return None

        return parsed.replace(tzinfo=UTC)

    @classmethod
    def set_shows_populated_until(cls, instant: datetime) -> None:
        cls.set(
            SHOWS_POPULATED_UNTIL, instant.astimezone(UTC).strftime(TIMESTAMP_FORMAT)
        )

    @classmethod
    def default_crossfade(cls) -> int:
        """Default crossfade duration in microseconds, ``0`` when unset."""
        return to_microseconds(cls.get(DEFAULT_CROSSFADE_DURATION) or "0")


def show_days_in_range(start: datetime, end: datetime) -> models.QuerySet:
    """
    Show days that may need instances between ``start`` and ``end`` (UTC).

    Port of the legacy ``getShowDaysInRange()`` criteria, which Propel renders
    as::

        WHERE first_show < :end
          AND (last_show > :start OR last_show IS NULL)
          AND repeat_type <> -1

    Both comparisons happen at day precision, since ``first_show`` and
    ``last_show`` are ``date`` columns.  Non repeating show days never reach
    the builder: they are excluded here, exactly like the legacy query does.
    """
    return (
        ShowDays.objects.filter(first_show_on__lt=end.date())
        .filter(Q(last_show_on__gt=start.date()) | Q(last_show_on__isnull=True))
        .exclude(repeat_kind=NO_REPEAT)
        .select_related("show")
        .order_by("id")
    )


@dataclass
class InstanceBuilder:
    """
    Create ``cc_show_instances`` rows for a set of show days.

    Attributes:
        now: Instant the run started, used for the ``created`` column and for
            the "do not rebroadcast into the past" check.
        new_instance_ids: Created instance ids per show, in creation order.
        shows: Shows that were visited, in the order their show days appeared.
    """

    now: datetime
    new_instance_ids: dict[int, list[int]] = field(default_factory=dict)
    shows: dict[int, Show] = field(default_factory=dict)

    def build(self, show_days: Iterable[ShowDays], populate_until: datetime) -> None:
        """Create instances of ``show_days`` up to ``populate_until`` (UTC)."""
        for day in show_days:
            self.shows.setdefault(day.show_id, day.show)
            self.new_instance_ids.setdefault(day.show_id, [])

            repeat = RepeatType(day.repeat_kind)
            if repeat in WEEKLY_INTERVALS:
                self._create_weekly(day, populate_until, WEEKLY_INTERVALS[repeat])
            elif repeat in MONTHLY_REPEATS:
                self._create_monthly(day, populate_until)
            # RepeatType.NONE is filtered out by show_days_in_range().

    def create_single(
        self, day: ShowDays, populate_until: datetime
    ) -> ShowInstance | None:
        """
        Create the one instance of a show that does not repeat.

        Port of ``createNonRepeatingInstance()``.  The comparison is strict here -
        ``starts < populate_until`` - where the repeating rules use ``<=``; that is
        what the legacy code says, and a show starting exactly at the populated
        boundary waits for the next run.
        """
        zone = get_zone(day.timezone)
        occurrence = Wall.from_fields(day.first_show_on, day.start_time, zone)
        start, finish = utc_start_end(occurrence, day.duration)

        if start.utc >= to_instant(populate_until):
            return None

        instance = self._create_instance(day, start, finish)
        self._create_rebroadcast_instances(day, occurrence, instance.pk)

        return instance

    # -- repeat rules ---------------------------------------------------------

    def _create_weekly(
        self, day: ShowDays, populate_until: datetime, interval_days: int
    ) -> None:
        """
        Create the instances of a weekly, bi-, tri- or quad-weekly show day.

        Port of ``createWeeklyRepeatInstances()``.  The iteration walks the
        *wall clock* in the timezone of the show - like PHP's ``DatePeriod``
        with a ``P7D`` interval does - so a show keeps airing at the same local
        time across DST changes, and stops before ``populate_until`` the way
        ``DatePeriod`` stops before its end date, end excluded.
        """
        zone = get_zone(day.timezone)
        occurrence = _next_populate_start(day, zone)

        # DatePeriod end: last show day at local midnight, else populate until.
        if day.last_show_on:
            end: Wall = Wall.from_fields(day.last_show_on, time.min, zone)
        else:
            end = Wall.from_instant(populate_until, UTC)

        until = Wall.from_instant(populate_until, UTC)
        last_show = (
            Wall.from_fields(day.last_show_on, time.min, zone)
            if day.last_show_on
            else None
        )
        last_created: Wall | None = None

        while occurrence < end:
            start, finish = utc_start_end(occurrence, day.duration)

            # The instance must still be inside the requested window, and
            # start before the last show day.
            if start <= until and (last_show is None or start < last_show):
                last_created = start
                instance = self._create_instance(day, start, finish)
                self._create_rebroadcast_instances(day, occurrence, instance.pk)

            occurrence = occurrence.add_days(interval_days)

        # Unlike the monthly rule, the next populate date is only moved when
        # something was actually created.
        if last_created is not None:
            # Back to the show timezone before adding the interval, otherwise
            # the next date may land a day later. Must stay in the timezone the
            # show was created in.
            next_pop = last_created.in_zone(zone).add_days(interval_days)
            _set_next_pop_date(day, next_pop.date)

    def _create_monthly(self, day: ShowDays, populate_until: datetime) -> None:
        """
        Create the instances of a monthly show day.

        Port of ``createMonthlyRepeatInstances()``, covering both "every month
        on day N" (``REPEAT_MONTHLY_MONTHLY``) and "every month on the Nth
        weekday" (``REPEAT_MONTHLY_WEEKLY``).  Unlike the weekly rule the loop
        is driven by the *instant* of the next occurrence, and the end
        comparison is inclusive of nothing: it stops as soon as the occurrence
        reaches the end instant.
        """
        zone = get_zone(day.timezone)
        start = _next_populate_start(day, zone)

        if day.last_show_on:
            end: Wall = Wall.from_fields(day.last_show_on, time.min, zone)
        else:
            end = Wall.from_instant(populate_until, UTC)

        until = Wall.from_instant(populate_until, UTC)
        last_show = (
            Wall.from_fields(day.last_show_on, time.min, zone)
            if day.last_show_on
            else None
        )

        # Only used by REPEAT_MONTHLY_WEEKLY, computed unconditionally like
        # the legacy code does.
        ordinal, weekday = monthly_weekly_interval(
            Wall.from_fields(day.first_show_on, time.min, zone)
        )

        rebroadcasts = self._rebroadcasts(day)

        while start < end:
            instance_start, instance_end = utc_start_end(start, day.duration)

            if instance_start <= until and (
                last_show is None or instance_start < last_show
            ):
                instance = self._create_instance(day, instance_start, instance_end)
                self._create_rebroadcast_instances(
                    day, start, instance.pk, rebroadcasts=rebroadcasts
                )

            if day.repeat_kind == REPEAT_MONTHLY_WEEKLY:
                # The legacy code takes the year and month of the instance it
                # just created *in UTC*, adds a month, and looks for the Nth
                # weekday of that month.
                base = Wall(
                    datetime(instance_start.wall.year, instance_start.wall.month, 1),
                    UTC,
                ).add_months(1)
                start = next_monthly_weekly_date(
                    base, zone, day.start_time, ordinal, weekday
                )
            else:
                start = next_monthly_monthly_date(start, zone, day.start_time)

        # Unconditional: the next run starts at the first occurrence past the
        # window, even when nothing was created this time.
        _set_next_pop_date(day, start.date)

    # -- instance rows --------------------------------------------------------

    def _create_instance(
        self,
        day: ShowDays,
        start: Wall,
        finish: Wall,
        *,
        rebroadcast_of: int | None = None,
    ) -> ShowInstance:
        """Insert one ``cc_show_instances`` row, as the legacy ``save()`` did."""
        rebroadcast = rebroadcast_of is not None

        instance = ShowInstance(
            show_id=day.show_id,
            starts_at=start.utc,
            ends_at=finish.utc,
            # Recorded rebroadcasts air the recording of the original instance
            # and are never recorded themselves.
            record_enabled=0 if rebroadcast else day.record_enabled,
            rebroadcast=1 if rebroadcast else 0,
            instance_id=rebroadcast_of,
            description="",
            filled_time=timedelta(0),
            created_at=self.now,
            modified=False,
            auto_playlist_built=False,
        )
        instance.save()

        self.new_instance_ids.setdefault(day.show_id, []).append(instance.pk)

        return instance

    @staticmethod
    def _rebroadcasts(day: ShowDays) -> list[ShowRebroadcast]:
        """Rebroadcast rules of the show, the legacy ``$this->isRebroadcast``."""
        return list(ShowRebroadcast.objects.filter(show_id=day.show_id).order_by("id"))

    def _create_rebroadcast_instances(
        self,
        day: ShowDays,
        show_start: Wall,
        original_id: int,
        *,
        rebroadcasts: list[ShowRebroadcast] | None = None,
    ) -> None:
        """
        Create the rebroadcast instances of one instance.

        Port of ``createRebroadcastInstances()``: one instance per
        ``cc_show_rebroadcast`` row, ``day_offset`` days after the show date at
        the rebroadcast ``start_time``, in the show timezone.  Rebroadcasts in
        the past are skipped.
        """
        if rebroadcasts is None:
            rebroadcasts = self._rebroadcasts(day)

        now = Wall.from_instant(self.now, UTC)

        for rebroadcast in rebroadcasts:
            days = int(str(rebroadcast.day_offset).split(" ")[0] or 0)
            offset = (days, rebroadcast.start_time.hour, rebroadcast.start_time.minute)
            start, finish = utc_start_end(show_start, day.duration, offset=offset)

            if start > now:
                self._create_instance(day, start, finish, rebroadcast_of=original_id)


def _next_populate_start(day: ShowDays, zone: Zone) -> Wall:
    """
    First occurrence to generate instances for.

    Port of ``getNextRepeatingPopulateStartDateTime()``: the stored next
    populate date when the show already generated instances, else the first
    show date.  Both are combined with the show start time in the show
    timezone, so a show moved forward in the calendar resumes where it stopped.
    """
    start_day = day.repeat_next_on or day.first_show_on

    return Wall.from_fields(start_day, day.start_time, zone)


def _set_next_pop_date(day: ShowDays, next_date: date) -> None:
    """
    Store where instance generation resumes for this show day.

    Port of ``setNextRepeatingShowDate()``, including its lookup: the legacy
    code finds the row by show, week day and "is repeating" instead of by
    primary key, and updates the first match.
    """
    target = (
        ShowDays.objects.filter(show_id=day.show_id)
        .exclude(repeat_kind=NO_REPEAT)
        .filter(
            Q(week_day=day.week_day)
            if day.week_day is not None
            else Q(week_day__isnull=True)
        )
        .order_by("id")
        .first()
    )
    if target is None:
        raise ShowDayNotFound(
            f"no repeating show day of show {day.show_id} on day {day.week_day} "
            "to store the next populate date on"
        )

    target.repeat_next_on = next_date
    target.save(update_fields=["repeat_next_on"])


def monthly_weekly_interval(start: Wall) -> tuple[int, int]:
    """
    Which weekday occurrence a show repeats on.

    Port of ``getMonthlyWeeklyRepeatInterval()``: the first occurrence of the
    weekday is looked up the way PHP's ``strtotime("Monday 2026-01")`` does -
    in the default timezone of the legacy service, which is UTC - and the day
    of month is then counted down by weeks.

    Returns:
        The ``nth`` occurrence of the weekday - a month holds any given weekday
        at most five times, so it is always 1..5 - and the Python weekday number.
    """
    wall = start.wall
    weekday = wall.weekday()
    first_occurrence = nth_weekday(1, weekday, wall.year, wall.month).day

    return count_week_occurrence(wall.day, first_occurrence), weekday


def next_monthly_monthly_date(start: Wall, zone: Zone, start_time: time) -> Wall:
    """
    Next "same day of the month" occurrence.

    Port of ``getNextMonthlyMonthlyRepeatDate()``: months without that day of
    month are skipped, so a show on the 31st only airs in months with 31 days.
    """
    current = Wall.from_wall(datetime(start.wall.year, start.wall.month, 1), zone)

    while True:
        # PHP advances $dt in place on *every* iteration, including the months
        # that do not have the day of month of $start.
        current = current.add_months(1)
        if valid_date(current.wall.year, current.wall.month, start.wall.day):
            break

    return current.set_date(
        current.wall.year, current.wall.month, start.wall.day
    ).set_time(start_time.hour, start_time.minute, start_time.second)


def next_monthly_weekly_date(
    base: Wall,
    zone: Zone,
    start_time: time,
    ordinal: int,
    weekday: int,
) -> Wall:
    """
    Next "Nth weekday of the month" occurrence.

    Port of ``getNextMonthlyWeeklyRepeatDate()``.  Two legacy quirks are kept
    as they are, because both decide where the instances land:

    *   ``date_create("$ordinal $weekday of $Month $Year")`` runs in the
        default timezone of the legacy service (UTC) and is only then converted
        to the show timezone, so in zones west of UTC the weekday shifts back
        one day - a "first Monday" show created in New York airs on Sundays.
    *   when a month has no ``ordinal`` weekday PHP's date parsing rolls into
        the next month; the month of the result is compared with the searched
        month and, when they differ, the month is skipped.  A "fifth Thursday"
        show therefore skips the months without one.
    """
    searched = Wall.from_wall(datetime(base.wall.year, base.wall.month, 1), zone)

    while True:
        occurrence = nth_weekday(
            ordinal, weekday, searched.wall.year, searched.wall.month
        )
        # date_create() in the default timezone (UTC), then setTimezone($zone).
        candidate = Wall.from_instant(
            datetime(occurrence.year, occurrence.month, occurrence.day, tzinfo=UTC),
            zone,
        )

        if candidate.wall.month == searched.wall.month:
            break

        searched = searched.add_months(1)

    return candidate.set_time(start_time.hour, start_time.minute, start_time.second)


def utc_start_end(
    start: Wall,
    duration: str,
    *,
    offset: tuple[int, int, int] | None = None,
) -> tuple[Wall, Wall]:
    """
    Instance boundaries in UTC of one occurrence.

    Port of ``createUTCStartEndDateTime()``.  The duration is added to the wall
    clock, so a show airing across the autumn transition is one hour longer in
    absolute time, exactly like the legacy rows.  ``offset`` is the
    ``(days, hours, minutes)`` rebroadcast offset and replaces the time of the
    occurrence.
    """
    hours, minutes = parse_duration(duration)

    if offset is not None:
        offset_days, offset_hours, offset_minutes = offset
        start = Wall.from_wall(
            datetime.combine(start.date, time(offset_hours, offset_minutes)),
            start.zone,
        ).add_days(offset_days)

    finish = start.add_wall_time(hours=hours, minutes=minutes)

    return start.in_zone(UTC), finish.in_zone(UTC)


def delegate_instance_creation(
    until: datetime,
    *,
    now: datetime | None = None,
    populated_until: datetime | None = None,
) -> dict[int, list[int]]:
    """
    Create the show instances needed to cover ``until``.

    Port of ``Application_Service_ShowService::delegateInstanceCreation()``
    with ``$daysAdded = null`` and ``$fillInstances = true``, which is the
    lazy calendar generation path.

    Args:
        until: Instant the calendar has to be built until, timezone aware.
        now: Instant of the run, defaults to ``timezone.now()``.
        populated_until: Current ``shows_populated_until`` preference, ``None``
            when the application never populated shows.

    Returns:
        Created instance ids per show id, in creation order.
    """
    now = now or timezone.now()
    if populated_until is None:
        # The legacy code writes the preference here and overwrites it with
        # ``until`` right after, so the current instant is good enough.
        populated_until = now

    builder = InstanceBuilder(now=now)
    builder.build(show_days_in_range(populated_until, until), populate_until=until)

    for show in builder.shows.values():
        if show.linked:
            fill_linked_instances(
                show,
                builder.new_instance_ids.get(show.pk, []),
                populated_until=populated_until,
                now=now,
            )

    return builder.new_instance_ids


def populate_show_instances(until: datetime) -> int:
    """
    Make the calendar contain every show instance until ``until``.

    Port of ``Application_Model_Show::createAndFillShowInstancesPastPopulatedUntilDate()``.
    Instances are only generated when ``until`` is further away than the
    ``shows_populated_until`` preference, and a whole run happens in one
    serializable transaction: without that, two concurrent requests - somebody
    browsing the calendar while playout asks for a schedule, for example -
    would both generate the same instances.  A rolled back race is not an
    error, the other transaction did the same work.

    Args:
        until: Instant the calendar has to be built until, timezone aware.

    Returns:
        Number of show instances created.
    """
    created: dict[int, list[int]] = {}

    try:
        with transaction.atomic():
            # Same isolation level as the legacy transaction. Has to be the
            # first statement of the transaction to be taken into account.
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")

            populated_until = PreferenceStore.shows_populated_until()
            if populated_until is not None and populated_until >= until:
                return 0

            created = delegate_instance_creation(until, populated_until=populated_until)
            PreferenceStore.set_shows_populated_until(until)
    except Exception:  # pylint: disable=broad-except
        logger.warning(
            "Did not create show instances due to transaction error. This is "
            "usually safe and caused by two concurrent transactions.",
            exc_info=True,
        )

        return 0

    count = sum(len(ids) for ids in created.values())
    if count:
        logger.info("created %d show instances until %s", count, until)

    return count


def fill_linked_instances(
    show: Show,
    instance_ids: list[int],
    *,
    populated_until: datetime,
    now: datetime | None = None,
) -> None:
    """
    Copy a linked show's content into its freshly created instances.

    Port of ``Application_Service_SchedulerService::fillLinkedInstances()``.
    The content of the latest instance that aired before ``populated_until`` is
    replayed into every new instance: same items, same clip lengths, same
    order, shifted to the new air time and overlapped by the crossfade
    duration.
    """
    if not instance_ids:
        return

    now = now or timezone.now()

    template = (
        ShowInstance.objects.filter(
            show=show, starts_at__lt=populated_until.astimezone(UTC)
        )
        .exclude(pk__in=instance_ids)
        .order_by("-starts_at")
        .first()
    )
    items = (
        []
        if template is None
        else list(Schedule.objects.filter(instance=template).order_by("starts_at"))
    )
    # The legacy code reads cc_show_instances.time_filled of the instance the
    # copied rows belong to, and an instance without content is not filled.
    time_filled = timedelta(0) if template is None else template.filled_time

    with transaction.atomic():
        if items:
            crossfade = timedelta(microseconds=PreferenceStore.default_crossfade())

            for instance_id in instance_ids:
                # Clear first: a programming error elsewhere must not leave
                # duplicated content behind.
                Schedule.objects.filter(instance_id=instance_id).delete()

                instance = ShowInstance.objects.get(pk=instance_id)
                rows = _linked_instance_rows(instance, items, crossfade, instance_id)
                Schedule.objects.bulk_create(rows)
                update_schedule_status(instance, now=now)

        ShowInstance.objects.filter(pk__in=instance_ids).update(
            filled_time=time_filled,
            last_scheduled_at=now,
        )


def _linked_instance_rows(
    instance: ShowInstance,
    items: list[Schedule],
    crossfade: timedelta,
    instance_id: int,
) -> list[Schedule]:
    """
    Build the ``cc_schedule`` rows of one linked show instance.

    The first item starts when the instance starts, every next item starts
    ``crossfade`` before the previous one ended.  ``fade_in``, ``fade_out``,
    cues and position are copied verbatim from the template item.
    """
    rows = []
    # Read from a column without time zone, see to_instant().
    next_start = to_instant(instance.starts_at)

    for item in items:
        end = next_start + timedelta(microseconds=to_microseconds(item.length))

        rows.append(
            Schedule(
                starts_at=truncate_second(next_start),
                ends_at=truncate_second(end),
                length=item.length,
                fade_in=item.fade_in,
                fade_out=item.fade_out,
                cue_in=item.cue_in,
                cue_out=item.cue_out,
                file_id=item.file_id,
                stream_id=item.stream_id,
                instance_id=instance_id,
                position=item.position,
                # Freshly copied content is not on air yet. The legacy insert
                # lists neither media_item_played nor broadcasted nor
                # playout_status, so all three get their column default: false,
                # 0 and 1 (inside the instance). Django would insert NULL for
                # the nullable media_item_played, hence the explicit value.
                broadcasted=0,
                played=False,
                position_status=Schedule.PositionStatus.INSIDE,
            )
        )

        next_start = end - crossfade

    return rows


def update_schedule_status(instance: ShowInstance, *, now: datetime) -> None:
    """
    Recompute the schedule statistics of a show instance.

    Port of ``CcShowInstances::updateScheduleStatus()``: the total filled time
    of the instance, and the position of every item against the end of the
    instance - inside, crossing the end (such an item is still played, it just
    runs into the next show) or past it ("overbooked", never played).  Items
    marked as fillers stay as they are.
    """
    filled = Schedule.objects.filter(instance=instance).aggregate(total=Sum("length"))[
        "total"
    ]
    # Read from a column without time zone, see to_instant().
    ends_at = to_instant(instance.ends_at)
    instance.filled_time = filled if filled is not None else timedelta(0)
    instance.save(update_fields=["filled_time"])

    statuses = {
        Schedule.PositionStatus.INSIDE: Q(ends_at__lte=ends_at),
        Schedule.PositionStatus.BOUNDARY: Q(starts_at__lt=ends_at)
        & Q(ends_at__gt=ends_at),
        Schedule.PositionStatus.OUTSIDE: Q(starts_at__gt=ends_at),
    }
    for status, condition in statuses.items():
        Schedule.objects.filter(instance=instance, position_status__gte=0).filter(
            condition
        ).update(position_status=status)

    instance.last_scheduled_at = now
    instance.save(update_fields=["last_scheduled_at"])
