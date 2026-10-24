"""
Show instance generation, against rows the legacy builder is known to produce.

The shows defined here are the ones of ``tools/schedule-equivalence/seed.sql``,
the horizon and the run instant are those of the harness run they were compared
at, and the expected numbers come out of ``out/php/*.csv`` - the rows the
legacy PHP service wrote. A failure here therefore means the port stopped
agreeing with the original, not that two Python views of it drifted apart.

Datetimes are compared through ``to_instant()``: in production the ``cc_*``
columns carry no time zone and the ORM answers naive, while the test database
creates them as ``timestamptz`` and answers aware. The helper normalises both.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from django.test import TestCase, TransactionTestCase

from ....core.models import Preference
from ...models import Schedule, Show, ShowDays, ShowInstance, ShowRebroadcast
from ...services.populate import (
    DEFAULT_CROSSFADE_DURATION,
    PreferenceStore,
    RepeatType,
    delegate_instance_creation,
    populate_show_instances,
    show_days_in_range,
    to_instant,
)

# The horizon and the wall clock of the harness run the expectations came from.
# The instant matters beyond bookkeeping: rebroadcast instances are only
# created for instances that have not aired yet.
UNTIL = datetime(2026, 12, 31, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
POPULATED_UNTIL = datetime(2026, 1, 1, tzinfo=timezone.utc)

UTC = timezone.utc


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
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)


def create_show(name: str, *, linked: bool = False) -> Show:
    return Show.objects.create(
        name=name,
        linked=linked,
        linkable=not linked,
        auto_playlist_enabled=False,
        auto_playlist_repeat=False,
        override_intro_playlist=False,
        override_outro_playlist=False,
    )


def create_day(
    show: Show,
    *,
    first: date,
    weekday: int,
    kind: int,
    tz: str = "UTC",
    start: time = time(1, 0),
    duration: str = "01:00",
    last: date | None = None,
    record: int = 0,
) -> ShowDays:
    return ShowDays.objects.create(
        show=show,
        first_show_on=first,
        last_show_on=last,
        start_time=start,
        timezone=tz,
        duration=duration,
        record_enabled=record,
        week_day=weekday,
        repeat_kind=kind,
        repeat_next_on=first,
    )


class BuilderTest(TestCase):
    """One run of the builder over the whole legacy fixture matrix."""

    @classmethod
    def setUpTestData(cls):
        # A weekly, plain: the baseline.
        cls.weekly_utc = create_show("A weekly utc")
        create_day(
            cls.weekly_utc,
            first=date(2026, 1, 5),
            weekday=ShowDays.WeekDay.MONDAY,
            kind=RepeatType.WEEKLY,
        )

        # B: weekly, Europe/Berlin 21:00 - both transitions in one year.
        cls.weekly_berlin = create_show("B weekly berlin")
        create_day(
            cls.weekly_berlin,
            first=date(2026, 1, 7),
            weekday=ShowDays.WeekDay.WEDNESDAY,
            kind=RepeatType.WEEKLY,
            tz="Europe/Berlin",
            start=time(21, 0),
            duration="02:00",
        )

        # C: bi weekly, New York, with a hard end date.
        cls.bi_weekly_ny = create_show("C biweekly ny")
        create_day(
            cls.bi_weekly_ny,
            first=date(2026, 1, 2),
            last=date(2026, 6, 26),
            weekday=ShowDays.WeekDay.FRIDAY,
            kind=RepeatType.BI_WEEKLY,
            tz="America/New_York",
            start=time(22, 0),
            duration="01:30",
        )

        # D: quad weekly, Tokyo.
        cls.quad_weekly_tokyo = create_show("D quadweekly tokyo")
        create_day(
            cls.quad_weekly_tokyo,
            first=date(2026, 1, 4),
            weekday=ShowDays.WeekDay.SUNDAY,
            kind=RepeatType.QUAD_WEEKLY,
            tz="Asia/Tokyo",
            start=time(9, 30),
            duration="00:45",
        )

        # E: tri weekly, Berlin.
        cls.tri_weekly_berlin = create_show("E triweekly berlin")
        create_day(
            cls.tri_weekly_berlin,
            first=date(2026, 2, 25),
            weekday=ShowDays.WeekDay.WEDNESDAY,
            kind=RepeatType.TRI_WEEKLY,
            tz="Europe/Berlin",
            start=time(2, 30),
            duration="00:30",
        )

        # F: monthly on the 31st.
        cls.monthly_31 = create_show("F monthly 31")
        create_day(
            cls.monthly_31,
            first=date(2026, 1, 31),
            weekday=ShowDays.WeekDay.TUESDAY,
            kind=RepeatType.MONTHLY_MONTHLY,
            start=time(17, 0),
        )

        # G: monthly on the 29th, February 2026 does not have one.
        cls.monthly_29 = create_show("G monthly 29")
        create_day(
            cls.monthly_29,
            first=date(2026, 1, 29),
            weekday=ShowDays.WeekDay.SATURDAY,
            kind=RepeatType.MONTHLY_MONTHLY,
            tz="Europe/Berlin",
            start=time(23, 30),
        )

        # H: monthly first Monday, created in New York.
        cls.monthly_first_monday = create_show("H monthly 1st mon ny")
        create_day(
            cls.monthly_first_monday,
            first=date(2026, 1, 5),
            weekday=ShowDays.WeekDay.MONDAY,
            kind=RepeatType.MONTHLY_WEEKLY,
            tz="America/New_York",
            start=time(20, 0),
            duration="02:00",
        )

        # I: monthly fifth Thursday.
        cls.monthly_fifth_thursday = create_show("I monthly 5th thu")
        create_day(
            cls.monthly_fifth_thursday,
            first=date(2026, 1, 29),
            weekday=ShowDays.WeekDay.THURSDAY,
            kind=RepeatType.MONTHLY_WEEKLY,
            start=time(20, 0),
        )

        # J: recorded weekly show with two rebroadcasts.
        cls.rebroadcast = create_show("J rebroadcast")
        create_day(
            cls.rebroadcast,
            first=date(2026, 1, 6),
            weekday=ShowDays.WeekDay.TUESDAY,
            kind=RepeatType.WEEKLY,
            start=time(14, 0),
            duration="02:00",
            record=1,
        )
        ShowRebroadcast.objects.create(
            show=cls.rebroadcast, day_offset="0", start_time=time(23, 30)
        )
        ShowRebroadcast.objects.create(
            show=cls.rebroadcast, day_offset="1", start_time=time(10, 0)
        )

        # K: linked show, with the content every new instance is filled from.
        cls.linked = create_show("K linked", linked=True)
        create_day(
            cls.linked,
            first=date(2026, 1, 7),
            weekday=ShowDays.WeekDay.WEDNESDAY,
            kind=RepeatType.WEEKLY,
            start=time(12, 0),
            duration="03:00",
        )
        cls.template = ShowInstance.objects.create(
            show=cls.linked,
            starts_at=utc_moment(2025, 12, 31, 12),
            ends_at=utc_moment(2025, 12, 31, 15),
            filled_time=timedelta(seconds=6, milliseconds=875),
            created_at=utc_moment(2025, 12, 1),
            description="",
            record_enabled=0,
            rebroadcast=0,
            modified=False,
            auto_playlist_built=False,
        )
        for position, (starts, ends, length) in enumerate(
            [
                (
                    utc_moment(2025, 12, 31, 12),
                    utc_moment(2025, 12, 31, 12, 0, 3),
                    timedelta(seconds=3, milliseconds=250),
                ),
                (
                    utc_moment(2025, 12, 31, 12, 0, 3, 250000),
                    utc_moment(2025, 12, 31, 12, 0, 5, 750000),
                    timedelta(seconds=2, milliseconds=500),
                ),
                (
                    utc_moment(2025, 12, 31, 12, 0, 4, 750000),
                    utc_moment(2025, 12, 31, 12, 0, 5, 875000),
                    timedelta(seconds=1, milliseconds=125),
                ),
            ]
        ):
            Schedule.objects.create(
                instance=cls.template,
                starts_at=starts,
                ends_at=ends,
                length=length,
                fade_in=time(0, 0, 1),
                fade_out=time(0, 0, 1),
                cue_in=timedelta(milliseconds=500),
                cue_out=timedelta(milliseconds=500),
                position=position,
                position_status=Schedule.PositionStatus.INSIDE,
                broadcasted=0,
                played=False,
            )

        # L: one show repeating on two days.
        cls.two_days = create_show("L two days")
        create_day(
            cls.two_days,
            first=date(2026, 1, 5),
            weekday=ShowDays.WeekDay.MONDAY,
            kind=RepeatType.WEEKLY,
            start=time(10, 0),
        )
        create_day(
            cls.two_days,
            first=date(2026, 1, 8),
            weekday=ShowDays.WeekDay.THURSDAY,
            kind=RepeatType.WEEKLY,
            start=time(10, 0),
        )

        # M: non repeating, must not be picked up by this path at all.
        cls.nonrepeating = create_show("M nonrepeating")
        create_day(
            cls.nonrepeating,
            first=date(2026, 2, 3),
            weekday=ShowDays.WeekDay.TUESDAY,
            kind=RepeatType.NONE,
            start=time(20, 0),
        )

        PreferenceStore.set_shows_populated_until(POPULATED_UNTIL)
        # Same value the harness seeds, so the copied items overlap.
        PreferenceStore.set(DEFAULT_CROSSFADE_DURATION, "1.5")

        cls.created = delegate_instance_creation(
            UNTIL, now=NOW, populated_until=POPULATED_UNTIL
        )

    def instances(self, show: Show) -> list[ShowInstance]:
        return list(ShowInstance.objects.filter(show=show).order_by("starts_at"))

    def starts(self, show: Show) -> list[datetime]:
        return [to_instant(instance.starts_at) for instance in self.instances(show)]

    def test_total_number_of_created_instances(self):
        """404 instances, which is what the legacy builder created."""
        self.assertEqual(sum(len(ids) for ids in self.created.values()), 404)
        self.assertEqual(
            ShowInstance.objects.count(), 405
        )  # 404 plus the template instance

    def test_weekly_show_in_utc(self):
        self.assertEqual(len(self.starts(self.weekly_utc)), 52)
        self.assertEqual(self.starts(self.weekly_utc)[0], utc_moment(2026, 1, 5, 1))
        self.assertEqual(self.starts(self.weekly_utc)[-1], utc_moment(2026, 12, 28, 1))

    def test_weekly_show_holds_the_local_wall_clock_across_a_transition(self):
        """21:00 in Berlin stays 21:00 local, so its instant moves by an hour."""
        starts = self.starts(self.weekly_berlin)

        self.assertEqual(len(starts), 52)
        # 2026-03-18 is before the transition, 2026-04-01 after it.
        self.assertIn(utc_moment(2026, 3, 18, 20), starts)
        self.assertIn(utc_moment(2026, 4, 1, 19), starts)

    def test_repeating_show_stops_at_its_end_date(self):
        starts = self.starts(self.bi_weekly_ny)

        self.assertEqual(len(starts), 13)
        self.assertEqual(starts[0], utc_moment(2026, 1, 3, 3))  # 22:00 New York
        self.assertEqual(starts[-1].date(), date(2026, 6, 20))

    def test_quad_weekly_show_in_another_hemisphere(self):
        starts = self.starts(self.quad_weekly_tokyo)

        self.assertEqual(len(starts), 13)
        self.assertEqual(starts[0], utc_moment(2026, 1, 4, 0, 30))  # 09:30 Tokyo
        self.assertEqual(starts[1] - starts[0], timedelta(days=28))

    def test_monthly_show_on_the_31st_skips_shorter_months(self):
        days = [start.date() for start in self.starts(self.monthly_31)]

        self.assertEqual(
            days,
            [
                date(2026, 1, 31),
                date(2026, 3, 31),
                date(2026, 5, 31),
                date(2026, 7, 31),
                date(2026, 8, 31),
                date(2026, 10, 31),
            ],
        )

    def test_monthly_show_on_the_29th_skips_february(self):
        self.assertEqual(len(self.starts(self.monthly_29)), 11)

        months = [start.month for start in self.starts(self.monthly_29)]
        self.assertNotIn(2, months)

    def test_monthly_first_monday_show_in_new_york_airs_on_sunday(self):
        """
        The occurrence is counted in UTC, so local time lands on Sunday.

        The legacy builder produced 2026-01-06 01:00 UTC and 2026-02-02 01:00
        UTC for this show, which is 20:00 on Monday and Sunday in New York
        respectively - the second one is the case that looks wrong on the
        calendar and is exactly what users of the legacy service saw.
        """
        starts = self.starts(self.monthly_first_monday)

        self.assertEqual(starts[0], utc_moment(2026, 1, 6, 1))
        self.assertEqual(starts[1], utc_moment(2026, 2, 2, 1))

        new_york = starts[1].astimezone(ZoneInfo("America/New_York"))
        self.assertEqual(new_york.strftime("%A %H:%M"), "Sunday 20:00")

    def test_monthly_fifth_thursday_show_only_airs_in_months_with_five(self):
        days = [start.date() for start in self.starts(self.monthly_fifth_thursday)]

        self.assertEqual(
            days,
            [
                date(2026, 1, 29),
                date(2026, 4, 30),
                date(2026, 7, 30),
                date(2026, 10, 29),
            ],
        )

    def test_rebroadcasts_are_created_for_instances_that_have_not_aired(self):
        """
        Rebroadcast instances exist only for primaries after the run instant,
        which is why 52 primaries of this show produce 20 rebroadcasts and not
        104: the legacy builder does not schedule repeats of shows that already
        aired.
        """
        primaries = self.instances(self.rebroadcast)
        children = [i for i in primaries if i.rebroadcast == 1]

        self.assertEqual(len([i for i in primaries if i.rebroadcast == 0]), 52)
        self.assertEqual(len(children), 20)

        first_primary = next(
            i for i in primaries if to_instant(i.starts_at) > NOW and i.rebroadcast == 0
        )
        repeats = [i for i in children if i.instance_id == first_primary.pk]

        self.assertEqual(len(repeats), 2)
        self.assertEqual(
            sorted(to_instant(i.starts_at) for i in repeats),
            [utc_moment(2026, 10, 27, 23, 30), utc_moment(2026, 10, 28, 10)],
        )
        for repeat in repeats:
            self.assertEqual(
                to_instant(repeat.ends_at) - to_instant(repeat.starts_at),
                timedelta(hours=2),
            )
            self.assertEqual(repeat.record_enabled, 0)

    def test_linked_show_instances_are_filled_with_the_latest_content(self):
        """Item boundaries include the crossfade overlap, truncated on storage."""
        instance = next(
            i
            for i in self.instances(self.linked)
            if to_instant(i.starts_at) == utc_moment(2026, 1, 7, 12)
        )
        items = Schedule.objects.filter(instance=instance).order_by("position")

        # default_crossfade_duration of the fixture is 1.5 seconds, and the
        # legacy service stores seconds while carrying the remainder forward.
        self.assertEqual(
            [
                (i.position, to_instant(i.starts_at), to_instant(i.ends_at))
                for i in items
            ],
            [
                (0, utc_moment(2026, 1, 7, 12), utc_moment(2026, 1, 7, 12, 0, 3)),
                (1, utc_moment(2026, 1, 7, 12, 0, 1), utc_moment(2026, 1, 7, 12, 0, 4)),
                (2, utc_moment(2026, 1, 7, 12, 0, 2), utc_moment(2026, 1, 7, 12, 0, 3)),
            ],
        )

        for item in items:
            self.assertEqual(item.position_status, Schedule.PositionStatus.INSIDE)
            self.assertEqual(item.broadcasted, 0)
            # The legacy insert omits the column, whose default is false.
            self.assertIs(item.played, False)

        instance.refresh_from_db()
        self.assertEqual(instance.filled_time, timedelta(seconds=6, milliseconds=875))
        self.assertIsNotNone(instance.last_scheduled_at)

    def test_show_with_two_repeating_days(self):
        self.assertEqual(
            len(self.starts(self.two_days)), 103
        )  # 52 mondays, 51 thursdays

    def test_non_repeating_show_is_not_picked_up(self):
        self.assertEqual(self.starts(self.nonrepeating), [])

        # ... because the show day query filters it out, which is the criteria
        # the legacy code renders as "repeat_type <> -1".
        self.assertNotIn(
            ShowDays.objects.get(show=self.nonrepeating),
            show_days_in_range(POPULATED_UNTIL, UNTIL),
        )

    def test_next_populate_date_is_written_back_per_show_day(self):
        """Where the next run starts, per repeating day."""
        expected = {
            self.weekly_utc: date(2027, 1, 4),
            self.weekly_berlin: date(2027, 1, 6),
            self.monthly_fifth_thursday: date(2026, 12, 31),
        }
        for show, day in expected.items():
            with self.subTest(show=show.name):
                self.assertEqual(
                    ShowDays.objects.filter(show=show).first().repeat_next_on, day
                )


class RepeatingShowOnAMissingLocalTime(TestCase):
    """An instance whose local time does not exist on that day."""

    def test_start_is_moved_forward_and_stays_there_for_the_run(self):
        show = create_show("weekly 02:30 berlin on sunday")
        create_day(
            show,
            first=date(2026, 3, 15),
            weekday=ShowDays.WeekDay.SUNDAY,
            kind=RepeatType.WEEKLY,
            tz="Europe/Berlin",
            start=time(2, 30),
            duration="00:30",
        )

        delegate_instance_creation(
            utc_moment(2026, 5, 1), now=NOW, populated_until=POPULATED_UNTIL
        )

        starts = [
            to_instant(i.starts_at)
            for i in ShowInstance.objects.filter(show=show).order_by("starts_at")
        ]

        # 2026-03-29 has no 02:30 in Berlin, so that instance starts at 03:30.
        # What the legacy service also does - and the reason this is spelled
        # out - is keep the shifted wall clock for the rest of the run instead
        # of snapping back to 02:30, because DatePeriod hands the mutated
        # DateTime to the next iteration. Verified against the legacy service:
        # every occurrence from 2026-03-29 on is 01:30 UTC in the same run.
        # The stored start_time stays 02:30, so a later run, which rebuilds
        # from next_pop_date and the show day, produces 02:30 again.
        self.assertEqual(
            starts,
            [
                utc_moment(2026, 3, 15, 1, 30),
                utc_moment(2026, 3, 22, 1, 30),
                utc_moment(2026, 3, 29, 1, 30),
                utc_moment(2026, 4, 5, 1, 30),
                utc_moment(2026, 4, 12, 1, 30),
                utc_moment(2026, 4, 19, 1, 30),
                utc_moment(2026, 4, 26, 1, 30),
            ],
        )


class PopulateGuardTests(TransactionTestCase):
    """The populated-until preference that keeps the calendar from being built twice.

    A serializable transaction cannot run inside the per-test transaction of
    ``TestCase``, hence ``TransactionTestCase`` - and rows are cleared here
    rather than relied on from the flush between tests.
    """

    def setUp(self):
        ShowInstance.objects.all().delete()
        ShowDays.objects.all().delete()
        ShowRebroadcast.objects.all().delete()
        Preference.objects.filter(key__in=["shows_populated_until"]).delete()

        self.show = create_show("weekly utc")
        create_day(
            self.show,
            first=date(2026, 1, 5),
            weekday=ShowDays.WeekDay.MONDAY,
            kind=RepeatType.WEEKLY,
        )

    def test_nothing_is_created_when_the_calendar_is_already_populated(self):
        PreferenceStore.set_shows_populated_until(UNTIL)

        self.assertEqual(populate_show_instances(UNTIL - timedelta(days=1)), 0)
        self.assertEqual(ShowInstance.objects.filter(show=self.show).count(), 0)

    def test_instances_are_created_and_the_preference_moves(self):
        PreferenceStore.set_shows_populated_until(POPULATED_UNTIL)

        created = populate_show_instances(UNTIL)

        self.assertGreater(created, 0)
        self.assertEqual(PreferenceStore.shows_populated_until(), UNTIL)
        self.assertEqual(ShowInstance.objects.filter(show=self.show).count(), created)

    def test_a_second_run_creates_nothing(self):
        first = populate_show_instances(UNTIL)
        second = populate_show_instances(UNTIL)

        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(ShowInstance.objects.filter(show=self.show).count(), first)

    def test_preference_is_written_in_the_legacy_format(self):
        PreferenceStore.set_shows_populated_until(UNTIL)

        value = Preference.objects.get(key="shows_populated_until").value
        self.assertEqual(value, "2026-12-31 00:00:00")
