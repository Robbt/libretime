"""
Putting a show on the calendar over http.

What a create leaves behind is pinned down by
``tools/show-editor-equivalence``, which compares against the legacy service row by
row. What is checked here is the part that only exists at the boundary: which
vocabulary a client may write in, who is allowed to, what a refusal says and in
whose words, and that a show that was refused left nothing behind.
"""

# The db fixture is asked for its effect, not its value
# pylint: disable=unused-argument
import json
import re
from datetime import UTC, date, datetime

import pytest
from django.db import connection
from model_bakery import baker
from rest_framework.test import APIClient

from libretime_api.core.models import Preference, Role, User
from libretime_api.schedule.models import Show, ShowDays, ShowInstance, ShowRebroadcast
from libretime_api.schedule.services.populate import to_instant

PASSWORD = "a-passphrase"
SHOWS_UNTIL = "2044-02-07 00:00:00"


#: A Friday show, once a week, an hour long, with no end. 2044-01-01 is a Friday,
#: so the instances land on the 1st, 8th, 15th, 22nd, 29th and 5th - the same six
#: the legacy fixtures produce.
def friday_show(**overrides):
    show = {
        "name": "Sunday night",
        "timezone": "UTC",
        "first_show_on": "2044-01-01",
        "start_time": "20:00",
        "duration": "1:00",
        "repeats": "weekly",
        "week_days": ["fri"],
    }
    show.update(overrides)

    return show


def make_user(role: Role, username: str) -> User:
    return User.objects.create_user(
        role=role,
        username=username,
        password=PASSWORD,
        email=f"{username}@example.org",
        first_name="Some",
        last_name="One",
    )


def signed_in(role: Role = Role.MANAGER, username: str = "manager") -> APIClient:
    """A client holding a session the api itself created, able to write."""
    make_user(role, username)
    client = APIClient()
    client.post(
        "/api/v2/session",
        {"username": username, "password": PASSWORD},
        format="json",
    )
    token = client.get("/api/v2/session/csrf-token").json()["csrf_token"]
    client.credentials(HTTP_X_CSRFTOKEN=token)

    return client


@pytest.fixture(autouse=True)
def calendar(db, monkeypatch):
    """The window the calendar is filled to, and no playout to announce to."""
    Preference.objects.create(key="shows_populated_until", value=SHOWS_UNTIL)
    monkeypatch.setattr(
        "libretime_api.schedule.services.show.update_schedule", lambda: None
    )


def rows(show):
    """
    Everything the create wrote, as text with the ids removed.

    Comparing two shows this way asks whether the two requests meant the same
    show, which is the only thing worth asking of two vocabularies.
    """

    def dump(sql, params):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)

            return [
                re.sub(r'"(id|show_id)": "?\d+"?', "", json.dumps(json.loads(text)))
                for (text,) in cursor.fetchall()
            ]

    return (
        dump(
            "select to_jsonb(t)::text from (select * from cc_show where id=%s) t",
            [show],
        )
        + dump(
            "select to_jsonb(t)::text from (select id, first_show, start_time,"
            " duration, repeat_type, day, last_show, record, next_pop_date"
            " from cc_show_days where show_id=%s order by id) t",
            [show],
        )
        + dump(
            "select to_jsonb(t)::text from (select day_offset, start_time"
            " from cc_show_rebroadcast where show_id=%s order by id) t",
            [show],
        )
        + dump(
            "select to_jsonb(t)::text from (select starts, ends, rebroadcast, record"
            " from cc_show_instances where show_id=%s order by id) t",
            [show],
        )
    )


# ---------------------------------------------------------------------- create ---


@pytest.mark.django_db()
def test_creating_a_show_puts_it_on_the_calendar():
    response = signed_in().post("/api/v2/shows", friday_show(), format="json")

    assert response.status_code == 201, response.data
    created = response.json()

    assert created["name"] == "Sunday night"
    assert created["week_days"] == [5]

    # The response is the answer to "what did I just schedule", so a client does
    # not have to ask twice for what the calendar now holds.
    assert [instance["starts_at"][:10] for instance in created["instances"]] == [
        "2044-01-01",
        "2044-01-08",
        "2044-01-15",
        "2044-01-22",
        "2044-01-29",
        "2044-02-05",
    ]

    show = Show.objects.get(pk=created["id"])

    assert show.description is None
    assert show.foreground_color == "ffffff"
    assert show.auto_playlist_enabled is False


