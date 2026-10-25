"""
The show editor: what one save leaves in the database.

The expected rows below are php's. They were taken from
``tools/show-editor-equivalence``, which runs these same payloads through
``Application_Service_ShowService`` and dumps the tables - so a change here that
breaks one of these assertions is a change to what a station's calendar looks like,
not just to a test.

The fixture payloads mirror ``ShowServiceData`` from the legacy test suite; the
harness reads php's copy directly, this one is a test fixture and states what it
needs to state.
"""

# The db fixture is asked for its effect, not its value
# pylint: disable=unused-argument
from datetime import UTC, datetime, timedelta

import pytest

from libretime_api.core.models import Preference, Role, User
from libretime_api.schedule.models import (
    Playlist,
    Show,
    ShowDays,
    ShowHost,
    ShowInstance,
    ShowRebroadcast,
)
from libretime_api.schedule.services.show import (
    ShowForm,
    ShowFormError,
    format_duration,
    save_show,
)

# The instant the harness fixtures are written around: instances are built to the
# populated-until date, and rebroadcasts only when they are still in the future.
NOW = datetime(2026, 10, 25, tzinfo=UTC)
POPULATED_UNTIL = "2044-02-07 00:00:00"


def form(**overrides):
    """The legacy fixture payload for a weekly show, 2044-01-01 00:00 UTC, 1 hour."""
    payload = {
        "add_show_id": -1,
        "add_show_instance_id": -1,
        "add_show_name": "test show",
        "add_show_url": None,
        "add_show_genre": None,
        "add_show_description": None,
        "add_show_start_date": "2044-01-01",
        "add_show_start_time": "00:00",
        "add_show_end_time": "01:00",
        "add_show_duration": "01h 00m",
        "add_show_timezone": "UTC",
        "add_show_has_autoplaylist": False,
        "add_show_autoplaylist_repeat": False,
        "add_show_autoplaylist_id": None,
        "add_show_override_intro_playlist": False,
        "add_show_intro_playlist_id": None,
        "add_show_override_outro_playlist": False,
        "add_show_outro_playlist_id": None,
        "add_show_repeats": 1,
        "add_show_linked": 0,
        "add_show_repeat_type": 0,
        "add_show_monthly_repeat_type": 2,
        "add_show_end_date": "2044-01-01",
        "add_show_no_end": 1,
        "cb_airtime_auth": 0,
        "cb_custom_auth": 0,
        "custom_username": None,
        "custom_password": None,
        "add_show_record": 0,
        "add_show_rebroadcast": 0,
        "add_show_background_color": "364492",
        "add_show_color": "ffffff",
        "add_show_hosts": None,
        "add_show_day_check": [5],
    }

    for index in range(1, 11):
        payload[f"add_show_rebroadcast_date_{index}"] = None
        payload[f"add_show_rebroadcast_time_{index}"] = None
        payload[f"add_show_rebroadcast_date_absolute_{index}"] = None
        payload[f"add_show_rebroadcast_time_absolute_{index}"] = None

    payload.update(overrides)

    return payload


@pytest.fixture(autouse=True)
def populated_until(db):
    Preference.objects.create(key="shows_populated_until", value=POPULATED_UNTIL)


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):
    """
    A save tells playout to reload, and the test environment has no broker.

    The two tests that care about the notification replace this one, so the call
    itself is still exercised.
    """
    monkeypatch.setattr(
        "libretime_api.schedule.services.show.update_schedule", lambda: None
    )


def create(**overrides):
    return save_show(ShowForm.from_legacy_payload(form(**overrides)), now=NOW)[0]


def instances(show):
    return [
        (
            instance.starts_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M"),
            instance.ends_at.astimezone(UTC).strftime("%H:%M"),
            instance.rebroadcast,
            instance.instance_id,
            instance.record_enabled,
        )
        for instance in ShowInstance.objects.filter(show=show).order_by("id")
    ]


# -- the duration the form sends ------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ("01h 00m", "01:00"),
        ("0h 30m", "0:30"),
        ("2h 15m", "2:15"),
        ("00h 00m", "00:00"),
        # No hours, no minutes: text surgery, not parsing, so the legacy code
        # produces "0:0" and the column stores zero. Reproduced rather than tidied.
        ("60", "0:0"),
        ("1h", "1:0"),
    ],
)
def test_the_duration_the_editor_writes(sent, stored):
    assert format_duration(sent) == stored


# -- one show, once -------------------------------------------------------------


