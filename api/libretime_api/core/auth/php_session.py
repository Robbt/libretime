"""
Authentication against the session the legacy service already issued.

A browser talking to the station already carries a ``PHPSESSID`` cookie, and the
legacy application keeps its sessions in the ``sessions`` table. This class reads
that row, finds the identity the legacy ``Zend_Auth`` storage wrote into it, and
returns the matching user - so a user who logged in once is authenticated to the
API as well, without a second login and without the API holding its own copy of
any credential.

Why it is read-only by design
-----------------------------

Unsafe requests are refused. Accepting a cookie as proof of identity is exactly
what makes cross-site request forgery possible: another page could make the
browser send an authenticated ``POST`` it did not ask for. The legacy service
protects itself with a token in ``$_SESSION['csrf_namespace']`` which this API
cannot validate the way Django's ``CsrfViewMiddleware`` validates its own,
because the two secrets live in different stores.

So this class authenticates reads, which is what a UI needs to replace the
read-only screens, and leaves writes to one of two follow-ups: either the browser
logs in through the API as well and gets a Django session for mutating requests,
or the API learns to verify the legacy token. Either is a design decision rather
than a detail, and refusing in the meantime is the honest answer.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from ..models import Session, User
from ._phpserialize import PhpDecodeError, loads

logger = logging.getLogger(__name__)

# The name the legacy application's session cookie has had since Airtime.
COOKIE_NAME = "PHPSESSID"

# Zend_Auth_Storage_Session('libretime') in Application_Model_Auth.
SESSION_NAMESPACE = "libretime"
IDENTITY_KEY = "storage"

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class PhpSessionAuthentication(BaseAuthentication):
    """
    Authenticate a request carrying the legacy session cookie.

    Returns ``None``, meaning "not my credentials", whenever the cookie is
    absent, the session is unknown or expired, or nobody is logged in to it - so
    that API-key and basic authentication keep working unchanged. Requests that
    do carry a live session but try to change something are refused rather than
    passed on half-authenticated.
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
            raise AuthenticationFailed(
                "The legacy session authenticates read requests only. Write "
                "requests need an authenticated session of this API, so that "
                "cross-site request forgery can be checked."
            )

        return user, record

    # No authenticate_header() on purpose. Rest framework turns a 401 into a 403
    # when the first authenticator class advertises no scheme, and this API has
    # always answered an anonymous request with 403. Offering a scheme here would
    # quietly change that for every endpoint, which is not what a read bridge is
    # for. See libretime_api/tests/test_permissions.py.

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


def _mask(session_id: str) -> str:
    """A session id is a credential: never log all of it."""
    return f"{session_id[:4]}...{len(session_id)}"
