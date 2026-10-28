"""
Ask the API the same questions ``run-php.php`` asks the legacy application.

Same ``cases.txt``, same output format, real permission code: the real
``IsSystemTokenOrUser``, the real role lists in ``permission_constants``, the real
``get_owner()`` implementations and the real viewsets. Nothing here reimplements a
rule, which is the only way the comparison says anything.
"""

import os
import sys
from pathlib import Path

import django

django.setup()

from libretime_api.core.models import User  # noqa: E402
from libretime_api.core.views.user import UserViewSet  # noqa: E402
from libretime_api.permissions import IsSystemTokenOrUser  # noqa: E402
from libretime_api.podcasts.views.podcast import (  # noqa: E402
    PodcastEpisodeViewSet,
    PodcastViewSet,
)
from libretime_api.schedule.views.playlist import (  # noqa: E402
    PlaylistContentViewSet,
    PlaylistViewSet,
)
from libretime_api.schedule.views.schedule import ScheduleViewSet  # noqa: E402
from libretime_api.schedule.views.show import (  # noqa: E402
    ShowDaysViewSet,
    ShowHostViewSet,
    ShowInstanceViewSet,
    ShowRebroadcastViewSet,
    ShowViewSet,
)
from libretime_api.schedule.views.smart_block import (  # noqa: E402
    SmartBlockContentViewSet,
    SmartBlockCriteriaViewSet,
    SmartBlockViewSet,
)
from libretime_api.schedule.views.webstream import (  # noqa: E402
    WebstreamMetadataViewSet,
    WebstreamViewSet,
)
from libretime_api.storage.views.file import FileViewSet  # noqa: E402
from rest_framework.request import Request  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402

# The api resource that stands for each fixture object kind. The object is read
# through the viewset's own queryset, so a kind mapped to the wrong viewset shows
# up as a mismatch rather than a silent agreement.
KINDS = {
    "show": ShowViewSet,
    "showdays": ShowDaysViewSet,
    "showrebroadcast": ShowRebroadcastViewSet,
    "showhost": ShowHostViewSet,
    "showinstance": ShowInstanceViewSet,
    "schedule": ScheduleViewSet,
    "file": FileViewSet,
    "playlist": PlaylistViewSet,
    "playlistcontent": PlaylistContentViewSet,
    "smartblock": SmartBlockViewSet,
    "smartblockcontent": SmartBlockContentViewSet,
    "smartblockcriteria": SmartBlockCriteriaViewSet,
    "webstream": WebstreamViewSet,
    "webstreammetadata": WebstreamMetadataViewSet,
    "podcast": PodcastViewSet,
    "podcastepisode": PodcastEpisodeViewSet,
    "user": UserViewSet,
}

METHODS = {"change": "patch", "delete": "delete"}


def decide(user_id: int, kind: str, obj_id: int, action: str):
    """
    Whether the api lets this user perform this action on this object.

    Both hooks are asked, as a request goes through them in that order:
    ``has_permission`` on the list, then ``has_object_permission`` once the row is
    known. A viewset that only ever answers the first still has to fail the second
    for the object that is not the caller's.
    """
    view_class = KINDS[kind]
    user = User.objects.get(pk=user_id)
    obj = view_class.queryset.model.objects.get(pk=obj_id)

    factory = APIRequestFactory()
    path = f"/api/v2/{kind}/{obj_id}"
    handler = getattr(factory, METHODS[action])
    wrapped = handler(path, {}, format="json")
    request = Request(wrapped)
    # Assigning through the wrapper's user setter, which is also what a view
    # does once an authenticator has answered. The helper in rest_framework.test
    # only takes effect on a request a view has not wrapped yet.
    request.user = user

    view = view_class()
    view.action = "partial_update" if action == "change" else "destroy"
    view.args = ()
    view.kwargs = {"pk": obj_id}
    view.request = request
    view.format_kwarg = None

    permission = IsSystemTokenOrUser()

    if not permission.has_permission(request, view):
        return False, f"role={user.role} list"

    if not permission.has_object_permission(request, view, obj):
        return False, f"role={user.role} object"

    return True, f"role={user.role} allowed"


# Where cases.txt lives. ab.sh copies this script to /tmp in container mode, which is
# why it cannot simply read alongside __file__ there, so the directory is named
# explicitly by the caller: /tools in the container, the harness directory on a plain
# host. The fallback is for running this file straight from a checkout by hand.
FIXTURES = os.environ.get("AB_FIXTURES") or str(Path(__file__).resolve().parent)


def main() -> int:
    with open(f"{FIXTURES}/cases.txt", encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    for line in lines:
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        user_id, obj, action = line.split()
        kind, obj_id = obj.split(":")

        try:
            allowed, why = decide(int(user_id), kind, int(obj_id), action)
            verdict = "allow" if allowed else "deny"
        except Exception as error:  # noqa: BLE001
            # A role the api cannot look up is a finding, not a reason to stop
            # the run: report it as its own verdict and let compare_permission.py show it.
            allowed, why = None, f"{type(error).__name__}: {error}"
            verdict = "error"

        sys.stdout.write(f"{user_id} {kind} {obj_id} {action}|" f"{verdict}|{why}\n")

    sys.stdout.flush()

    return 0


sys.exit(main())
