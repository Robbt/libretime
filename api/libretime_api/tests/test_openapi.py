"""The shipped OpenAPI document has to describe how LibreTime is actually reached.

These assertions are about the generated file, not about the code that generates it,
because the thing a client author reads is the document. Generation goes through the same
view a browser or a code generator would use, so a hook that is registered but silently
ineffective — or a settings change that drops it — fails here rather than in someone's
integration test.
"""

import copy
from functools import cache
from typing import Any

import pytest
import yaml
from django.test import Client

from libretime_api.openapi import (
    API_KEY_SCHEME_NAME,
    CSRF_HEADER,
    libretime_authentication_extensions,
)

METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
UNSAFE = ("post", "put", "patch", "delete")

pytestmark = pytest.mark.django_db


@cache
def api_document() -> dict[str, Any]:
    """
    The real ``/api/v2/schema`` response, generated once per test session.

    A cached function rather than a module-scoped fixture on purpose: the fixture idiom
    makes every test re-bind the fixture's name as an argument, which pylint reads as
    shadowing and there is no way to satisfy both tools from a fixture, while a call
    reads exactly like what it is.
    """
    response = Client().get("/api/v2/schema")

    assert response.status_code == 200, response.content[:400]

    parsed = yaml.safe_load(response.content)

    # A document with no operations would satisfy every assertion below, so fail here if
    # generation ever produces an empty shell.
    assert len(parsed["paths"]) > 20, f"only {len(parsed['paths'])} paths generated"

    return parsed


def operations(document: dict[str, Any]):
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method in METHODS and isinstance(operation, dict):
                yield path, method, operation


def parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in operation.get("parameters", []) if isinstance(p, dict)]


def has_csrf_header(operation: dict[str, Any]) -> bool:
    return any(
        p.get("name") == CSRF_HEADER and p.get("in") == "header"
        for p in parameters(operation)
    )


def test_api_key_is_a_declared_security_scheme():
    """
    The station key is checked by a permission class, not an authentication class, so
    nothing in the code tells the generator it exists. If this fails, the documentation
    has quietly gone back to describing an api no LibreTime service uses.
    """
    scheme = api_document()["components"]["securitySchemes"][API_KEY_SCHEME_NAME]

    assert scheme["type"] == "apiKey"
    assert scheme["in"] == "header"
    assert scheme["name"].lower() == "authorization"
    assert "Api-Key" in scheme["description"]


def test_api_key_is_offered_on_every_operation():
    all_operations = list(operations(api_document()))
    offered = [
        f"{method.upper()} {path}"
        for path, method, operation in all_operations
        if any(
            API_KEY_SCHEME_NAME in scheme for scheme in operation.get("security", [])
        )
    ]

    assert offered, "no operation advertises the station api key"
    # Not merely "some": a client author picks one endpoint to try, and a partial list
    # means the documentation answered differently than the code will.
    assert len(offered) == len(
        all_operations
    ), f"{len(offered)} of {len(all_operations)} operations offer the key"


def test_session_cookie_is_named_as_the_legacy_application_sets_it():
    """
    ``sessionid`` alone would be a lie: the cookie a signed-in browser carries is the one
    the legacy application created.
    """
    scheme = api_document()["components"]["securitySchemes"]["phpSessionCookie"]

    assert scheme["in"] == "cookie"
    assert scheme["name"] == "PHPSESSID"


def test_write_operations_document_the_csrf_header():
    writes = [
        (path, method, operation)
        for path, method, operation in operations(api_document())
        if method in UNSAFE
    ]

    assert writes, "the api has no write operations any more?"

    missing = [
        f"{method.upper()} {path}"
        for path, method, operation in writes
        if not has_csrf_header(operation)
    ]

    assert not missing, f"csrf header not documented on: {', '.join(missing[:8])}"


def test_read_operations_do_not_carry_the_csrf_header():
    wrong = [
        f"{method.upper()} {path}"
        for path, method, operation in operations(api_document())
        if method not in UNSAFE and has_csrf_header(operation)
    ]

    assert not wrong, f"csrf header advertised on read-only operations: {wrong[:8]}"


def test_csrf_rule_is_stated_where_a_caller_will_read_it():
    """
    ``required`` is false deliberately — mandatory for cookie callers, irrelevant to key
    and basic ones, and OpenAPI cannot express a condition on which scheme was chosen —
    so the only place the rule exists at all is the description.
    """
    operation = api_document()["paths"]["/api/v2/shows"]["post"]
    header = next(p for p in parameters(operation) if p.get("name") == CSRF_HEADER)

    assert header["required"] is False
    assert "csrf-token" in header["description"]
    assert "cookie" in header["description"].lower()


def test_hook_is_idempotent():
    """
    The generator can run more than once in a process, and a hook that appends twice
    yields a document with duplicated parameters — invalid to a strict reader, invisible
    to a lenient one, and not something anyone notices until a code generator errors.
    """
    sample = {
        "components": {"securitySchemes": {"basicAuth": {"type": "http"}}},
        "paths": {
            "/api/v2/shows": {
                "get": {"security": [{"basicAuth": []}]},
                "post": {"security": [{"basicAuth": []}], "parameters": []},
            }
        },
    }

    once = libretime_authentication_extensions(copy.deepcopy(sample))
    twice = libretime_authentication_extensions(copy.deepcopy(once))

    assert twice == once
    assert len(twice["paths"]["/api/v2/shows"]["post"]["parameters"]) == 1