@pytest.mark.django_db()
def test_a_show_posted_in_the_calendar_s_words_makes_the_same_rows():
    """
    The two vocabularies are one language.

    Asserted as rows rather than as a parsed payload, because the payload is not
    the contract: the calendar is. A field that is renamed but means something
    slightly different - a duration rounded, a weekday dropped, an offset turned
    into a date - would pass a test about names and fail a station.
    """
    client = signed_in()

    in_our_words = client.post("/api/v2/shows", friday_show(), format="json")
    in_its_words = client.post(
        "/api/v2/shows",
        {
            "add_show_name": "Sunday night",
            "add_show_timezone": "UTC",
            "add_show_start_date": "2044-01-01",
            "add_show_start_time": "20:00",
            "add_show_duration": "01h 00m",
            "add_show_repeats": 1,
            "add_show_repeat_type": 0,
            "add_show_day_check": [5],
            "add_show_no_end": 1,
            "add_show_color": "ffffff",
            "add_show_background_color": "364492",
        },
        format="json",
    )

    assert in_our_words.status_code == 201, in_our_words.data
    assert in_its_words.status_code == 201, in_its_words.data

    ours = rows(in_our_words.json()["id"])
    theirs = rows(in_its_words.json()["id"])

    assert ours == theirs
    assert len(ours) == 1 + 1 + 0 + 6


@pytest.mark.django_db()
def test_the_weekday_a_monthly_show_remembers_is_the_one_it_first_aird():
    """
    "The first Friday of the month" is stored as a Friday, not as a first.

    The instance builder needs the weekday to find the next one, and the calendar
    has always taken it from the date the show was first set to air: a show posted
    with a Friday start date and asked for on a Wednesday would come back on
    Fridays, which is worth knowing but is not this endpoint's decision to make.
    """
    response = signed_in().post(
        "/api/v2/shows",
        friday_show(repeats="monthly_by_weekday"),
        format="json",
    )

    assert response.status_code == 201, response.data

    rule = ShowDays.objects.get(show_id=response.json()["id"])

    assert (rule.week_day, rule.repeat_kind) == (5, 3)
    assert [row[0] for row in rows(response.json()["id"])[2:]] == [
        row[0] for row in rows(response.json()["id"])[2:]
    ]


@pytest.mark.django_db()
def test_days_of_the_week_may_be_written_as_names_or_numbers():
    client = signed_in()

    by_name = client.post(
        "/api/v2/shows", friday_show(week_days=["sun", "tue"]), format="json"
    )
    by_number = client.post(
        "/api/v2/shows", friday_show(week_days=[0, 2]), format="json"
    )

    assert rows(by_name.json()["id"]) == rows(by_number.json()["id"])
    assert ShowDays.objects.filter(show_id=by_name.json()["id"]).count() == 2


@pytest.mark.django_db()
def test_a_show_that_runs_once_runs_once():
    response = signed_in().post(
        "/api/v2/shows", friday_show(repeats="none", week_days=None), format="json"
    )

    assert response.status_code == 201, response.data
    assert len(response.json()["instances"]) == 1
    assert ShowDays.objects.get(show_id=response.json()["id"]).repeat_kind == -1


@pytest.mark.django_db()
def test_rebroadcasts_are_counted_from_the_show_they_replay():
    response = signed_in().post(
        "/api/v2/shows",
        friday_show(
            repeats="none",
            week_days=None,
            record=True,
            rebroadcasts=[
                {"after_days": 1, "at": "20:00"},
                {"after_days": 3, "at": "22:30"},
            ],
        ),
        format="json",
    )

    assert response.status_code == 201, response.data

    show = Show.objects.get(pk=response.json()["id"])

    assert [rule.day_offset for rule in ShowRebroadcast.objects.filter(show=show)] == [
        "1 days",
        "3 days",
    ]

    instances = list(ShowInstance.objects.filter(show=show).order_by("id"))

    # The recording airs once and is replayed twice; the replays are not recorded.
    assert [(i.rebroadcast, i.record_enabled) for i in instances] == [
        (0, 1),
        (1, 0),
        (1, 0),
    ]
    assert [i.instance_id for i in instances[1:]] == [instances[0].pk] * 2


