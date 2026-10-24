"""
Who may change or delete which object.

These are the verdicts of the permission equivalence harness
(``tools/permission-equivalence``), which asks the legacy application and this api
the same 336 questions over a fixture station whose objects belong to different
people. The harness needs a running php stack, so the answers are recorded here as
well: this is what keeps the rules in place in CI, and what tells a reviewer
whether a change here was meant to change the station's behaviour.

The rules themselves, in the words of the legacy code that decides them:

- show structure - a show, its days, its instances, its rebroadcasts, and who
  hosts it - is a manager's call. A host cannot move their own show, because
  ``ScheduleController.php:219`` and ``:698`` ask for a manager and never for the
  host of the show.
- media and the things made of it - files, playlists, smart blocks, podcasts, and
  their contents - belong to whoever created them, and a manager may change
  anybody's.
- a schedule row belongs to the show its instance belongs to, and a host of that
  show may change it. That is ``Application_Model_User::canSchedule``: manager, or
  a row in ``cc_show_hosts``.
- a guest changes nothing anywhere.
"""

# The station fixture asks for db for its effect, not its value, and the
# parametrised ids are the readable part of a failure report
# pylint: disable=unused-argument,invalid-name
import pytest
from model_bakery import baker
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from ..core.models import Role, User
from ..permissions import IsSystemTokenOrUser
from ..podcasts.views.podcast import PodcastEpisodeViewSet, PodcastViewSet
from ..schedule.views.playlist import PlaylistContentViewSet, PlaylistViewSet
from ..schedule.views.schedule import ScheduleViewSet
from ..schedule.views.show import (
    ShowDaysViewSet,
    ShowHostViewSet,
    ShowInstanceViewSet,
    ShowRebroadcastViewSet,
    ShowViewSet,
)
from ..schedule.views.smart_block import (
    SmartBlockContentViewSet,
    SmartBlockCriteriaViewSet,
    SmartBlockViewSet,
)
from ..schedule.views.webstream import WebstreamViewSet
from ..storage.views.file import FileViewSet

MANAGER_KINDS = {
    "show": ShowViewSet,
    "showdays": ShowDaysViewSet,
    "showinstance": ShowInstanceViewSet,
    "showrebroadcast": ShowRebroadcastViewSet,
    "showhost": ShowHostViewSet,
}

# Owned directly by a user, or by the user who owns the thing they hang off.
OWNER_KINDS = {
    "file": FileViewSet,
    "playlist": PlaylistViewSet,
    "playlistcontent": PlaylistContentViewSet,
    "smartblock": SmartBlockViewSet,
    "smartblockcontent": SmartBlockContentViewSet,
    "smartblockcriteria": SmartBlockCriteriaViewSet,
    "webstream": WebstreamViewSet,
    "podcast": PodcastViewSet,
    "podcastepisode": PodcastEpisodeViewSet,
}

ACTIONS = ("change", "delete")


def verdict(user: User, view_class, obj, action: str) -> bool:
    """
    Whether the permission layer lets ``user`` perform ``action`` on ``obj``.

    Both hooks, in the order a request meets them. The harness runs this same
    sequence against the real viewsets; if one of the two changes, the other two
    places to look are ``tools/permission-equivalence/run-py.py`` and that
    directory's README.
    """
    factory = APIRequestFactory()
    handler = getattr(factory, "patch" if action == "change" else "delete")
    wrapped = handler(f"/api/v2/objects/{obj.pk}", {}, format="json")

    request = Request(wrapped)
    request.user = user

    view = view_class()
    view.action = "partial_update" if action == "change" else "destroy"
    view.args = ()
    view.kwargs = {"pk": obj.pk}
    view.request = request
    view.format_kwarg = None

    permission = IsSystemTokenOrUser()

    return permission.has_permission(
        request, view
    ) and permission.has_object_permission(request, view, obj)


@pytest.fixture()
def station(db):
    """
    Two hosts, a manager, an admin, a superadmin and a guest, and one of every
    object owned by each of the two hosts and by nobody at all.

    The second owner matters. With only one owner in a table, a rule that asks who
    owns everything in here agrees with one that asks who owns this row, and the
    difference between them is most of what is under test.
    """
    users = {
        role.value: User.objects.create_user(
            role=role,
            username=f"perm_{role.value}",
            password="test",
            email=f"{role.value}@example.org",
            first_name="Perm",
            last_name="User",
        )
        for role in Role
    }

    mine = users["H"]
    theirs = User.objects.create_user(
        role=Role.HOST,
        username="perm_host_two",
        password="test",
        email="h2@example.org",
        first_name="Other",
        last_name="Host",
    )

    def objects(owner):
        # A show always has a host - a row nobody owns is only interesting for
        # the media, where the column is nullable.
        host = owner if owner is not None else theirs

        show = baker.make("schedule.Show")
        show.hosts.add(host)
        instance = baker.make("schedule.ShowInstance", show=show)
        playlist = baker.make("schedule.Playlist", owner=owner)
        block = baker.make("schedule.SmartBlock", owner=owner)
        podcast = baker.make("podcasts.Podcast", owner=owner)
        schedule = baker.make("schedule.Schedule", instance=instance)

        made = {
            "show": show,
            "showdays": baker.make("schedule.ShowDays", show=show),
            "showinstance": instance,
            "showrebroadcast": baker.make("schedule.ShowRebroadcast", show=show),
            "showhost": baker.make("schedule.ShowHost", show=show, user=host),
            "schedule": schedule,
            "file": baker.make("storage.File", owner=owner),
            "playlist": playlist,
            "playlistcontent": baker.make(
                "schedule.PlaylistContent", playlist=playlist
            ),
            "smartblock": block,
            "smartblockcontent": baker.make("schedule.SmartBlockContent", block=block),
            "smartblockcriteria": baker.make(
                "schedule.SmartBlockCriteria", block=block
            ),
            "podcast": podcast,
            "podcastepisode": baker.make("podcasts.PodcastEpisode", podcast=podcast),
        }

        if owner is not None:
            # Created only when there is somebody to create it: the column is not
            # nullable, so a station never holds a stream that nobody added.
            made["webstream"] = baker.make("schedule.Webstream", owner=owner)

        return made

    return {
        "users": users,
        "theirs": theirs,
        "mine": objects(mine),
        "foreign": objects(theirs),
        "orphaned": objects(None),
    }


