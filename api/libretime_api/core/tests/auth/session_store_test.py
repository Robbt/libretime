"""
Writing a session the legacy application will read.

These tests read what the store writes with the same code that reads a session
php wrote, so a mismatch in either direction fails here: if the shape drifts, the
legacy application stops seeing a logged in user, and the first sign would be a
station whose screens bounce to a login page.
"""

import time
from datetime import UTC, datetime

import pytest

# The db fixtures are asked for their effect, not their value
# pylint: disable=unused-argument

from ....core.auth import csrf_token, identity
from ....core.auth._phpserialize import dumps, loads
from ....core.auth.session_store import (
    SESSION_ID_ALPHABET,
    SESSION_ID_LENGTH,
    SESSION_LIFETIME,
    create,
    destroy,
    identity as identity_row,
    new_csrf_token,
    new_session_id,
)
from ....core.models import Session, User
from ....core.models.role import Role


@pytest.fixture()
def user(db) -> User:
    return User.objects.create_user(
        role=Role.HOST,
        username="writer",
        password="a-passphrase",
        email="writer@example.org",
        first_name="Wri",
        last_name="Ter",
    )


def test_an_api_session_reads_as_a_session(user):
    session = create(user)

    assert identity(session.data) == "writer"
    assert csrf_token(session.data)


def test_the_row_is_the_shape_the_handler_reads(user):
    session = create(user)

    assert len(session.id) == SESSION_ID_LENGTH
    assert set(session.id) <= set(SESSION_ID_ALPHABET)
    assert session.lifetime == SESSION_LIFETIME
    # Modified plus lifetime is the expiry, which is the test
    # Zend_Session_SaveHandler_DbTable::read() applies, and the one
    # core.tasks.clean_expired_sessions and PhpSessionAuthentication apply.
    assert session.updated_at + session.lifetime > int(time.time())


def test_session_ids_are_not_reused(user):
    ids = {create(user).id for _ in range(20)}

    assert len(ids) == 20
    assert len({new_session_id() for _ in range(100)}) == 100


def test_tokens_are_not_reused():
    assert len({new_csrf_token() for _ in range(20)}) == 20


def test_the_identity_is_the_whole_row_including_the_hash(user):
    """
    ``LoginController`` asks for the row "omit only the password" while the column
    is named ``pass``, so the hash rides along - and the legacy application reads
    that row back, which is the only reason this code has to reproduce it.

    Pinned rather than avoided: dropping the field here would make an api session
    differ from a php session, and the leak is worth fixing in both at once,
    upstream.
    """
    session = create(user)

    stored = loads(session.data)["libretime"]["storage"]

    assert stored["login"] == "writer"
    assert stored["pass"] == user.password
    assert stored["type"] == Role.HOST.value
    assert stored["id"] == user.pk


def test_a_timestamp_is_written_as_the_text_the_driver_hands_over(user):
    """
    php's driver gives Propel a string for a timestamp, so that is what a session
    row holds. Writing a python object here would be an array of numbers to php.
    """
    user.last_login = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    user.save()

    stored = identity_row(user)

    assert stored["lastlogin"].startswith("2026-03-04 0")
    assert isinstance(stored["lastlogin"], str)


def test_logging_out_clears_the_identity_and_keeps_the_row(user):
    session = create(user)
    data = loads(session.data)
    data["some_screens_state"] = {"last_tab": "library"}
    session.data = dumps(data)
    session.save()

    destroy(session)

    assert Session.objects.filter(pk=session.pk).exists()

    after = loads(session.data)

    assert after["libretime"]["storage"] is None
    assert "csrf_namespace" not in after
    # Anything a screen stored for this user is left where it is, as the legacy
    # logout leaves it.
    assert after["some_screens_state"] == {"last_tab": "library"}


def test_logging_out_of_a_session_that_cannot_be_read_is_not_an_error(db):
    session = Session.objects.create(
        id="unreadable" + "0" * 16,
        updated_at=int(time.time()),
        lifetime=60,
        data="nonsense{",
    )

    destroy(session)

    assert loads(session.data) == []
    assert csrf_token(session.data) is None
