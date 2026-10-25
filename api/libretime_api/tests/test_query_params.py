"""
Filtering, searching and ordering the collections.

Two things are asserted about every collection here, and the second matters more than
the first: that the parameters work, and that nothing changes when they are absent.

The absence rule exists because playout reads ``GET /api/v2/schedule`` and parses a
bare json array in the order it arrives. A pagination class or a default ordering added
"helpfully" and globally would change that response shape for the one consumer in the
running system - which is why the last test in this file checks the shape of every
collection rather than checking the new features.
"""

# A test that takes a fixture rebinds the fixture function's name as its own parameter:
# that is how pytest injects, and no spelling of it satisfies pylint's shadowing check.
# The database fixtures are likewise requested for their effect, not their value.
# pylint: disable=redefined-outer-name,unused-argument


# The db and api_client fixtures are asked for their effect, not their values.
# pylint: disable=unused-argument
from datetime import timedelta

import pytest
from model_bakery import baker

from libretime_api.core.models import Role, User
from libretime_api.schedule.models import Show, ShowInstance

# ------------------------------------------------------------------------ files ---


@pytest.mark.django_db()
def test_files_can_be_searched_by_text(api_client):
    baker.make("storage.File", track_title="Midnight Drone", artist_name="VLSI")
    baker.make(
        "storage.File", track_title="Sunday-service Recording", artist_name="KYB"
    )

    response = api_client.get("/api/v2/files?search=drone")

    assert response.status_code == 200
    assert [row["track_title"] for row in response.json()] == ["Midnight Drone"]


@pytest.mark.django_db()
def test_the_filters_that_already_worked_still_work(api_client):
    """``md5`` and ``genre`` were the whole surface before; they are not a memory."""
    one = baker.make("storage.File", md5="a" * 32, genre="dub")

    two = baker.make("storage.File", md5="b" * 32, genre="techno")

    assert [
        row["id"] for row in api_client.get(f"/api/v2/files?md5={one.md5}").json()
    ] == [one.id]
    assert [
        row["id"] for row in api_client.get("/api/v2/files?genre=techno").json()
    ] == [two.id]


@pytest.mark.django_db()
def test_files_can_be_filtered_by_a_duration_range(api_client):
    """Lengths are Postgres intervals, so they are asked for as the api writes them."""
    short = baker.make(
        "storage.File", track_title="short", length=timedelta(seconds=30)
    )
    long = baker.make("storage.File", track_title="long", length=timedelta(minutes=9))

    ids = {
        row["id"] for row in api_client.get("/api/v2/files?length_min=00:05:00").json()
    }

    assert ids == {long.id}
    assert short.id not in ids


@pytest.mark.django_db()
def test_files_are_newest_first_and_can_be_asked_otherwise(api_client):
    old = baker.make(
        "storage.File", track_title="aaa", created_at="2044-01-01 00:00:00+00:00"
    )
    new = baker.make(
        "storage.File", track_title="zzz", created_at="2044-06-01 00:00:00+00:00"
    )

    default = [row["id"] for row in api_client.get("/api/v2/files").json()]
    by_title = [
        row["id"] for row in api_client.get("/api/v2/files?ordering=track_title").json()
    ]

    assert default == [new.id, old.id]
    assert by_title == [old.id, new.id]


@pytest.mark.django_db()
def test_an_unknown_order_is_ignored_rather_than_an_error(api_client):
    """A screen asking for a column that no longer exists should still load."""
    baker.make("storage.File", track_title="one")

    response = api_client.get("/api/v2/files?ordering=not_a_column")

    assert response.status_code == 200


# -------------------------------------------------------------- show instances ---


def show_with_instances():
    show = baker.make("schedule.Show", name="Fridays", linked=False, linkable=True)

    for night in range(1, 5):
        baker.make(
            "schedule.ShowInstance",
            show=show,
            starts_at=f"2044-01-{night:02d} 00:00:00+00:00",
            ends_at=f"2044-01-{night:02d} 01:00:00+00:00",
            rebroadcast=0,
        )

    return show


@pytest.mark.django_db()
def test_a_week_of_the_calendar_can_be_asked_for(api_client):
    show = show_with_instances()
    other = show_with_instances()

    week = api_client.get(
        "/api/v2/show-instances?starts_after=2044-01-01T00:00:00Z"
        "&starts_before=2044-01-02T23:59:59Z"
    ).json()

    # Two shows, each with both nights inside the window: four rows, and only the
    # nights inside it - the third and fourth of each show are not in the payload.
    assert {row["show"] for row in week} == {show.id, other.id}
    assert len(week) == 4

    only_one_show = api_client.get(f"/api/v2/show-instances?show={show.id}").json()

    assert len(only_one_show) == 4
    assert {row["show"] for row in only_one_show} == {show.id}