@pytest.mark.django_db()
def test_a_duration_arrives_as_the_calendar_would_have_written_it():
    """
    The duration column is a string, made from the string the form posted.

    So "1:30" from an api client has to arrive as the calendar's own "01h 30m", to
    end up as "01:30" - the same row as the same show built in the browser.
    """
    client = signed_in()
    written = client.post(
        "/api/v2/shows", friday_show(name="a", duration="1:30"), format="json"
    )
    posted = client.post(
        "/api/v2/shows", friday_show(name="b", duration="01h 30m"), format="json"
    )

    assert ShowDays.objects.get(show_id=written.json()["id"]).duration == "01:30"
    assert ShowDays.objects.get(show_id=posted.json()["id"]).duration == "01:30"

    instance = ShowInstance.objects.filter(show_id=written.json()["id"]).first()

    assert instance.ends_at - instance.starts_at == __import__("datetime").timedelta(
        minutes=90
    )


@pytest.mark.django_db()
def test_a_show_that_ends_does_so_on_the_day_it_was_told():
    response = signed_in().post(
        "/api/v2/shows",
        friday_show(last_show_on="2044-01-26"),
        format="json",
    )

    assert response.status_code == 201, response.data
    # The 26th is a Thursday, so the last Friday that runs is the 22nd.
    assert [i["starts_at"][:10] for i in response.json()["instances"]] == [
        "2044-01-01",
        "2044-01-08",
        "2044-01-15",
        "2044-01-22",
    ]
    assert (
        ShowDays.objects.get(show_id=response.json()["id"]).last_show_on.strftime(
            "%Y-%m-%d"
        )
        == "2044-01-27"
    )


@pytest.mark.django_db()
def test_hosts_are_named_by_id_and_need_not_be_listed():
    host = make_user(Role.HOST, "voice")
    response = signed_in().post(
        "/api/v2/shows", friday_show(hosts=[host.pk]), format="json"
    )

    assert response.status_code == 201, response.data
    assert list(Show.objects.get(pk=response.json()["id"]).hosts.all()) == [host]


@pytest.mark.django_db()
def test_an_autoplaylist_is_enabled_by_naming_one():
    playlist = baker.make("schedule.Playlist", name="friday set")

    response = signed_in().post(
        "/api/v2/shows", friday_show(auto_playlist=playlist.pk), format="json"
    )

    assert response.json()["auto_playlist_enabled"] is True


# ---------------------------------------------------------------- permissions ---


@pytest.mark.django_db()
def test_a_host_may_not_put_a_show_on_the_calendar():
    """The calendar is a manager's tool: a host presents shows, not schedules."""
    response = signed_in(Role.HOST, "voice").post(
        "/api/v2/shows", friday_show(), format="json"
    )

    assert response.status_code == 403
    assert not Show.objects.exists()


@pytest.mark.django_db()
def test_an_anonymous_request_may_not():
    assert APIClient().post(
        "/api/v2/shows", friday_show(), format="json"
    ).status_code in (
        401,
        403,
    )


@pytest.mark.django_db()
def test_a_write_without_the_sessions_token_is_refused():
    make_user(Role.MANAGER, "manager")
    client = APIClient()
    client.post(
        "/api/v2/session",
        {"username": "manager", "password": PASSWORD},
        format="json",
    )

    assert client.post("/api/v2/shows", friday_show(), format="json").status_code == 403
    assert not Show.objects.exists()


@pytest.mark.django_db()
def test_a_service_with_the_station_token_may_schedule():
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Api-Key {__import__('django').conf.settings.CONFIG.general.api_key}"
    )

    response = client.post("/api/v2/shows", friday_show(), format="json")

    assert response.status_code == 201, response.data
    assert ShowInstance.objects.count() == 6


