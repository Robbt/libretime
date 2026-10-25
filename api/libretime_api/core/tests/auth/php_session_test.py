"""
Authentication from a legacy session cookie.

The payloads here are real: captured from the ``sessions`` table of a running
installation after logging in through the legacy UI, so they describe what the
bridge has to read rather than what it would like to read.
"""

import time

import pytest
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient, APIRequestFactory

from ....core.auth import (
    COOKIE_NAME,
    PhpSessionAuthentication,
    csrf_token,
    identity,
)
from ....core.auth._phpserialize import PhpDecodeError, loads
from ....core.models import Session, User
from ....core.models.role import Role

# pytest asks for the db and user fixtures for their side effects, not their values
# pylint: disable=unused-argument


# Captured after a successful login of the user "api_spike". The password hash it
# carries is a finding in itself: the legacy session leaks it to anyone who can
# read a session row.
LOGGED_IN = (
    'a:3:{s:9:"libretime";a:1:{s:7:"storage";O:8:"stdClass":13:{s:2:"id";i:2;'
    's:5:"login";s:9:"api_spike";s:4:"pass";s:32:"90f6215e2776fac037f0ce19fdbef1ed";'
    's:4:"type";s:1:"A";s:10:"first_name";s:3:"API";s:9:"last_name";s:5:"Spike";'
    's:9:"lastlogin";N;s:8:"lastfail";N;s:13:"skype_contact";N;s:14:"jabber_contact";N;'
    's:5:"email";s:17:"spike@example.org";s:10:"cell_phone";N;s:14:"login_attempts";i:0;}}'
    's:14:"csrf_namespace";a:1:{s:9:"authtoken";s:40:"c95a9722fe0096c1ff1a0a0277b9f555a0a824ac";}'
    's:4:"__ZF";a:1:{s:14:"csrf_namespace";a:1:{s:3:"ENT";i:1793472058;}}}'
)

# Captured from a browser that never logged in.
ANONYMOUS = (
    'a:3:{s:9:"libretime";a:1:{s:7:"storage";N;}'
    's:14:"csrf_namespace";a:1:{s:9:"authtoken";s:40:"7d90450bfeda751bc40a42900abc1cf1146fc794";}'
    's:4:"__ZF";a:1:{s:14:"csrf_namespace";a:1:{s:3:"ENT";i:1793322342;}}}'
)

# The token the same session expects back on any request that changes something.
CSRF_TOKEN = "c95a9722fe0096c1ff1a0a0277b9f555a0a824ac"
ANONYMOUS_CSRF_TOKEN = "7d90450bfeda751bc40a42900abc1cf1146fc794"

LIFETIME = 1440

# A session written before the csrf namespace existed, to exercise the branch
# where a write cannot be checked at all.
WITHOUT_CSRF = (
    'a:1:{s:9:"libretime";a:1:{s:7:"storage";O:8:"stdClass":2:{s:2:"id";i:2;'
    's:5:"login";s:9:"api_spike";}}}'
)


# ---------------------------------------------------------------------------
# the php reader
# ---------------------------------------------------------------------------


