"""
The session cookie, from the api side.

Four things a browser needs and had to go to the legacy application for before:
log in, log out, ask who it is, and ask what it is allowed to reach. The cookie
itself and the rules around it are in :mod:`libretime_api.core.auth.php_session`
and :mod:`libretime_api.core.auth.session_store`.
"""

from __future__ import annotations

from django.db.models import F, Value
from django.db.models.functions import Coalesce
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...permission_constants import GROUPS
from ..auth import COOKIE_NAME, csrf_token
from ..auth.session_store import SESSION_LIFETIME, create, destroy
from ..models import LoginAttempt, Session, User
from ..models.role import Role

# The message the legacy login page shows, kept identical so that a station's
# translators and its users meet one wording rather than two. It says nothing
# about which half was wrong on purpose.
WRONG_CREDENTIALS = "Wrong username or password provided."

# cc_login_attempts.ip is char(32), which an IPv6 address is not.
MAX_IP_LENGTH = 32


def _cookie_kwargs(request) -> dict:
    """
    How the session cookie is set.

    ``path=/`` is what makes one login cover both the legacy screens and the api:
    nginx serves both from the same origin, and a cookie scoped to ``/api`` would
    leave the station logged out. ``httponly`` because nothing should read this
    out of ``document.cookie``, and ``samesite=lax`` because a cross-site image or
    link must not carry it - which is also why the csrf token is still checked for
    anything that changes something.
    """
    return {
        "max_age": SESSION_LIFETIME,
        "path": "/",
        "httponly": True,
        "samesite": "Lax",
        "secure": request.is_secure(),
    }


def _increase_login_attempts(username: str | None, remote_addr: str | None) -> None:
    """
    Count a failed login, the way ``LoginController::loginError`` does.

    Both counters are kept because both are read: the per-user one travels with
    the account, the per-address one is what tools watching the station look at.
    Neither blocks anything today, in php or here - see the note in the tests.
    """
    if username:
        # Coalesce because rows written before this column had a default hold
        # NULL, and NULL + 1 is NULL: the count of a stubborn attacker would never
        # rise. The legacy update has the same arithmetic and the same trap.
        User.objects.filter(username=username).update(
            login_attempts=Coalesce(F("login_attempts"), Value(0)) + 1
        )

    if remote_addr and len(remote_addr) <= MAX_IP_LENGTH:
        row, created = LoginAttempt.objects.get_or_create(
            ip=remote_addr, defaults={"attempts": 1}
        )

        if not created:
            # Read, add, write. Two failures racing can lose a count, which is
            # what the raw SQL in Application_Model_LoginAttempts does too - and
            # is harmless only because nothing blocks on these numbers yet.
            LoginAttempt.objects.filter(ip=remote_addr).update(
                attempts=(row.attempts or 0) + 1
            )


def _reset_login_attempts(username: str, remote_addr: str | None) -> None:
    User.objects.filter(username=username).update(login_attempts=0)

    if remote_addr and len(remote_addr) <= MAX_IP_LENGTH:
        LoginAttempt.objects.filter(ip=remote_addr).delete()


class LoginRequestSerializer(serializers.Serializer):
    """What a sign-in request carries."""

    username = serializers.CharField()
    password = serializers.CharField()


class SessionUserSerializer(serializers.Serializer):
    """The person a session belongs to."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    role = serializers.CharField(help_text="The single-letter role code, as stored.")
    role_label = serializers.CharField(help_text="The same role, spelled out.")
    email = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class CsrfTokenSerializer(serializers.Serializer):
    """The token a session has to present back to write anything."""

    csrf_token = serializers.CharField()


class SessionPermissionsSerializer(serializers.Serializer):
    """What the caller's role may do, in the names the api checks."""

    role = serializers.CharField()
    is_superuser = serializers.BooleanField()
    permissions = serializers.ListField(child=serializers.CharField())