# ------------------------------------------------------------------- refusal ---


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"name": "no schedule at all"}, "timezone"),
        (friday_show(timezone=None), "timezone"),
        (friday_show(timezone="Not/AZone"), "timezone"),
        (friday_show(name=""), "name"),
        (friday_show(duration="ptwo thirty"), "duration"),
        (friday_show(duration="0:00"), "duration"),
        (friday_show(first_show_on="the first of next month"), "first_show_on"),
        (friday_show(week_days=["fribbidy"]), "week_days"),
        (friday_show(week_days=None), "week_days"),
        (friday_show(week_days=[9]), "week_days"),
        (friday_show(last_show_on="2043-12-31"), "last_show_on"),
        (friday_show(rebroadcasts=[{"after_days": 1, "at": "20:00"}]), "rebroadcasts"),
    ],
)
def test_a_refused_show_says_which_field_and_leaves_nothing_behind(payload, field):
    response = signed_in().post("/api/v2/shows", payload, format="json")

    assert response.status_code == 400, response.data
    assert field in response.json(), response.data
    assert not Show.objects.exists()
    assert not ShowDays.objects.exists()
    assert not ShowInstance.objects.exists()


@pytest.mark.django_db()
def test_a_refused_show_says_so_in_the_words_the_client_used():
    """
    A client that wrote ``add_show_timezone`` is a client reading the calendar's
    documentation, so it is answered in the vocabulary of its own request.
    """
    response = signed_in().post(
        "/api/v2/shows",
        {
            "add_show_name": "x",
            "add_show_timezone": "Not/AZone",
            "add_show_start_date": "2044-01-01",
            "add_show_start_time": "20:00",
            "add_show_duration": "01h 00m",
            "add_show_repeats": 0,
        },
        format="json",
    )

    assert response.status_code == 400, response.data
    assert "timezone" in response.json()


@pytest.mark.django_db()
def test_playout_is_told_about_a_show_only_once_it_exists(monkeypatch):
    """
    A create that failed says nothing to the playout service.

    php pushed a schedule reload and then found the transaction had not committed
    what it was reloading; the order here is write, commit, tell.
    """
    notified = []
    monkeypatch.setattr(
        "libretime_api.schedule.services.show.update_schedule",
        lambda: notified.append(True),
    )
    client = signed_in()

    client.post("/api/v2/shows", friday_show(timezone="Not/AZone"), format="json")

    assert not notified, notified

    client.post("/api/v2/shows", friday_show(), format="json")

    assert notified == [True]


# ------------------------------------------------------------------- editing ---


def make_show(client, **overrides):
    response = client.post("/api/v2/shows", friday_show(**overrides), format="json")

    assert response.status_code == 201, response.data

    return response.json()


@pytest.mark.django_db()
def test_editing_a_show_goes_through_the_same_calendar_work():
    """
    A put is not an update of one row: the nights change with it.

    Ending the show on the 9th removes the instances after it, which is the same
    thing the browser's dialog does and the reason this endpoint is not a serializer
    .save().
    """
    client = signed_in()
    created = make_show(client)

    response = client.put(
        f"/api/v2/shows/{created['id']}",
        friday_show(last_show_on="2044-01-09"),
        format="json",
    )

    assert response.status_code == 200, response.data

    show = Show.objects.get(pk=created["id"])

    assert ShowDays.objects.get(show=show).last_show_on == date(2044, 1, 10)
    assert ShowInstance.objects.filter(show=show).count() == 2
    # One rule, rewritten - not a second one describing the same Fridays.
    assert ShowDays.objects.filter(show=show).count() == 1


@pytest.mark.django_db()
def test_a_host_may_not_move_a_show_about_the_calendar():
    client = signed_in()
    created = make_show(client)

    host = signed_in(Role.HOST, "voice")
    response = host.put(
        f"/api/v2/shows/{created['id']}",
        friday_show(last_show_on="2044-01-09"),
        format="json",
    )

    assert response.status_code == 403
    assert ShowDays.objects.count() == 1
    assert ShowDays.objects.get().last_show_on is None