def test_loads_a_logged_in_session():
    decoded = loads(LOGGED_IN)

    assert isinstance(decoded, dict)
    assert decoded["libretime"]["storage"]["login"] == "api_spike"
    assert decoded["libretime"]["storage"]["id"] == 2
    assert decoded["libretime"]["storage"]["skype_contact"] is None
    assert decoded["libretime"]["storage"]["login_attempts"] == 0


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("i:42;", 42),
        ("d:1.5;", 1.5),
        ("b:1;", True),
        ("b:0;", False),
        ("N;", None),
        ('s:5:"hello";', "hello"),
        ('s:6:"héllo";', "héllo"),
        ('s:0:"";', ""),
        ('a:2:{s:1:"a";i:1;s:1:"b";i:2;}', {"a": 1, "b": 2}),
        ('a:2:{i:0;s:1:"x";i:1;s:1:"y";}', ["x", "y"]),
        ('a:2:{i:3;s:1:"x";i:1;s:1:"y";}', {3: "x", 1: "y"}),
        ('O:8:"stdClass":1:{s:1:"a";i:1;}', {"a": 1}),
        ("a:0:{}", []),
    ],
)
def test_reader_handles_the_supported_types(payload, expected):
    assert loads(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        # Custom serialization and classes we cannot interpret.
        'C:11:"SomeClass":21:{some:serialized:stuff}',
        'O:17:"Zend_Session_Namespace":1:{s:1:"a";i:1;}',
        # A length that does not match the bytes that follow it.
        's:5:"ab";',
        # A document with junk after the value.
        "i:1;junk",
        # A boolean that is not 0 or 1.
        "b:9;",
        # Nothing at all.
        "",
    ],
)
def test_reader_refuses_what_it_cannot_read(payload):
    if payload.startswith("i:1"):
        payload = "i:1;x"

    with pytest.raises(PhpDecodeError):
        loads(payload)


def test_reader_counts_bytes_not_characters():
    # "é" is two bytes; the length prefix is in bytes.
    assert loads('s:4:"éé";') == "éé"


# ---------------------------------------------------------------------------
# reading the identity
# ---------------------------------------------------------------------------


def test_identity_of_a_logged_in_session():
    assert identity(LOGGED_IN) == "api_spike"


def test_identity_of_an_anonymous_session_is_none():
    assert identity(ANONYMOUS) is None


def test_identity_of_a_session_being_garbage_is_none(caplog):
    with caplog.at_level("WARNING"):
        assert identity("nonsense{") is None

    assert "could not read" in caplog.text


def test_identity_stored_as_a_plain_login_is_understood():
    assert identity('a:1:{s:9:"libretime";a:1:{s:7:"storage";s:5:"admin";}}') == "admin"


# ---------------------------------------------------------------------------
# the authenticator
# ---------------------------------------------------------------------------


@pytest.fixture()
def user(db) -> User:
    return User.objects.create_user(
        role=Role.ADMIN,
        username="api_spike",
        password="whatever",
        email="spike@example.org",
        first_name="API",
        last_name="Spike",
    )


def make_session(
    *, session_id: str, live: bool = True, data: str = LOGGED_IN
) -> Session:
    return Session.objects.create(
        id=session_id,
        updated_at=int(time.time()) - (10 if live else LIFETIME * 3),
        lifetime=LIFETIME,
        data=data,
    )


def test_authenticates_a_live_session(user):
    make_session(session_id="sid0000000000000000000000000001")

    result = PhpSessionAuthentication().authenticate(
        _drf_request("get", "sid0000000000000000000000000001")
    )

    assert result is not None
    assert result[0] == user


def test_anonymous_session_authenticates_nobody(user):
    make_session(session_id="sid0000000000000000000000000002", data=ANONYMOUS)

    assert (
        PhpSessionAuthentication().authenticate(
            _drf_request("get", "sid0000000000000000000000000002")
        )
        is None
    )


def test_expired_session_authenticates_nobody(user):
    make_session(session_id="sid0000000000000000000000000003", live=False)

    assert (
        PhpSessionAuthentication().authenticate(
            _drf_request("get", "sid0000000000000000000000000003")
        )
        is None
    )


def test_unknown_session_authenticates_nobody(user):
    assert (
        PhpSessionAuthentication().authenticate(
            _drf_request("get", "sid0000000000000000000000000004")
        )
        is None
    )


def test_missing_cookie_authenticates_nobody(user):
    assert PhpSessionAuthentication().authenticate(_drf_request("get", None)) is None


def test_session_of_a_deleted_user_authenticates_nobody(db, caplog):
    make_session(session_id="sid0000000000000000000000000005")

    with caplog.at_level("WARNING"):
        assert (
            PhpSessionAuthentication().authenticate(
                _drf_request("get", "sid0000000000000000000000000005")
            )
            is None
        )

    assert "no matching user" in caplog.text
    assert "sid00000" not in caplog.text  # the id is a credential