@extend_schema(tags=["core"])
class SessionView(APIView):
    """
    ``POST`` to log in, ``GET`` to ask who you are, ``DELETE`` to log out.

    Only json is accepted, and that is a security measure rather than taste: a
    login form on another site can post a form body to this url with the
    victim's browser, but it cannot post json, so an endpoint that reads nothing
    else cannot be made to log a victim in to an attacker's account.

    A request that arrives carrying a live session can log in again only by
    presenting that session's csrf token, like any other write - and doing so
    logs the session it arrived on out. Two live sessions for one person in one
    browser is not a state anyone wants: whichever one they log out of, the other
    keeps them in.
    """

    parser_classes = [JSONParser]

    # A login request has no credentials by definition; everything else does.
    def get_permissions(self):
        if self.request.method == "POST":
            return [AllowAny()]

        return [IsAuthenticated()]

    @extend_schema(responses=SessionUserSerializer)
    def get(self, request):
        return Response(SessionUserSerializer(_who(request.user)).data)

    @extend_schema(
        request=LoginRequestSerializer,
        responses={
            200: SessionUserSerializer,
            401: OpenApiResponse(description="Wrong username or password."),
        },
    )
    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")

        user = (
            User.objects.filter(username=username).first()
            if isinstance(username, str) and username
            else None
        )

        remote_addr = request.META.get("REMOTE_ADDR")

        if (
            user is None
            or not isinstance(password, str)
            or not user.check_password(password)
        ):
            # One answer whether the account is unknown or the password was
            # wrong, and the counter moves in both cases: the count is a fact
            # about the address, not about whether the account exists. The
            # per-user count can only move when there is a user to move it on.
            _increase_login_attempts(
                user.username if user is not None else None, remote_addr
            )

            return Response(
                {"detail": WRONG_CREDENTIALS},
                status=status.HTTP_403_FORBIDDEN,
            )

        # The session this request arrived on, if there was one, is over: its
        # credentials have just been used to open a new one, and leaving it alive
        # would mean a logout that did not log out.
        if isinstance(request.auth, Session):
            destroy(request.auth)

        session = create(user)
        _reset_login_attempts(user.username, remote_addr)

        response = Response(
            {**_who(user), "csrf_token": csrf_token(session.data)},
            status=status.HTTP_200_OK,
        )
        response.set_cookie(COOKIE_NAME, session.id, **_cookie_kwargs(request))

        return response

    @extend_schema(responses={204: None})
    def delete(self, request):
        """
        Log out of the station, not just of this request.

        The session row is cleared rather than deleted - the same thing the legacy
        ``logoutAction`` leaves behind - and the cookie is dropped so the browser
        stops offering it. A caller authenticated some other way (an api key, a
        django session from the browsable api) has no session row here to clear,
        and gets told so rather than a logout that did nothing.
        """
        if not isinstance(request.auth, Session):
            raise PermissionDenied("This request did not arrive on a station session.")

        destroy(request.auth)

        response = Response(status=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(COOKIE_NAME, path="/")

        return response


class CsrfTokenView(APIView):
    """
    The token the caller's own session expects back on a request that writes.

    A page replacing a legacy screen needs this to change anything: the cookie
    proves which user it is, and this proves the page asked for it. It is handed
    out over an ordinary authenticated read, which is safe because the same-origin
    policy is what stops any other page reading the answer - and it is only ever
    the caller's own token, never somebody else's.

    Callers authenticated some other way get nothing. An API key does not need a
    CSRF token, and basic authentication has no session to read one from, so
    there is no case where returning a token here would be correct.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=CsrfTokenSerializer)
    def get(self, request):
        if not isinstance(request.auth, Session):
            raise PermissionDenied("Only a legacy session has a CSRF token.")

        token = csrf_token(request.auth.data)

        if token is None:
            raise PermissionDenied("This session carries no CSRF token.")

        return Response({"csrf_token": token})


@extend_schema(tags=["core"])
class SessionPermissionsView(APIView):
    """
    What the caller's role may do, in the words the api itself uses.

    A browser replacing a legacy screen needs this to draw its own menus. The
    legacy application answers the same question from
    ``application/configs/ACL.php``, which lists controllers against roles; that
    list is about php's own pages and says nothing about ``/api/v2``, so the api
    reports its own permission names and the interface decides what to show.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=SessionPermissionsSerializer)
    def get(self, request):
        user: User = request.user

        if user.is_superuser():
            # Admin and superadmin: the role lists say nothing they have to be
            # told, and leaving them out would draw a smaller menu than the user
            # is entitled to.
            permissions = sorted({name for group in GROUPS.values() for name in group})
        else:
            permissions = sorted(set(GROUPS.get(user.role, [])))

        return Response(
            {
                "role": user.role,
                "is_superuser": user.is_superuser(),
                "permissions": permissions,
            }
        )


def _who(user: User) -> dict:
    return {
        "id": user.pk,
        "username": user.username,
        "role": user.role,
        "role_label": Role(user.role).label if user.role in Role.values else user.role,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


__all__ = ["CsrfTokenView", "SessionPermissionsView", "SessionView"]