def test_a_show_that_does_not_repeat_gets_one_instance_and_a_rule():
    show = create(add_show_repeats=0, add_show_day_check=None)

    assert instances(show) == [("2044-01-01 00:00", "01:00", 0, None, 0)]

    rule = ShowDays.objects.get(show=show)

    # The rule carries the weekday of the start date: a single show needs a day
    # for the row to mean anything, and php gives it one rather than leaving it
    # empty.
    assert (
        rule.week_day,
        rule.repeat_kind,
        rule.first_show_on.strftime("%Y-%m-%d"),
    ) == (
        5,
        -1,
        "2044-01-01",
    )
    assert rule.last_show_on is None


def test_a_recorded_show_gets_a_rebroadcast_instance_per_rule():
    absolute = {
        f"add_show_rebroadcast_date_absolute_{index}": (
            datetime(2044, 1, index + 1).strftime("%Y-%m-%d")
        )
        for index in range(1, 11)
    }
    absolute.update(
        {
            f"add_show_rebroadcast_time_absolute_{index}": "00:00"
            for index in range(1, 11)
        }
    )

    show = create(
        add_show_repeats=0,
        add_show_day_check=None,
        add_show_record=1,
        add_show_rebroadcast=1,
        **absolute,
    )

    rules = list(ShowRebroadcast.objects.filter(show=show).order_by("id"))

    # The editor offers dates for a show that does not repeat; the table stores
    # offsets, which is all the instance builder needs.
    assert [rule.day_offset for rule in rules] == [f"{n} days" for n in range(1, 11)]

    made = instances(show)

    assert made[0] == ("2044-01-01 00:00", "01:00", 0, None, 1)
    assert made[1][:3] == ("2044-01-02 00:00", "01:00", 1)
    assert len(made) == 11

    # Every rebroadcast instance points at the instance it replays. Named rather
    # than assumed to be id 1: sequences keep advancing across tests, and a
    # database the legacy tests ran in restarts them, which is why php's own
    # dataset can spell the id out.
    original = ShowInstance.objects.filter(show=show, rebroadcast=0).get()

    assert all(row[3] == original.pk for row in made[1:])
    # A rebroadcast airs the recording; it is not itself recorded.
    assert all(row[4] == 0 for row in made[1:])


# -- repeats --------------------------------------------------------------------


def test_weekly_instances_stop_at_the_populated_date():
    show = create()

    assert instances(show) == [
        (f"2044-01-{day:02d} 00:00", "01:00", 0, None, 0) for day in (1, 8, 15, 22, 29)
    ] + [("2044-02-05 00:00", "01:00", 0, None, 0)]

    # Where generation resumes next time, past the last instance created.
    assert (
        ShowDays.objects.get(show=show).repeat_next_on.strftime("%Y-%m-%d")
        == "2044-02-12"
    )


def test_biweekly_steps_two_weeks():
    show = create(add_show_repeat_type="1")

    assert [row[0] for row in instances(show)] == [
        "2044-01-01 00:00",
        "2044-01-15 00:00",
        "2044-01-29 00:00",
    ]


def test_an_end_date_is_inclusive_and_lands_on_the_day_after():
    show = create(
        add_show_no_end=0,
        add_show_end_date="2044-01-26",
    )

    rule = ShowDays.objects.get(show=show)

    # last_show is stored as the exclusive end, a day past the date the user picked.
    assert rule.last_show_on.strftime("%Y-%m-%d") == "2044-01-27"
    assert [row[0] for row in instances(show)] == [
        "2044-01-01 00:00",
        "2044-01-08 00:00",
        "2044-01-15 00:00",
        "2044-01-22 00:00",
    ]


def test_each_repeat_day_becomes_its_own_rule_shifted_into_the_future():
    show = create(add_show_day_check=[5, 1, 2])

    rules = list(ShowDays.objects.filter(show=show).order_by("id"))

    # 2044-01-01 is a Friday. A show that also runs on Mondays and Tuesdays starts
    # those on the 4th and 5th, never back in the past.
    assert [
        (rule.week_day, rule.first_show_on.strftime("%Y-%m-%d")) for rule in rules
    ] == [(5, "2044-01-01"), (1, "2044-01-04"), (2, "2044-01-05")]
    assert len(instances(show)) == 16


@pytest.mark.parametrize(
    ("repeat_type", "monthly_type", "weekday_column", "starts"),
    [
        # php takes the "monthly has no weekday" branch on the submitted repeat
        # type, not the resolved one: 2 + monthly 2 stores no weekday, while 3
        # straight from the form keeps the one the start date falls on. The
        # instances are the same either way.
        ("2", 2, None, ["2044-01-01 00:00", "2044-02-01 00:00"]),
        ("3", 2, 5, ["2044-01-01 00:00", "2044-02-05 00:00"]),
    ],
)
def test_monthly_repeats(repeat_type, monthly_type, weekday_column, starts):
    show = create(
        add_show_repeat_type=repeat_type, add_show_monthly_repeat_type=monthly_type
    )

    rule = ShowDays.objects.get(show=show)

    assert rule.week_day == weekday_column
    assert [row[0] for row in instances(show)] == starts


