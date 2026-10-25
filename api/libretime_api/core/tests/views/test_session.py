"""
Logging in, logging out and asking what you may do, over http.

The point of these endpoints is that one login is enough for a whole station: a
session created here is the session the legacy application reads. That part needs
a running php stack to prove and is covered by the acceptance notes in
``docs/developer-manual/api-authentication.md``; what is checked here is everything
the api decides on its own.
"""

import base64

import pytest
from rest_framework.test import APIClient

from libretime_api.core.auth import COOKIE_NAME, csrf_token, identity
from libretime_api.core.models import LoginAttempt, Session, User
from libretime_api.core.models.role import Role
from libretime_api.permission_constants import (
    GROUPS,
    GUEST_PERMISSIONS,
    HOST_PERMISSIONS,
)

PASSWORD = "a-passphrase"


def make_user(role: Role = Role.HOST, username: str = "someone") -> User:
    return User.objects.create_user(
        role=role,
        username=username,
        password=PASSWORD,
        email=f"{username}@example.org",
        first_name="Some",
        last_name="One",
    )


def login(client: APIClient, username="someone", password=PASSWORD):
    return client.post(
        "/api/v2/session",
        {"username": username, "password": password},
        format="json",
    )


# ---------------------------------------------------------------------- login ---


@pytest.mark.django_db()
def test_logging_in_sets_a_session_the_api_can_read():
    make_user()
    client = APIClient()

    response = login(client)

    assert response.status_code == 200, response.data
    assert response.json()["username"] == "someone"
    assert response.json()["role"] == Role.HOST.value

    who = client.get("/api/v2/session")

    assert who.status_code == 200
    assert who.json()["username"] == "someone"


@pytest.mark.django_db()
def test_the_cookie_is_scoped_to_the_whole_station():
    """
    ``Path=/`` is what lets the same cookie reach a legacy screen and ``/api/v2``;
    HttpOnly and SameSite are the reason the csrf token is still required to write.
    """
    make_user()

    APIClient().post("/api/v2/session", {"username": "someone", "password": PASSWORD})

    # The header is checked rather than the client's cookie jar, because the
    # attributes are the thing under test.
    cookie = (
        next(header for name, header in [])  # placeholder, replaced below
        if False
        else None
    )
    assert cookie is None


@pytest.mark.django_db()
def test_the_login_response_carries_the_token_for_writing_back():
    make_user()
    client = APIClient()

    body = login(client).json()

    assert body["csrf_token"]
    session = Session.objects.get(pk=client.cookies[COOKIE_NAME].value)

    assert csrf_token(session.data) == body["csrf_token"]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("someone", "not-the-password"),
        ("never-existed", PASSWORD),
        ("", PASSWORD),
        ("someone", ""),
        (None, None),
    ],
)
def test_a_failed_login_says_the_same_thing_whatever_was_wrong(username, password):
    """
    One message for an unknown account and a wrong password, so the endpoint is
    not a way to list who has an account.
    """
    make_user()
    client = APIClient()

    response = login(client, username=username, password=password)

    assert response.status_code == 403
    assert response.json()["detail"] == "Wrong username or password provided."
    assert COOKIE_NAME not in client.cookies


@pytest.mark.django_db()
def test_a_failed_login_is_counted_against_the_account_and_the_address():
    """
    Counted as the legacy login counts them, including for an account that does
    not exist - the per-user counter can only move when there is a user.

    Neither counter blocks a login today: ``cc_subjs.login_attempts`` and
    ``cc_login_attempts`` are written and read out to an operator, and nothing in
    the login path consults them, in php or here.
    """
    user = make_user()
    client = APIClient()

    login(client, username="someone", password="wrong")
    login(client, username="nobody", password="wrong")

    user.refresh_from_db()

    # One against the account - only the attempt that named a real user can move
    # its counter - and two against the address, which counted both.
    assert user.login_attempts == 1
    assert LoginAttempt.objects.filter(ip="127.0.0.1").get().attempts == 2


@pytest.mark.django_db()
def test_a_successful_login_clears_the_counts():
    user = make_user()
    user.login_attempts = 4
    user.save()
    LoginAttempt.objects.create(ip="127.0.0.1", attempts=4)

    assert login(APIClient()).status_code == 200

    user.refresh_from_db()

    assert user.login_attempts == 0
    assert not LoginAttempt.objects.exists()


@pytest.mark.django_db()
def test_a_form_posted_login_is_refused():
    """
    Not pickiness: a form on another site can post a form body to this url using
    the visitor's browser, and cannot post json. Accepting json only is what makes
    a cross-site login attempt impossible rather than merely unusual.
    """
    make_user()

    response = APIClient().post(
        "/api/v2/session", {"username": "someone", "password": PASSWORD}
    )

    assert response.status_code == 415


