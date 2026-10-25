from secrets import compare_digest

from django.conf import settings
from django.db.models import QuerySet
from django.http import RawPostDataException
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from .ownership import owner_of_parent

# A POST asks for an "add" permission rather than a "change" one. It used to ask
# for change, which left the add_* names in permission_constants checked by nothing
# while a host - who may upload a file and make a playlist in the legacy station,
# and is given add_file and add_playlist for it - could create nothing here.
REQUEST_PERMISSION_TYPE_MAP = {
    "GET": "view",
    "HEAD": "view",
    "OPTIONS": "view",
    "POST": "add",
    "PUT": "change",
    "DELETE": "delete",
    "PATCH": "change",
}


def permission_names(request, view) -> tuple[str | None, str | None]:
    """
    The permissions this request could satisfy: the role's, and the owner's.

    Two names come back rather than one because the two questions are asked at
    different moments. On a list request there is no row to inspect, so all that
    can be established is whether this role may change rows of this kind at all.
    On a detail request the row is known, and the ownership permission means
    something - which is when the second name gets its extra check.

    ``None`` means no permission of that kind exists, which denies.
    """
    permission_type = REQUEST_PERMISSION_TYPE_MAP.get(request.method)

    if permission_type is None:
        return None, None

    if view.__class__.__name__ == "APIRootView":
        # Not attached to a model: it says only that the caller may open the api
        # at all, which every role does, and the model permissions decide what
        # they find inside.
        return f"{permission_type}_apiroot", None

    model = getattr(view, "model_permission_name", None)

    if model is None:
        return None, None

    if permission_type == "view":
        # Reading is never decided by ownership. The legacy application gates the
        # library, the playlists and the calendar by page, and shows a host every
        # row - the api says the same with its view_* permissions.
        return f"view_{model}", None

    if permission_type == "add":
        # There is no row yet to own, so nothing to ask about one. What a create
        # does require, when the row will belong to something else, is checked
        # against that parent - see has_permission.
        return f"add_{model}", None

    return (
        f"{permission_type}_{model}",
        f"{permission_type}_own_{model}",
    )


def may_create_within(user, view, parent) -> bool:
    """
    Whether this user may put a new row into ``parent``.

    The permission names come from the model being created, since that is where
    the role's rights are written; the ownership comes from the parent, since a
    child has no owner of its own. A role that may change any row of the kind -
    a manager, an admin - is not asked about the parent at all.
    """
    model = view.queryset.model._meta.model_name

    if user.has_perm(f"change_{model}"):
        return True

    if not user.has_perm(f"change_own_{model}"):
        return False

    try:
        return is_owner(user, parent)
    except AttributeError:
        return False


def may_act(user, obj, permission_type: str) -> bool:
    """
    Whether this user may act on this object, by role or by owning it.

    The object's own model name is used rather than the view's, so the same rule
    answers for a parent: adding a song to a playlist is a change to the playlist,
    and the playlist is the thing whose owner matters.
    """
    model = obj._meta.model_name

    if user.has_perm(f"{permission_type}_{model}", obj):
        return True

    if user.has_perm(f"{permission_type}_own_{model}", obj):
        try:
            return is_owner(user, obj)
        except AttributeError:
            return False

    return False


def is_owner(user, obj) -> bool:
    """
    Whether this object belongs to this user.

    ``get_owner()`` answers with the one user who created a thing, or with the set
    of users responsible for it - a show's hosts, which is the ``cc_show_hosts``
    row behind ``Application_Model_User::canSchedule`` in the legacy code.
    """
    owner = obj.get_owner()

    if owner is None:
        # A row nobody owns - an import that ran before owners were recorded, a
        # file whose user has since been deleted. Only a manager may change it,
        # which is what the role permission already decided.
        return False

    if isinstance(owner, QuerySet):
        return owner.filter(pk=user.pk).exists()

    return owner == user


def check_authorization_header(request: Request) -> bool:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Api-Key"):
        token = auth_header.split()[1]
        return compare_digest(token, settings.CONFIG.general.api_key)
    return False