@pytest.mark.django_db()
def test_instances_come_back_in_time_order(api_client):
    show_with_instances()

    starts = [
        row["starts_at"] for row in api_client.get("/api/v2/show-instances").json()
    ]

    assert starts == sorted(starts)


@pytest.mark.django_db()
def test_instances_can_be_read_backwards(api_client):
    show_with_instances()

    starts = [
        row["starts_at"]
        for row in api_client.get("/api/v2/show-instances?ordering=-starts_at").json()
    ]

    assert starts == sorted(starts, reverse=True)


# --------------------------------------------------------------------- shows ----


@pytest.mark.django_db()
def test_shows_can_be_filtered_by_what_they_do(api_client):
    baker.make("schedule.Show", name="Auto", auto_playlist_enabled=True)
    baker.make("schedule.Show", name="Manual", auto_playlist_enabled=False)

    names = [
        row["name"]
        for row in api_client.get("/api/v2/shows?auto_playlist_enabled=true").json()
    ]

    assert names == ["Auto"]


@pytest.mark.django_db()
def test_shows_are_listed_in_the_order_a_picker_wants(api_client):
    baker.make("schedule.Show", name="Zebra")
    baker.make("schedule.Show", name="Antelope")

    assert [row["name"] for row in api_client.get("/api/v2/shows").json()] == [
        "Antelope",
        "Zebra",
    ]


# ------------------------------------------------------------------ playlists ----


@pytest.mark.django_db()
def test_playlists_and_blocks_search_and_sort(api_client):
    baker.make("schedule.Playlist", name="Winter Sets")
    baker.make("schedule.Playlist", name="Summer Sets")
    baker.make("schedule.SmartBlock", name="Deep House", kind="bool")

    playlists = api_client.get("/api/v2/playlists?search=winter").json()
    assert [row["name"] for row in playlists] == ["Winter Sets"]

    blocks = api_client.get("/api/v2/smart-blocks?ordering=-name").json()
    assert [row["name"] for row in blocks] == ["Deep House"]

    assert [row["name"] for row in api_client.get("/api/v2/playlists").json()] == [
        "Summer Sets",
        "Winter Sets",
    ]


@pytest.mark.django_db()
def test_playlist_contents_can_be_narrowed_without_being_reshuffled(api_client):
    """
    Contents are ordered by position only when asked.

    Those position values are written by the legacy editor as well as here, so the api
    does not guess at an order the other writer may not share - but a screen has to be
    able to ask for one.
    """
    playlist = baker.make("schedule.Playlist", name="One")
    other = baker.make("schedule.Playlist", name="Two")

    baker.make("schedule.PlaylistContent", playlist=playlist, position=1)
    baker.make("schedule.PlaylistContent", playlist=playlist, position=0)
    baker.make("schedule.PlaylistContent", playlist=other, position=5)

    mine = api_client.get(
        f"/api/v2/playlist-contents?playlist={playlist.id}&ordering=position"
    ).json()

    assert [row["position"] for row in mine] == [0, 1]
    assert len(api_client.get("/api/v2/playlist-contents").json()) == 3


# --------------------------------------------------------------------- history ---


@pytest.mark.django_db()
def test_playout_history_can_be_read_as_a_window(api_client):
    baker.make(
        "history.PlayoutHistory",
        starts="2044-01-01 00:00:00+00:00",
        ends="2044-01-01 00:04:00+00:00",
    )
    baker.make(
        "history.PlayoutHistory",
        starts="2044-02-01 00:00:00+00:00",
        ends="2044-02-01 00:04:00+00:00",
    )

    january = api_client.get(
        "/api/v2/playout-history?starts_after=2044-01-01T00:00:00Z"
        "&starts_before=2044-01-31T23:59:59Z"
    ).json()

    assert len(january) == 1

    newest_first = [
        row["starts"] for row in api_client.get("/api/v2/playout-history").json()
    ]

    assert newest_first == sorted(newest_first, reverse=True)


@pytest.mark.django_db()
def test_listener_counts_can_be_plotted_for_one_mount(api_client):
    # A count belongs to a Timestamp row, not to a datetime of its own.
    lowland = baker.make("history.MountName", mount_name="lowland")
    january = baker.make("history.Timestamp", timestamp="2044-01-01 00:00:00+00:00")
    february = baker.make("history.Timestamp", timestamp="2044-02-01 00:00:00+00:00")

    baker.make(
        "history.ListenerCount", mount_name=lowland, listener_count=3, timestamp=january
    )
    baker.make(
        "history.ListenerCount",
        mount_name=lowland,
        listener_count=9,
        timestamp=february,
    )

    window = api_client.get(
        f"/api/v2/listener-counts?mount_name={lowland.id}"
        "&timestamp_after=2044-01-15T00:00:00Z"
    ).json()

    assert [row["listener_count"] for row in window] == [9]