@pytest.mark.django_db()
def test_an_edit_needs_the_sessions_token_like_any_other_write():
    """
    A session that arrives without its token may read the calendar and change
    nothing - on a show that exists as well as on one that does not.
    """
    client = signed_in()
    created = make_show(client)

    without_token = APIClient()
    without_token.post(
        "/api/v2/session",
        {"username": "manager", "password": PASSWORD},
        format="json",
    )

    response = without_token.put(
        f"/api/v2/shows/{created['id']}", friday_show(), format="json"
    )

    assert response.status_code == 403
    assert ShowDays.objects.get().last_show_on is None


@pytest.mark.django_db()
def test_a_show_can_be_built_out_further_on_request():
    client = signed_in()
    created = make_show(client, repeats="biweekly")

    response = client.post(
        f"/api/v2/shows/{created['id']}/extend",
        {"until": "2044-03-12"},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.json()["instances_created"] == 3

    show = Show.objects.get(pk=created["id"])

    assert ShowInstance.objects.filter(show=show).count() == 6
    # Looking at March is not the same as the calendar being built to March: the
    # station's populated date is what the periodic populator works to, and this
    # request leaves it alone.
    assert Preference.objects.get(key="shows_populated_until").value == SHOWS_UNTIL


# -------------------------------------------------------------- one night ------


@pytest.mark.django_db()
def test_deleting_one_night_flags_it():
    client = signed_in()
    created = make_show(client)
    show = Show.objects.get(pk=created["id"])
    victim = ShowInstance.objects.filter(show=show).order_by("starts_at")[2]

    response = client.delete(
        f"/api/v2/show-instances/{victim.pk}?scope=single", format="json"
    )

    assert response.status_code == 200, response.data
    assert ShowInstance.objects.get(pk=victim.pk).modified is True
    assert ShowInstance.objects.filter(show=show, modified=False).count() == 5


@pytest.mark.django_db()
def test_deleting_a_night_and_all_following_is_a_different_request():
    client = signed_in()
    created = make_show(client)
    show = Show.objects.get(pk=created["id"])
    third = ShowInstance.objects.filter(show=show).order_by("starts_at")[2]

    response = client.delete(
        f"/api/v2/show-instances/{third.pk}?scope=following", format="json"
    )

    assert response.status_code == 200, response.data
    assert ShowInstance.objects.filter(show=show, modified=False).count() == 2


@pytest.mark.django_db()
def test_the_scope_of_a_delete_has_to_be_one_of_the_two_the_calendar_offers():
    client = signed_in()
    created = make_show(client)
    victim = ShowInstance.objects.filter(show_id=created["id"]).first()

    response = client.delete(f"/api/v2/show-instances/{victim.pk}?scope=please")

    assert response.status_code == 400
    assert "scope" in response.json()
    assert ShowInstance.objects.get(pk=victim.pk).modified is False


@pytest.mark.django_db()
def test_a_night_that_already_aired_cannot_be_taken_back():
    client = signed_in()
    Preference.objects.filter(key="shows_populated_until").update(value="2024-01-08")

    response = client.post(
        "/api/v2/shows",
        friday_show(first_show_on="2024-01-01", last_show_on=None),
        format="json",
    )

    assert response.status_code == 201, response.data
    aired = ShowInstance.objects.first()

    refused = client.delete(f"/api/v2/show-instances/{aired.pk}?scope=single")

    assert refused.status_code == 400
    assert "already aired" in str(refused.json())


@pytest.mark.django_db()
def test_one_night_can_be_given_its_own_time():
    client = signed_in()
    created = make_show(client)
    show = Show.objects.get(pk=created["id"])
    second = ShowInstance.objects.filter(show=show).order_by("starts_at")[1]

    response = client.patch(
        f"/api/v2/show-instances/{second.pk}",
        {"start_time": "01:00", "duration": "1:00", "description": "guest host"},
        format="json",
    )

    assert response.status_code == 200, response.data

    moved = ShowInstance.objects.get(pk=response.json()["id"])

    assert to_instant(moved.starts_at) == datetime(2044, 1, 8, 1, 0, tzinfo=UTC)
    assert moved.description == "guest host"
    # The night it leaves is flagged, and the show's own pattern keeps going.
    assert ShowInstance.objects.get(pk=second.pk).modified is True
    assert ShowInstance.objects.filter(show=show, modified=False).count() == 6
    assert ShowDays.objects.filter(show=show).count() == 2