def test_a_read_needs_no_token(user):
    make_session(session_id="sid0000000000000000000000000006")

    assert PhpSessionAuthentication().authenticate(
        _drf_request("get", "sid0000000000000000000000000006")
    )


def test_a_write_without_the_token_is_refused(user):
    """A cookie proves which user, not that this page meant to write."""
    make_session(session_id="sid0000000000000000000000000006")

    with pytest.raises(AuthenticationFailed) as error:
        PhpSessionAuthentication().authenticate(
            _drf_request("post", "sid0000000000000000000000000006")
        )

    assert "forgery check failed" in str(error.value)


def test_a_write_with_the_header_token_is_authenticated(user):
    make_session(session_id="sid0000000000000000000000000006")

    result = PhpSessionAuthentication().authenticate(
        _drf_request(
            "post",
            "sid0000000000000000000000000006",
            {"key": "general.front_end_timeout", "value": "1"},
            format="json",
            HTTP_X_CSRFTOKEN=CSRF_TOKEN,
        )
    )

    assert result is not None
    assert result[0] == user


def test_a_write_with_the_form_field_token_is_authenticated(user):
    """The legacy convention stays working for the bodies that can carry it."""
    make_session(session_id="sid0000000000000000000000000006")

    result = PhpSessionAuthentication().authenticate(
        _drf_request(
            "post",
            "sid0000000000000000000000000006",
            {"csrf_token": CSRF_TOKEN, "key": "general.front_end_timeout"},
        )
    )

    assert result is not None
    assert result[0] == user


def test_a_write_carrying_another_sessions_token_is_refused(user):
    make_session(session_id="sid0000000000000000000000000006")
    make_session(
        session_id="sid0000000000000000000000000007",
        data=LOGGED_IN.replace(CSRF_TOKEN, "0" * 40),
    )

    with pytest.raises(AuthenticationFailed):
        PhpSessionAuthentication().authenticate(
            _drf_request(
                "post",
                "sid0000000000000000000000000006",
                format="json",
                HTTP_X_CSRFTOKEN="0" * 40,
            )
        )


def test_a_write_with_an_empty_header_token_is_refused(user):
    make_session(session_id="sid0000000000000000000000000006")

    with pytest.raises(AuthenticationFailed):
        PhpSessionAuthentication().authenticate(
            _drf_request(
                "post",
                "sid0000000000000000000000000006",
                format="json",
                HTTP_X_CSRFTOKEN="",
            )
        )


def test_a_session_without_a_token_cannot_write(user, caplog):
    """Better a refusal that names the reason than a check that cannot happen."""
    make_session(session_id="sid0000000000000000000000000008", data=WITHOUT_CSRF)

    with pytest.raises(AuthenticationFailed) as error:
        PhpSessionAuthentication().authenticate(
            _drf_request(
                "post",
                "sid0000000000000000000000000008",
                format="json",
                HTTP_X_CSRFTOKEN=CSRF_TOKEN,
            )
        )

    assert "has no CSRF token" in str(error.value)


def test_a_request_without_a_session_may_still_write_with_other_credentials():
    """Refusal must not leak onto API-key callers, which send no cookie."""
    result = PhpSessionAuthentication().authenticate(_drf_request("post", None))

    assert result is None


def test_a_request_that_names_itself_is_not_read_as_a_session(user):
    """
    Both credentials in one request: the cookie authenticator steps aside.

    The header is the caller saying who it is; the cookie is a browser doing what
    browsers do to every request on the domain. A request that presents its own
    credentials is authenticated by those, and by nothing else - including when
    they are wrong, which is the case this also covers. A key that does not work
    should be answered with a rejection, not quietly rescued by whoever happens to
    be logged in to the same browser.
    """
    make_session(session_id="sid0000000000000000000000000013")

    assert (
        PhpSessionAuthentication().authenticate(
            _drf_request(
                "get",
                "sid0000000000000000000000000013",
                HTTP_AUTHORIZATION="Api-Key not-the-stations-key",
            )
        )
        is None
    )


