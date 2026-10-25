"""
Authentication against the session the legacy service already issued.

A browser talking to the station already carries a ``PHPSESSID`` cookie, and the
legacy application keeps its sessions in the ``sessions`` table. This class reads
that row, finds the identity the legacy ``Zend_Auth`` storage wrote into it, and
returns the matching user - so a user who logged in once is authenticated to the
API as well, without a second login and without the API holding its own copy of
any credential.

Why a write also needs the session token
----------------------------------------

Accepting a cookie as proof of identity is exactly what makes cross-site request
forgery possible: another page could make the browser send an authenticated
``POST`` it did not ask for. Cookies arrive whether the browser was sent there or
not, so a cookie can prove which user, but not that this page asked.

The legacy service solves this with a random token kept in the session and sent
back as a ``csrf_token`` field - ``SecurityHelper::verifyCSRFToken`` compares the
two. That token sits in the very payload this class already reads, so the API can
apply the same rule: a request that changes something has to present the token its
own session holds. A cross-site page cannot read it - same-origin policy stops it
from reading the response of ``GET /api/v2/session/csrf-token`` - and a cross-site
form cannot set a request header, so a custom header is out of reach for an
attacker's page and is what JSON writes are expected to carry.

Django's own ``CsrfViewMiddleware`` is not the thing enforcing this. It validates
the ``django_session`` secret, which a legacy session does not have, so the check
below is the only one standing between a forged request and a write.
"""

from __future__ import annotations

import logging
from secrets import compare_digest
from typing import Any

from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request

from ..models import Session, User
from ._phpserialize import PhpDecodeError, loads

logger = logging.getLogger(__name__)

# The name the legacy application's session cookie has had since Airtime.
COOKIE_NAME = "PHPSESSID"

# Zend_Auth_Storage_Session('libretime') in Application_Model_Auth.
SESSION_NAMESPACE = "libretime"
IDENTITY_KEY = "storage"

# Zend_Session_Namespace('csrf_namespace')->authtoken, as written by
# PageLayoutInitPlugin and compared by SecurityHelper::verifyCSRFToken.
CSRF_NAMESPACE = "csrf_namespace"
CSRF_KEY = "authtoken"

# The name Django's own AJAX convention uses, so browser tooling already knows it.
CSRF_HEADER = "HTTP_X_CSRFTOKEN"
# The name the legacy application uses for the same value in a form body.
CSRF_FIELD = "csrf_token"

# Only these two body formats carry the field, and reading the body during
# authentication is only safe for the parsers that know how to cache it.
CSRF_FIELD_MEDIA_TYPES = (FormParser.media_type, MultiPartParser.media_type)

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class PhpSessionScheme(OpenApiAuthenticationExtension):
    """
    How to describe the session cookie in the schema.

    Without this, drf-spectacular warns once per view that it cannot resolve the
    authenticator, and the shipped documentation page describes every endpoint as
    taking no credentials at all - which is close to the opposite of the truth.
    """

    target_class = "libretime_api.core.auth.php_session.PhpSessionAuthentication"
    name = "phpSessionCookie"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "cookie", "name": COOKIE_NAME}


