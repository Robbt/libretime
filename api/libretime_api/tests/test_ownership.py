"""
Creating something and being able to change it afterwards.

The ownership rules only mean anything if a created row carries the person who
created it. Without the stamp, a host who makes a playlist through the api owns
nothing, and the station's answer to their next request is 403 on their own work -
which is what the legacy application never does: a playlist is made with
``creator_id`` set to the logged in user.
"""

# A test that takes a fixture rebinds the fixture function's name as its own parameter:
# that is how pytest injects, and no spelling of it satisfies pylint's shadowing check.
# The database fixtures are likewise requested for their effect, not their value.
# pylint: disable=redefined-outer-name,unused-argument


import pytest
from django.apps import apps
from django.conf import settings
from model_bakery import baker
from rest_framework.test import APIClient

from ..core.models import Role, User
from ..permissions import may_act
from ..schedule.models import Playlist


def make_user(role: Role, username: str) -> User:
    return User.objects.create_user(
        role=role,
        username=username,
        password="test",
        email=f"{username}@example.org",
        first_name="Own",
        last_name="Er",
    )


@pytest.fixture()
def host(db) -> User:
    return make_user(Role.HOST, "creator-host")


@pytest.fixture()
def other(db) -> User:
    return make_user(Role.HOST, "creator-other")


@pytest.fixture()
def manager(db) -> User:
    return make_user(Role.MANAGER, "creator-manager")


def client_as(user: User | None) -> APIClient:
    client = APIClient()

    if user is None:

        client.credentials(
            HTTP_AUTHORIZATION=f"Api-Key {settings.CONFIG.general.api_key}"
        )
    else:
        client.force_authenticate(user=user)

    return client


class TestStampedOwner:
    def test_a_host_who_makes_a_playlist_can_change_it(self, host):
        client = client_as(host)

        created = client.post(
            "/api/v2/playlists", {"name": "my set", "description": ""}
        )

        assert created.status_code == 201, created.data

        playlist_id = created.json()["id"]

        assert host.has_perm("change_own_playlist")
        updated = client.patch(
            f"/api/v2/playlists/{playlist_id}", {"name": "my set vol 2"}
        )

        assert updated.status_code == 200, updated.data

    def test_every_creation_names_its_author(self, host):
        """Each kind of thing a host may make, and the row it leaves behind."""

        client = client_as(host)

        made = {
            "schedule.Playlist": client.post("/api/v2/playlists", {"name": "p"}),
            "schedule.SmartBlock": client.post("/api/v2/smart-blocks", {"name": "b"}),
            "schedule.Webstream": client.post(
                "/api/v2/webstreams",
                {
                    "name": "w",
                    "description": "a station stream",
                    "url": "http://example.org/stream.mp3",
                    "length": "00:00:00",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            ),
        }

        for model, response in made.items():
            assert response.status_code == 201, (model, response.data)

            row = apps.get_model(model).objects.get(pk=response.json()["id"])

            assert row.owner == host, model

    def test_a_named_owner_is_not_overwritten(self, host, manager):
        """A manager filing something for somebody else means it."""
        response = client_as(manager).post(
            "/api/v2/playlists",
            {"name": "for the host", "description": "", "owner": host.pk},
        )

        assert response.status_code == 201, response.data

        assert Playlist.objects.get(pk=response.json()["id"]).owner == host

    def test_a_service_call_is_not_attributed_to_a_person(self, db):
        """
        An api key is not a user, and inventing an owner would blame a person for
        a machine's import. The column stays empty, as the analyzer leaves it.
        """
        response = client_as(None).post(
            "/api/v2/playlists", {"name": "imported", "description": ""}
        )

        assert response.status_code == 201, response.data

        assert Playlist.objects.get(pk=response.json()["id"]).owner is None

    def test_a_guest_creates_nothing(self, db):
        guest = make_user(Role.GUEST, "creator-guest")

        response = client_as(guest).post(
            "/api/v2/playlists", {"name": "nope", "description": ""}
        )

        assert response.status_code == 403


class TestChildrenFollowTheirParent:
    """
    Adding a row to something is a change to that something. The legacy code asks
    it plainly: Playlist.php and Block.php check whether the caller may edit the
    playlist or block, rather than whether they may make rows.
    """

    def test_a_host_may_add_a_song_to_their_own_playlist(self, host):
        playlist = baker.make("schedule.Playlist", owner=host)

        response = client_as(host).post(
            "/api/v2/playlist-contents",
            {"playlist": playlist.pk, "kind": 0, "offset": 0},
        )

        assert response.status_code == 201, response.data

    def test_a_host_may_not_add_a_song_to_someone_elses_playlist(self, host, other):
        playlist = baker.make("schedule.Playlist", owner=other)

        response = client_as(host).post(
            "/api/v2/playlist-contents",
            {"playlist": playlist.pk, "kind": 0, "offset": 0},
        )

        assert response.status_code == 403

    def test_a_host_may_not_write_into_a_smart_block_they_did_not_make(
        self, host, other
    ):
        block = baker.make("schedule.SmartBlock", owner=other)

        response = client_as(host).post(
            "/api/v2/smart-block-contents",
            {"block": block.pk, "offset": 0},
        )

        assert response.status_code == 403

    def test_a_manager_may_add_to_anyones_playlist(self, other):
        playlist = baker.make("schedule.Playlist", owner=other)

        response = client_as(make_user(Role.MANAGER, "creator-manager-2")).post(
            "/api/v2/playlist-contents",
            {"playlist": playlist.pk, "kind": 0, "offset": 0},
        )

        assert response.status_code == 201, response.data

    def test_content_in_a_hosted_show_may_be_added_by_its_host(self, host):
        """``broadcasted`` is an integer column, not a flag, so it takes a 0."""
        show = baker.make("schedule.Show")
        show.hosts.add(host)
        instance = baker.make("schedule.ShowInstance", show=show)

        allowed = client_as(host).post(
            "/api/v2/schedule",
            {
                "instance": instance.pk,
                "position": 0,
                "starts_at": "2030-01-05T01:00:00Z",
                "ends_at": "2030-01-05T01:05:00Z",
                "cue_in": "00:00:00",
                "cue_out": "00:05:00",
                "broadcasted": 0,
            },
        )

        assert allowed.status_code in (200, 201), allowed.data

    def test_content_of_another_show_may_not(self, host, other):
        show = baker.make("schedule.Show")
        show.hosts.add(other)
        instance = baker.make("schedule.ShowInstance", show=show)

        response = client_as(host).post(
            "/api/v2/schedule",
            {
                "instance": instance.pk,
                "position": 0,
                "starts_at": "2030-01-06T01:00:00Z",
                "ends_at": "2030-01-06T01:05:00Z",
                "cue_in": "00:00:00",
                "cue_out": "00:05:00",
                "broadcasted": 0,
            },
        )

        assert response.status_code == 403


class TestMayAct:
    """The rule the parent checks and the object checks share."""

    def test_an_owner_may_act_and_a_stranger_may_not(self, host, other):
        playlist = baker.make("schedule.Playlist", owner=host)
        stranger = baker.make("schedule.Playlist", owner=other)

        assert may_act(host, playlist, "change")
        assert not may_act(host, stranger, "change")
        assert may_act(host, playlist, "delete")

    def test_nobody_owns_a_row_without_an_owner_but_a_manager_acts(self, other):
        orphan = baker.make("schedule.Playlist", owner=None)

        assert not may_act(other, orphan, "change")
        assert may_act(make_user(Role.MANAGER, "orphan-manager"), orphan, "change")