@pytest.mark.parametrize("kind", sorted(MANAGER_KINDS))
@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.SUPERADMIN, True),
        (Role.ADMIN, True),
        (Role.MANAGER, True),
        # A host may not touch the shape of a show, not even their own: the
        # legacy controller asks for a manager and stops there.
        (Role.HOST, False),
        (Role.GUEST, False),
    ],
)
def test_show_structure_is_a_managers_call(station, kind, action, role, expected):
    user = station["users"][role.value]

    assert verdict(user, MANAGER_KINDS[kind], station["mine"][kind], action) is expected
    assert (
        verdict(user, MANAGER_KINDS[kind], station["foreign"][kind], action) is expected
    )


@pytest.mark.parametrize("kind", sorted(OWNER_KINDS))
@pytest.mark.parametrize("action", ACTIONS)
def test_a_host_may_change_what_they_own_and_only_that(station, kind, action):
    host = station["users"]["H"]

    assert verdict(host, OWNER_KINDS[kind], station["mine"][kind], action) is True
    assert verdict(host, OWNER_KINDS[kind], station["foreign"][kind], action) is False


@pytest.mark.parametrize("kind", sorted(OWNER_KINDS))
@pytest.mark.parametrize("action", ACTIONS)
def test_a_host_may_not_take_over_what_nobody_owns(station, kind, action):
    """
    Rows with no owner are a manager's to fix, not the first host's to claim.
    """
    if kind not in station["orphaned"]:
        pytest.skip("the schema gives every row of this kind a creator")

    host = station["users"]["H"]

    assert verdict(host, OWNER_KINDS[kind], station["orphaned"][kind], action) is False


@pytest.mark.parametrize("kind", sorted(OWNER_KINDS))
@pytest.mark.parametrize("role", [Role.ADMIN, Role.SUPERADMIN, Role.MANAGER])
@pytest.mark.parametrize("action", ACTIONS)
def test_managers_may_change_anyones_media(station, kind, role, action):
    if kind not in station["orphaned"]:
        pytest.skip("the schema gives every row of this kind a creator")

    user = station["users"][role.value]

    assert verdict(user, OWNER_KINDS[kind], station["mine"][kind], action) is True
    assert verdict(user, OWNER_KINDS[kind], station["foreign"][kind], action) is True
    assert verdict(user, OWNER_KINDS[kind], station["orphaned"][kind], action) is True


@pytest.mark.parametrize("kind", sorted(OWNER_KINDS) + sorted(MANAGER_KINDS))
@pytest.mark.parametrize("action", ACTIONS)
def test_a_guest_changes_nothing(station, kind, action):
    guest = station["users"]["G"]
    view = {**OWNER_KINDS, **MANAGER_KINDS}[kind]

    assert verdict(guest, view, station["mine"][kind], action) is False


class TestScheduleRows:
    """The one rule that is not about who created a row."""

    def test_a_host_may_change_the_content_of_a_show_they_host(self, station):
        host = station["users"]["H"]

        assert verdict(host, ScheduleViewSet, station["mine"]["schedule"], "change")

    def test_a_host_may_not_change_the_content_of_another_show(self, station):
        host = station["users"]["H"]

        assert not verdict(
            host, ScheduleViewSet, station["foreign"]["schedule"], "change"
        )

    def test_a_manager_may_change_either(self, station):
        manager = station["users"]["P"]

        assert verdict(manager, ScheduleViewSet, station["mine"]["schedule"], "delete")
        assert verdict(
            manager, ScheduleViewSet, station["foreign"]["schedule"], "delete"
        )


def test_a_superadmin_is_an_admin_everywhere_including_webstreams(station):
    """
    The one place the api is known to differ from the legacy application.

    ``Application_Model_Webstream::deleteStreams`` lists the manager roles without
    ``UTYPE_SUPERADMIN``, so a superadmin who does not own a webstream cannot
    delete it there. That reads as a slip rather than an intention, so the api does
    not copy it. The harness keeps it visible instead of quiet:
    ``tools/permission-equivalence/compare.py``.
    """
    superadmin = station["users"]["S"]

    assert verdict(
        superadmin, WebstreamViewSet, station["foreign"]["webstream"], "delete"
    )


def test_an_unknown_role_grants_nothing_and_breaks_nothing(station):
    """
    A role the code does not know is not a reason for a 500.

    ``cc_subjs.type`` is a plain column: a future release, or an operator with
    psql, can put anything in it.
    """
    user = User.objects.create_user(
        role="Z",
        username="perm_unknown",
        password="test",
        email="z@example.org",
        first_name="Unknown",
        last_name="Role",
    )

    assert not verdict(user, FileViewSet, station["mine"]["file"], "change")
    assert not verdict(user, FileViewSet, station["mine"]["file"], "delete")
    assert not verdict(user, FileViewSet, station["foreign"]["file"], "change")