class PhpSessionAuthentication(BaseAuthentication):
    """
    Authenticate a request carrying the legacy session cookie.

    Returns ``None``, meaning "not my credentials", whenever the cookie is
    absent, the session is unknown or expired, or nobody is logged in to it - so
    that API-key and basic authentication keep working unchanged. A request that
    carries a live session and tries to change something is authenticated only
    when it also presents that session's CSRF token.
    """

    def authenticate(self, request: Request) -> tuple[User, Any] | None:
        session_id = request.COOKIES.get(COOKIE_NAME)

        if not session_id:
            return None

        record = Session.objects.filter(pk=session_id).first()

        if record is None:
            return None

        if not self.is_live(record):
            logger.debug("ignoring expired session %s", _mask(session_id))

            return None

        login = identity(record.data)

        if login is None:
            return None

        user = User.objects.filter(username=login).first()

        if user is None:
            # The session outlived its user, or the legacy auth adapter allows a
            # login this installation has no row for. Either way nobody is
            # authenticated by it.
            logger.warning("session for %s has no matching user", _mask(session_id))

            return None

        if request.method not in SAFE_METHODS:
            self.verify_csrf(request, record, user)

        return user, record

    # No authenticate_header() on purpose. Rest framework turns a 401 into a 403
    # when the first authenticator class advertises no scheme, and this API has
    # always answered an anonymous request with 403. Offering a scheme here would
    # quietly change that for every endpoint, which is not what a read bridge is
    # for. See libretime_api/tests/test_permissions.py.

    @staticmethod
    def verify_csrf(request: Request, record: Session, user: User) -> None:
        """
        Refuse a write that did not present the token its session holds.

        The header is tried first, then a ``csrf_token`` field, in that order and
        for a reason: only a body can carry the field, and a body can be sent by a
        forged form, so the field is the weaker of the two - it is accepted to stay
        compatible with the legacy convention, and it is still bound to the session
        rather than to the browser. JSON bodies are deliberately not looked at. A
        reader would have parsed the stream during authentication, before the view
        knows which parser it wanted, and a write that is worth forging is not
        worth that trade. Send the header with JSON.
        """
        expected = csrf_token(record.data)

        if not expected:
            raise AuthenticationFailed(
                f"The session of user {user.username!r} has no CSRF token, so "
                "this API cannot check a write it carries."
            )

        observed = request.META.get(CSRF_HEADER, "")

        if not observed and _media_type(request) in CSRF_FIELD_MEDIA_TYPES:
            observed = request.data.get(CSRF_FIELD, "")

        # compare_digest rather than ==, which is what the legacy helper uses for
        # two sha1 hex strings: an early-exit comparison of a secret is a needless
        # way to leak it a character at a time.
        if not observed or not compare_digest(str(observed), expected):
            raise AuthenticationFailed(
                "Cross-site request forgery check failed: send the token from "
                "GET /api/v2/session/csrf-token as the X-CSRFToken header, or as "
                f"a {CSRF_FIELD} field in a form body."
            )

    @staticmethod
    def is_live(record: Session) -> bool:
        """
        Whether the session has not expired.

        The same rule ``core.tasks.clean_expired_sessions`` applies - modified
        plus lifetime - so the API and the garbage collector cannot disagree about
        whether a session is still valid.
        """
        if record.updated_at is None or record.lifetime is None:
            return False

        return record.updated_at + record.lifetime >= int(timezone.now().timestamp())


def identity(payload: str | None) -> str | None:
    """
    The login of the user a session belongs to, ``None`` when nobody is logged in.

    ``Zend_Auth`` writes the whole ``cc_subjs`` row as a ``stdClass``, so an
    anonymous session has a null here and a logged in one a mapping with a login.
    """
    if not payload:
        return None

    try:
        decoded = loads(payload)
    except PhpDecodeError as error:
        logger.warning("could not read a legacy session payload: %s", error)

        return None

    if not isinstance(decoded, dict):
        return None

    namespaced = decoded.get(SESSION_NAMESPACE)

    if not isinstance(namespaced, dict):
        return None

    stored = namespaced.get(IDENTITY_KEY)

    if stored is None:
        return None

    # An older release stored the plain login string rather than the row.
    if isinstance(stored, str):
        return stored or None

    if isinstance(stored, dict):
        login = stored.get("login")

        return login if isinstance(login, str) and login else None

    logger.warning("legacy session identity has an unexpected shape")

    return None


def _media_type(request: Request) -> str:
    """
    The request's media type on its own.

    ``content_type`` keeps the parameters - a multipart body arrives as
    ``multipart/form-data; boundary=...; charset=utf-8`` - so it cannot be
    compared to a media type directly.
    """
    return (request.content_type or "").split(";")[0].strip().lower()


def csrf_token(payload: str | None) -> str | None:
    """
    The CSRF token a session expects back on any request that changes something.

    ``PageLayoutInitPlugin`` keeps it in ``$_SESSION['csrf_namespace']['authtoken']``
    as a sha1 hex string, generated for anonymous sessions as well as logged in
    ones, so a session that reached this far normally has one.
    """
    if not payload:
        return None

    try:
        decoded = loads(payload)
    except PhpDecodeError:
        return None

    if not isinstance(decoded, dict):
        return None

    namespaced = decoded.get(CSRF_NAMESPACE)

    if not isinstance(namespaced, dict):
        return None

    token = namespaced.get(CSRF_KEY)

    return token if isinstance(token, str) and token else None


def _mask(session_id: str) -> str:
    """A session id is a credential: never log all of it."""
    return f"{session_id[:4]}...{len(session_id)}"
