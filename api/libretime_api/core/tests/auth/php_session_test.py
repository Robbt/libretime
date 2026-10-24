"""
Authentication from a legacy session cookie.

The payloads here are real: captured from the ``sessions`` table of a running
installation after logging in through the legacy UI, so they describe what the
bridge has to read rather than what it would like to read.
"""

import time

# pytest asks for the db and user fixtures for their side effects, not their values
# pylint: disable=unused-argument

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from ....core.auth import COOKIE_NAME, PhpSessionAuthentication, identity
from ....core.auth._phpserialize import PhpDecodeError, loads
from ....core.models import Session, User
from ....core.models.role import Role

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

LIFETIME = 1440


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


def test_a_live_session_may_not_write(user):
    """The bridge is read-only until cross-site forgery can be checked."""
    make_session(session_id="sid0000000000000000000000000006")

    with pytest.raises(AuthenticationFailed) as error:
        PhpSessionAuthentication().authenticate(
            _drf_request("post", "sid0000000000000000000000000006")
        )

    assert "read requests only" in str(error.value)


def test_a_request_without_a_session_may_still_write_with_other_credentials():
    """Refusal must not leak onto API-key callers, which send no cookie."""
    result = PhpSessionAuthentication().authenticate(_drf_request("post", None))

    assert result is None


def _drf_request(method: str, session_id: str | None):
    from rest_framework.request import Request

    factory = APIRequestFactory()
    handler = getattr(factory, method)

    headers = {}
    if session_id is not None:
        headers["HTTP_COOKIE"] = f"{COOKIE_NAME}={session_id}"

    return Request(handler("/api/v2/library/files/", **headers))
