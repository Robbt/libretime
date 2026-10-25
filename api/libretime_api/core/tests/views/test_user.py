"""
The people list, at the boundary.

This view is about accounts, so it answers to a person: "admin or own user".
"""

import pytest
from rest_framework.test import APIClient

from libretime_api.core.models import Role, User

PASSWORD = "a-passphrase"


def make(role: Role, username: str) -> User:
    return User.objects.create_user(
        role=role,
        username=username,
        password=PASSWORD,
        email=f"{username}@example.org",
        first_name=username.title(),
        last_name="Person",
    )


@pytest.mark.django_db()
def test_an_admin_may_list_the_people_of_the_station():
    client = APIClient()
    client.force_authenticate(user=make(Role.ADMIN, "boss"))
    make(Role.HOST, "voice")

    response = client.get("/api/v2/users")

    assert response.status_code == 200
    # A fixture creates an admin account for other tests, so this asserts that both
    # of these are listed rather than that nothing else is.
    assert {"boss", "voice"} <= {row["username"] for row in response.json()}


@pytest.mark.django_db()
def test_a_host_may_not():
    client = APIClient()
    client.force_authenticate(user=make(Role.HOST, "voice"))

    assert client.get("/api/v2/users").status_code == 403


@pytest.mark.django_db()
def test_the_station_token_is_refused_by_the_user_list_rather_than_crashing(api_client):
    """
    ``/users`` is about people, so a request with no person behind it is refused.

    It used to answer 500: the permission class called ``is_superuser()`` on
    AnonymousUser, where it is a property rather than the method the station's user
    model defines.
    """
    assert api_client.get("/api/v2/users").status_code == 403
