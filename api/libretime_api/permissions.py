from secrets import compare_digest

from django.conf import settings
from django.db.models import QuerySet
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

REQUEST_PERMISSION_TYPE_MAP = {
    "GET": "view",
    "HEAD": "view",
    "OPTIONS": "view",
    "POST": "change",
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

    return (
        f"{permission_type}_{model}",
        f"{permission_type}_own_{model}",
    )


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
        if request.user.is_superuser():
            return True
        return False

    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser():
            return True
        return obj.username == request.user


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

        return any(
            name is not None and request.user.has_perm(name)
            for name in permission_names(request, view)
        )

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
