"""The documentation pages, and the decisions written into them.

These pages have been on `main` for a while, but nothing asserted what they load or who may
load them — so `@latest` assets, a missing ReDoc route, or an accidental permission change
to the public schema would all have passed CI quietly.
"""

from pathlib import Path

import pytest
import yaml
from django.test import Client

from libretime_api.settings._internal import REDOC_VERSION, SWAGGER_UI_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.django_db


def body(client: Client, path: str) -> str:
    response = client.get(path)

    assert response.status_code == 200, response.status_code

    return response.content.decode()


def test_swagger_ui_loads_a_pinned_bundle(client):
    """
    drf-spectacular's default is `swagger-ui-dist@latest`: a station's API page would then
    change with no commit here and no release, and break the same way.
    """
    html = body(client, "/api/v2/schema/swagger-ui")

    assert f"swagger-ui-dist@{SWAGGER_UI_VERSION}" in html
    assert "swagger-ui-dist@latest" not in html


def test_redoc_route_exists_and_is_pinned(client):
    """
    ReDoc is the readable half — a whole endpoint group on one page, filter parameters
    visible without expanding panels. It was configured but never routed.
    """
    html = body(client, "/api/v2/schema/redoc")

    assert f"redoc@{REDOC_VERSION}" in html
    assert "redoc@latest" not in html


def test_document_is_public(client):
    """
    Deliberate, and cheap to lose: the generated document is derived from serializers and
    filter classes, so it carries no station data, and the application is open source.
    Permissions are still enforced per request — this only means someone can read the map.
    """
    response = client.get("/api/v2/schema")

    assert (
        response.status_code == 200
    ), "the schema view stopped allowing anonymous reads"

    document = yaml.safe_load(response.content)

    assert document["paths"], "the public document has no operations"


def test_public_document_leaks_no_station_secrets(client):
    """
    The one thing that would make an anonymous document unacceptable: a value from this
    station's configuration. The schema is generated from serializers, not from rows or
    settings, and that stays true only if someone remembers it when adding an example —
    `general.api_key` is the credential that bypasses every permission check in the api.
    """
    from django.conf import settings

    content = body(client, "/api/v2/schema")
    secret = settings.CONFIG.general.api_key

    assert secret, "the test fixture has no api key to look for"
    assert secret not in content


def test_contributor_page_agrees_with_the_settings_pin():
    """
    The offline page in tools/api-docs repeats the version by hand, because a static file
    cannot import python. This is the half that keeps that honest.
    """
    page = (REPO_ROOT / "tools" / "api-docs" / "index.html").read_text(encoding="utf-8")

    assert f"swagger-ui-dist@{SWAGGER_UI_VERSION}" in page
    assert "@latest" not in page
