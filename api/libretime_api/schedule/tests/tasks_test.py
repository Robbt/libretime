# A test that takes a fixture rebinds the fixture function's name as its own parameter:
# that is how pytest injects, and no spelling of it satisfies pylint's shadowing check.
# The database fixtures are likewise requested for their effect, not their value.
# pylint: disable=redefined-outer-name,unused-argument
from datetime import date, datetime, time, timedelta

import pytest
from django.utils import timezone
from model_bakery import baker

from ...core.models import Preference
from ...worker import populate_shows_scheduled
from ..models import Schedule, Show, ShowDays, ShowInstance
from ..services.populate import PreferenceStore, RepeatType
from ..tasks import (
    POPULATE_AHEAD,
    POPULATE_MIN_AHEAD,
    clean_overbooked_schedule,
    populate_shows,
)


def make_instance(ends_ago: timedelta) -> ShowInstance:
    ends_at = timezone.now() - ends_ago
    return baker.make(
        ShowInstance,
        starts_at=ends_at - timedelta(hours=1),
        ends_at=ends_at,
    )


def make_schedule(
    instance: ShowInstance,
    starts_at: datetime | None = None,
    position_status: int = Schedule.PositionStatus.OUTSIDE,
    played: bool = False,
    broadcasted: int = 0,
) -> Schedule:
    """
    Make a schedule item that is overbooked and eligible for cleanup by default.
    """
    if starts_at is None:
        starts_at = instance.ends_at + timedelta(minutes=5)

    return baker.make(
        Schedule,
        instance=instance,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=5),
        position_status=position_status,
        played=played,
        broadcasted=broadcasted,
        cue_in=timedelta(0),
        cue_out=timedelta(minutes=5),
    )


# pylint: disable=invalid-name,unused-argument
def test_clean_overbooked_schedule(db) -> None:
    old = make_instance(ends_ago=timedelta(days=100))
    overbooked = make_schedule(old)

    assert clean_overbooked_schedule() == 1

    assert not Schedule.objects.filter(id=overbooked.id).exists()


# pylint: disable=invalid-name,unused-argument
def test_clean_overbooked_schedule_nothing_to_delete(db) -> None:
    assert clean_overbooked_schedule() == 0


# pylint: disable=invalid-name,unused-argument
def test_clean_overbooked_schedule_keeps_items(db) -> None:
    old = make_instance(ends_ago=timedelta(days=100))
    recent = make_instance(ends_ago=timedelta(days=10))

    kept = [
        # Show instance ended less than 90 days ago.
        make_schedule(recent),
        # Item ended less than 90 days ago, even if its show instance is older.
        make_schedule(old, starts_at=old.ends_at + timedelta(days=15)),
        # Item starts before, or exactly at, the end of the show instance.
        make_schedule(old, starts_at=old.ends_at - timedelta(minutes=1)),
        make_schedule(old, starts_at=old.ends_at),
        # Item is not outside of the show instance.
        make_schedule(old, position_status=Schedule.PositionStatus.INSIDE),
        make_schedule(old, position_status=Schedule.PositionStatus.BOUNDARY),
        make_schedule(old, position_status=Schedule.PositionStatus.FILLER),
        # Item was played or broadcasted.
        make_schedule(old, played=True),
        make_schedule(old, broadcasted=1),
    ]

    assert clean_overbooked_schedule() == 0

    assert Schedule.objects.count() == len(kept)


# pylint: disable=invalid-name,unused-argument
def test_clean_overbooked_schedule_batches(db) -> None:
    old = make_instance(ends_ago=timedelta(days=100))
    for _ in range(5):
        make_schedule(old)

    assert clean_overbooked_schedule(batch_size=2) == 5

    assert not Schedule.objects.exists()


def make_weekly_show(first_show_on: date) -> Show:
    """A repeating show, which is what needs instances generated ahead."""
    show = baker.make(Show, linked=False, linkable=True)
    baker.make(
        ShowDays,
        show=show,
        first_show_on=first_show_on,
        last_show_on=None,
        start_time=time(1, 0),
        timezone="UTC",
        duration="01:00",
        record_enabled=0,
        week_day=ShowDays.WeekDay.MONDAY,
        repeat_kind=RepeatType.WEEKLY,
        repeat_next_on=first_show_on,
    )
    return show


@pytest.fixture()
def clean_calendar(transactional_db):
    """
    A calendar with no instances and no populated-until preference.

    The legacy tables are turned into managed ones by the test runner only for
    the duration of a session and back to unmanaged at its end, so they are not
    truncated between tests - a preference left behind by another test would
    silently decide whether this one has work to do.
    """
    Schedule.objects.all().delete()
    ShowInstance.objects.all().delete()
    ShowDays.objects.all().delete()
    Show.objects.all().delete()
    Preference.objects.filter(
        key__in=["shows_populated_until", "default_crossfade_duration"]
    ).delete()


def test_populate_shows_leaves_a_long_horizon_alone(clean_calendar) -> None:
    """Nothing to do while the horizon is further away than the threshold."""
    make_weekly_show(timezone.localdate())
    PreferenceStore.set_shows_populated_until(timezone.now() + timedelta(days=5))

    assert populate_shows() == 0
    assert not ShowInstance.objects.exists()


def test_populate_shows_extends_a_short_horizon(clean_calendar) -> None:
    make_weekly_show(timezone.localdate())
    horizon = timezone.now() + POPULATE_MIN_AHEAD - timedelta(hours=1)
    PreferenceStore.set_shows_populated_until(horizon)

    created = populate_shows()

    assert created > 0
    assert ShowInstance.objects.count() == created

    # The horizon moved to the same instant the legacy service would use.
    extended = PreferenceStore.shows_populated_until()
    assert extended is not None
    assert extended > horizon
    assert abs(extended - (timezone.now() + POPULATE_AHEAD)) < timedelta(minutes=5)


def test_populate_shows_extends_an_unset_horizon(clean_calendar) -> None:
    make_weekly_show(timezone.localdate())

    assert populate_shows() > 0


def test_populate_shows_is_untouched_by_a_second_run(clean_calendar) -> None:
    """The preference is the guard, so repeating the task does nothing twice."""
    make_weekly_show(timezone.localdate())

    first = populate_shows()
    second = populate_shows()

    assert first > 0
    assert second == 0
    assert ShowInstance.objects.count() == first


def test_populate_shows_is_only_scheduled_when_the_api_owns_it(settings) -> None:
    """The switch that hands the calendar to this worker, and takes it back."""
    settings.CONFIG.general.show_instance_populator = "legacy"
    assert not populate_shows_scheduled()

    settings.CONFIG.general.show_instance_populator = "api"
    assert populate_shows_scheduled()