@pytest.mark.django_db()
def test_a_cookie_and_a_key_together_are_answered_as_the_key(user):
    """
    The observable difference between the two identities is the CSRF token: a
    session has to present one to write, a station key never does. So a write
    carrying both, with no token, succeeds only if the key was the one believed.

    The session here belongs to an admin, who could write this with a token -
    the point is that it succeeds without one.
    """
    make_session(session_id="sid0000000000000000000000000014")

    client = APIClient()
    client.cookies[COOKIE_NAME] = "sid0000000000000000000000000014"
    client.credentials(
        HTTP_AUTHORIZATION=f"Api-Key {settings.CONFIG.general.api_key}",
    )

    response = client.post(
        "/api/v2/preferences",
        {"key": "general.pref_written_by_key", "value": "1"},
        format="json",
    )

    assert response.status_code == 201, response.data


# ---------------------------------------------------------------------------
# the token a session carries
# ---------------------------------------------------------------------------


def test_the_token_lives_in_the_same_payload_as_the_identity():
    assert csrf_token(LOGGED_IN) == CSRF_TOKEN


def test_an_anonymous_session_has_one_too():
    """
    PageLayoutInitPlugin mints it before anybody logs in, so a live session
    always has something a write can be checked against.
    """
    assert csrf_token(ANONYMOUS) == ANONYMOUS_CSRF_TOKEN


@pytest.mark.parametrize("payload", [None, "", WITHOUT_CSRF, "nonsense{"])
def test_a_payload_without_a_token_yields_none(payload):
    assert csrf_token(payload) is None


# ---------------------------------------------------------------------------
# handing the token to the page that needs it
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
def test_a_session_can_read_its_own_token(user):
    make_session(session_id="sid0000000000000000000000000006")

    client = APIClient()
    client.cookies[COOKIE_NAME] = "sid0000000000000000000000000006"

    response = client.get("/api/v2/session/csrf-token")

    assert response.status_code == 200
    assert response.json()["csrf_token"] == CSRF_TOKEN


@pytest.mark.django_db()
def test_an_anonymous_caller_gets_no_token():
    response = APIClient().get("/api/v2/session/csrf-token")

    assert response.status_code == 403


@pytest.mark.django_db()
def test_a_caller_with_no_session_of_its_own_gets_no_token(user):
    """An api key or a password has no csrf token to be shown."""
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.get("/api/v2/session/csrf-token")

    assert response.status_code == 403
    assert "legacy session" in response.json()["detail"]


@pytest.mark.django_db()
def test_a_session_without_a_token_says_so(user):
    make_session(session_id="sid0000000000000000000000000008", data=WITHOUT_CSRF)

    client = APIClient()
    client.cookies[COOKIE_NAME] = "sid0000000000000000000000000008"

    response = client.get("/api/v2/session/csrf-token")

    assert response.status_code == 403
    assert "no CSRF token" in response.json()["detail"]


def _drf_request(method: str, session_id: str | None, data=None, **extra):
    """
    A request as a view would hand it to its authenticators.

    The parsers and the parser context come from the view in real use, so they
    have to be named here: reading a multipart body without them raises rather
    than parses, and the test would be testing its own scaffolding.
    """
    from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
    from rest_framework.request import Request

    factory = APIRequestFactory()
    handler = getattr(factory, method)

    headers = dict(extra)
    if session_id is not None:
        headers["HTTP_COOKIE"] = f"{COOKIE_NAME}={session_id}"

    if data is None:
        wrapped = handler("/api/v2/core/preferences/", **headers)
    else:
        wrapped = handler("/api/v2/core/preferences/", data, **headers)

    return Request(
        wrapped,
        parsers=[JSONParser(), FormParser(), MultiPartParser()],
        parser_context={"request": wrapped},
    )