class IsAdminOrOwnUser(BasePermission):
    """
    Implements Django Rest Framework permissions. This is separate from
    Django's standard permission system. For details see
    https://www.django-rest-framework.org/api-guide/permissions/#custom-permissions
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            # A request carrying only the station token has no user at all, and
            # AnonymousUser.is_superuser is a property while the station's own user
            # model defines it as a method. Calling it was a TypeError, so this view
            # answered 500 to every token request instead of refusing it.
            return False

        return request.user.is_superuser()

    def has_object_permission(self, request, view, obj):
        if not request.user.is_authenticated:
            return False

        return request.user.is_superuser() or obj.username == request.user


class IsSystemTokenOrUser(BasePermission):
    """
    Implements Django Rest Framework permissions. This is separate from
    Django's standard permission system. For details see
    https://www.django-rest-framework.org/api-guide/permissions/#custom-permissions

    This permission allows services (liquidsoap, 3rd-party, etc) to connect with
    an API-Key header. All standard-users (i.e. not using the API-Key) have their
    permissions checked against Django's standard permission system.
    """

    def has_permission(self, request, view):
        if not _is_authenticated(request):
            return check_authorization_header(request)

        allowed = any(
            name is not None and request.user.has_perm(name)
            for name in permission_names(request, view)
        )

        if request.method != "POST":
            return allowed

        # A create has no row to inspect, but a row that belongs to something else
        # - a song in a playlist, media in a show instance - belongs to whoever owns
        # that thing, and the legacy application decides accordingly: Playlist.php
        # and Block.php ask whether the caller may edit the playlist or block, and
        # Scheduler.php asks canSchedule for the show. So the parent named in the
        # body is the object the question is about, in both directions:
        #
        #   - a role may create rows of a kind it cannot change anywhere
        #     (a host adds media to a show it hosts without any add_schedule
        #     permission, because the show is theirs to put things into);
        #   - a role may not create rows of a kind it may only change when it owns
        #     (a host cannot add a song to somebody else's playlist).
        parent = owner_of_parent(view, _body(request))

        if parent is None:
            return allowed

        # When there is a parent, it decides. The role's create permission is not
        # simply added to that answer: a host has no add_schedule at all, because
        # "add media to a show" is not a right a station grants in the abstract,
        # and requiring it would refuse the very case the parent rule exists for.
        return may_create_within(request.user, view, parent)

    def has_object_permission(self, request, view, obj):
        """
        Allow the role outright, or the owner of this particular row.

        The ownership question is asked about ``obj`` and nothing else. It used to
        be answered by looking at every row of the table to see whether exactly one
        user owned all of them, which denied a host their own file as soon as
        anybody else had uploaded anything - that is, in every station with more
        than one person in it.
        """
        if not _is_authenticated(request):
            return check_authorization_header(request)

        role_permission, owner_permission = permission_names(request, view)

        if role_permission is not None and request.user.has_perm(role_permission, obj):
            return True

        if owner_permission is not None and request.user.has_perm(
            owner_permission, obj
        ):
            # A model with no ownership rule grants nothing on the owner's terms,
            # rather than becoming a manager by accident.
            try:
                return is_owner(request.user, obj)
            except AttributeError:
                return False

        return False


def _is_authenticated(request) -> bool:
    return bool(request.user and request.user.is_authenticated)


def _body(request):
    """
    The submitted data, or nothing when there is none to read.

    A create is checked against its parent, which means looking at what was
    posted. A request with no parser, or one whose body is not a mapping - an
    upload that has not been parsed yet - simply has no parent to check here, and
    the role permission stays the answer.
    """
    try:
        data = request.data
    except (ParseError, UnsupportedMediaType, RawPostDataException):
        # Nothing readable to check against: an upload that has not been parsed, a body
        # whose content type has no parser, a stream already consumed by something
        # earlier. The request is not wrong; there is simply no parent named in it, and
        # the role permission stays the answer.
        return None

    return data if hasattr(data, "get") else None