# ---------------------------------------------------------------------- people ---


@pytest.mark.django_db()
def test_users_can_be_found_by_name_or_role(admin_client):
    for name, role in [("nadia", Role.ADMIN), ("tomas", Role.HOST)]:
        User.objects.create_user(
            role=role,
            username=name,
            password="whatever",
            email=f"{name}@example.org",
            first_name=name.title(),
            last_name="Person",
        )

    hosts = admin_client.get("/api/v2/users?role=H").json()
    assert [row["username"] for row in hosts] == ["tomas"]

    found = admin_client.get("/api/v2/users?search=nad").json()
    assert [row["username"] for row in found] == ["nadia"]

    # Ordered, and containing both: the test database has admin users from other
    # fixtures, and this asserts the sort rather than the station's population.
    ordered = [
        row["username"]
        for row in admin_client.get("/api/v2/users?ordering=-username").json()
    ]
    assert ordered == sorted(ordered, reverse=True)
    assert {"tomas", "nadia"} <= set(ordered)


# --------------------------------------------------- what must not have changed ---

COLLECTIONS = [
    "/api/v2/files",
    "/api/v2/libraries",
    "/api/v2/playlists",
    "/api/v2/playlist-contents",
    "/api/v2/schedule",
    "/api/v2/shows",
    "/api/v2/show-days",
    "/api/v2/show-hosts",
    "/api/v2/show-instances",
    "/api/v2/show-rebroadcasts",
    "/api/v2/smart-blocks",
    "/api/v2/smart-block-contents",
    "/api/v2/smart-block-criteria",
    "/api/v2/webstreams",
    "/api/v2/webstream-metadata",
    "/api/v2/podcasts",
    "/api/v2/podcast-episodes",
    "/api/v2/playout-history",
    "/api/v2/listener-counts",
    "/api/v2/live-logs",
    "/api/v2/users",
    "/api/v2/preferences",
]


@pytest.fixture()
def admin_client(api_client, db):
    """A person, not a token: the list shape has to hold for both."""
    admin = User.objects.create_user(
        role=Role.ADMIN,
        username="list_admin",
        password="whatever",
        email="admin@example.org",
        first_name="List",
        last_name="Admin",
    )
    api_client.force_authenticate(user=admin)

    return api_client


@pytest.mark.django_db()
@pytest.mark.parametrize("path", COLLECTIONS)
def test_a_collection_is_still_a_bare_list(admin_client, path):
    """
    No pagination, no envelope, no matter what filters were added.

    Every one of these is a list today, and playout parses one of them as such. A
    default pagination class - the obvious next thing to reach for when a library
    gets big - turns all of these into ``{"count": …, "results": […]}`` and would be
    a silent break of the running station, not a new feature. When pagination comes,
    it comes per view with the consumers listed, and this test moves with it.
    """
    response = admin_client.get(path)

    assert response.status_code == 200, path
    assert isinstance(response.json(), list), path


@pytest.mark.django_db()
def test_the_schedule_queue_is_not_ordered_by_the_api(api_client):
    """
    The queue playout broadcasts is left in the database's own order.

    Ordering is available on request (that is what ``?ordering=`` is for), but a
    default here would decide the running order for the station.
    """
    assert not Show.objects.exists()

    instance = baker.make(
        "schedule.ShowInstance",
        starts_at="2044-01-01 00:00:00+00:00",
        ends_at="2044-01-01 01:00:00+00:00",
    )
    file = baker.make("storage.File", length=timedelta(minutes=3))

    for hour in range(3):
        baker.make(
            "schedule.Schedule",
            instance=instance,
            file=file,
            starts_at=f"2044-01-01 0{hour}:00:00+00:00",
            ends_at=f"2044-01-01 0{hour}:30:00+00:00",
            position=hour,
        )

    assert ShowInstance.objects.count() == 1

    # The read serializer renders the schedule row's own field names.
    plain = [row["starts_at"] for row in api_client.get("/api/v2/schedule").json()]
    reversed_order = [
        row["starts_at"]
        for row in api_client.get("/api/v2/schedule?ordering=-starts_at").json()
    ]

    assert sorted(plain) == sorted(reversed_order)
    assert reversed_order == sorted(reversed_order, reverse=True)