# -- the show row itself --------------------------------------------------------


def test_the_show_row_holds_what_the_editor_said():
    show = create(
        add_show_name="Sunday night",
        add_show_description="repeat after midnight",
        add_show_url="http://example.org",
        add_show_genre="jazz",
        add_show_linked=1,
    )

    show.refresh_from_db()

    assert show.name == "Sunday night"
    assert show.description == "repeat after midnight"
    assert show.foreground_color == "ffffff"
    assert show.background_color == "364492"
    assert show.linked is True
    # A show nobody has linked yet can be linked; only php's ORM sets that default.
    assert show.linkable is True
    assert show.image == ""


def test_a_playlist_id_is_what_makes_the_override_flag_true(db):
    """
    php derives the override flag from the id, and ignores the form's own checkbox.

    Reproduced because it is visible in the row. It also means an edit that removes
    the playlist leaves the flag behind - reported upstream rather than fixed here,
    since fixing it changes rows the legacy editor would not have changed.
    """
    playlist = Playlist.objects.create(name="intro set", owner=None)

    show = create(add_show_override_intro_playlist=1, add_show_intro_playlist_id=None)

    assert show.override_intro_playlist is False

    show = create(
        add_show_override_intro_playlist=0, add_show_intro_playlist_id=playlist.pk
    )

    assert show.override_intro_playlist is True


def test_hosts_are_recorded_and_a_show_may_have_none(db):
    host = User.objects.create_user(
        role=Role.HOST,
        username="host-one",
        password="test",
        email="h@example.org",
        first_name="Host",
        last_name="One",
    )

    assert ShowHost.objects.filter(show=create()).count() == 0
    assert ShowHost.objects.filter(show=create(add_show_hosts=[host.pk])).count() == 1


# -- failure --------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"add_show_name": None},
        {"add_show_start_date": "not a date"},
        {"add_show_start_time": None},
        {"add_show_timezone": None},
        {"add_show_duration": None},
        {"add_show_day_check": [9]},
        {"add_show_day_check": None},
        {"add_show_hosts": "host-one"},
    ],
)
def test_a_payload_that_cannot_describe_a_show_is_refused_by_field(db, overrides):
    with pytest.raises(ShowFormError) as error:
        save_show(ShowForm.from_legacy_payload(form(**overrides)), now=NOW)

    assert next(iter(overrides)) in str(error.value)


def test_a_refused_save_leaves_nothing_behind(db, monkeypatch):
    """
    php rolls the transaction back and then, unhelpfully, logs and returns.

    Here the caller is told, and the database is left as it was - which is the part
    an interface depends on: a show with days but no instances, or instances whose
    show was never written, is worse than no show.
    """
    notified = []

    monkeypatch.setattr(
        "libretime_api.schedule.services.show.update_schedule",
        lambda: notified.append(True),
    )

    with pytest.raises(ShowFormError):
        save_show(
            ShowForm.from_legacy_payload(form(add_show_timezone="Not/AZone")), now=NOW
        )

    assert Show.objects.exists() is False
    assert ShowDays.objects.exists() is False
    assert ShowInstance.objects.exists() is False
    assert notified == []


def test_playout_is_told_once_the_show_exists(db, monkeypatch):
    notified = []

    monkeypatch.setattr(
        "libretime_api.schedule.services.show.update_schedule",
        lambda: notified.append(True),
    )

    create()

    assert notified == [True]


def test_editing_a_show_is_not_claimed_before_it_is_ported(db):
    """
    The reconciliation of existing instances is the harder half of the editor.

    Until it is, an update would leave instances the new rules no longer describe,
    so it refuses rather than half-writing a calendar.
    """
    show = create()

    with pytest.raises(NotImplementedError):
        save_show(
            ShowForm.from_legacy_payload(
                form(add_show_id=show.pk, add_show_name="edited")
            ),
            is_update=True,
            now=NOW,
        )


def test_a_show_day_duration_round_trips_as_the_column_holds_it(db):
    show = create(add_show_duration="1h 30m")

    # The column is text, not an interval: it holds whatever format_duration made,
    # which is why the unpadded "1:30" is what both php and this code store, and why
    # the instance maths reads it back as one hour thirty.
    assert ShowDays.objects.get(show=show).duration == "1:30"
    assert ShowDays.objects.get(show=show).first_show_on.year == 2044
    assert ShowInstance.objects.filter(show=show).first().ends_at == (
        ShowInstance.objects.filter(show=show).first().starts_at + timedelta(minutes=90)
    )