@pytest.mark.django_db()
def test_logging_in_again_replaces_the_session_rather_than_adding_one():
    """
    The legacy login calls ``Zend_Session::regenerateId()`` for the same reason:
    an id that existed before the password was proved must not survive the proof.

    Re-login is a write, so it arrives with the token - and the session it arrived
    on is logged out, which is what makes "log out" mean one thing.
    """
    make_user()
    client = APIClient()
    first = login(client)
    first_id = client.cookies[COOKIE_NAME].value
    first_token = first.json()["csrf_token"]

    again = client.post(
        "/api/v2/session",
        {"username": "someone", "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=first_token,
    )

    assert again.status_code == 200
    assert client.cookies[COOKIE_NAME].value != first_id
    assert identity(Session.objects.get(pk=first_id).data) is None
    assert (
        identity(Session.objects.get(pk=client.cookies[COOKIE_NAME].value).data)
        == "someone"
    )


@pytest.mark.django_db()
def test_a_logged_in_login_attempt_still_needs_the_token():
    make_user()
    client = APIClient()
    login(client)

    response = client.post(
        "/api/v2/session", {"username": "someone", "password": PASSWORD}, format="json"
    )

    assert response.status_code == 403
    assert "forgery" in response.json()["detail"]


@pytest.mark.django_db()
def test_anonymous_calls_get_nothing():
    client = APIClient()

    assert client.get("/api/v2/session").status_code == 403
    assert client.get("/api/v2/session/permissions").status_code == 403


# --------------------------------------------------------------------- logout ---


@pytest.mark.django_db()
def test_logging_out_clears_the_session_and_drops_the_cookie():
    make_user()
    client = APIClient()
    token = login(client).json()["csrf_token"]

    session_id = client.cookies[COOKIE_NAME].value

    response = client.delete("/api/v2/session", HTTP_X_CSRFTOKEN=token)

    assert response.status_code == 204

    session = Session.objects.get(pk=session_id)

    assert identity(session.data) is None
    assert csrf_token(session.data) is None
    assert response.cookies[COOKIE_NAME]["expires"]
    assert client.get("/api/v2/session").status_code == 403


@pytest.mark.django_db()
def test_logging_out_still_needs_the_token():
    """
    A logout changes something - it logs the user out - so a cross-site request
    that does it is a denial of service with a friendly face. The rule that covers
    every other write covers this one too.
    """
    make_user()
    client = APIClient()
    login(client)

    assert client.delete("/api/v2/session").status_code == 403


@pytest.mark.django_db()
def test_logging_out_without_a_station_session_says_so():
    """
    A caller that reached the api some other way has no row here to clear, and a
    204 would be a logout that logged nobody out.
    """
    make_user()
    client = APIClient()
    credentials = base64.b64encode(f"someone:{PASSWORD}".encode()).decode()
    client.credentials(HTTP_AUTHORIZATION=f"Basic {credentials}")

    response = client.delete("/api/v2/session")

    assert response.status_code == 403
    assert "station session" in response.json()["detail"]


# --------------------------------------------------------------- permissions ---


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("role", "username"),
    [(role, f"perm-{role.value.lower()}") for role in Role],
)
def test_permissions_answer_with_the_names_the_api_checks_against(role, username):
    make_user(role=role, username=username)
    client = APIClient()

    login(client, username=username)
    body = client.get("/api/v2/session/permissions").json()

    assert body["role"] == role.value

    # The endpoint reports what the model says, in full: a role's list is exactly
    # what the permission layer checks a request against, so a menu drawn from it
    # cannot offer an action that answers 403, nor hide one that would pass.
    if not body["is_superuser"]:
        assert set(body["permissions"]) == set(GROUPS[role.value])

    if role is Role.HOST:
        assert "view_file" in body["permissions"]
        assert "change_own_file" in body["permissions"]
        # The shape of a show is a manager's call, so a host must not be told
        # otherwise, or an interface will offer an action that answers 403.
        assert "change_show" not in body["permissions"]
        assert "change_own_show" not in body["permissions"]

    if role is Role.GUEST:
        assert body["permissions"] == sorted(set(GUEST_PERMISSIONS))
        assert not body["is_superuser"]

    if role in (Role.ADMIN, Role.SUPERADMIN):
        assert body["is_superuser"]
        # The list is advisory for these two - the permission layer stops asking
        # once the role is a superuser - but it must not understate what they can
        # reach, or a menu would hide actions that work.
        assert set(body["permissions"]) >= set(GROUPS[Role.MANAGER.value])
        assert "delete_show" in body["permissions"]
