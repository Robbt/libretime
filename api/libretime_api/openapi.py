"""Parts of the OpenAPI document that cannot be inferred from the code.

drf-spectacular describes what it can see: serializers, viewsets, filter backends and
the authentication classes in ``REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]``. Two
things LibreTime needs in the document are not visible to it, and both are the kind of
thing that costs a client author an hour:

**The station api key.** ``IsSystemTokenOrUser`` accepts ``Authorization: Api-Key <key>``
and compares it against ``general.api_key`` — that is how liquidsoap and the other
machine callers reach the api. It is a *permission* shortcut, not an authentication
class, so there is nothing for the generator to find. Without it declared, the shipped
documentation describes an api that no service in the stack actually uses.

**The csrf token.** A request that arrives on a session cookie has to carry the
station's csrf token, exactly as the legacy interface requires, and the api answers 403
when it does not. OpenAPI 3.0 has no way to say "cookie authentication, plus a required
companion header": ``securitySchemes`` describes credentials, and a scheme cannot depend
on another scheme. So it is documented where a caller will meet it — as a header
parameter on the operations it applies to.

Both are added in one postprocessing hook rather than by decorating every view, because
they are properties of the station's authentication, not of an endpoint: a new viewset
gets them without anybody remembering, which is the whole point of a generated document.
"""

from typing import Any

# Header names as clients send them. OpenAPI parameter names are case-insensitive on the
# wire but the document should spell them the way the browser does.
CSRF_HEADER = "X-CSRFToken"

API_KEY_SCHEME_NAME = "stationApiKey"

API_KEY_SECURITY_SCHEME: dict[str, Any] = {
    "type": "apiKey",
    "in": "header",
    "name": "Authorization",
    "description": (
        "Station-wide service credential: send the value of `general.api_key` from the "
        "station configuration as `Authorization: Api-Key <key>`. This is what the other "
        "LibreTime services use; a browser logged in to the station uses the session "
        "cookie instead. Requests carrying it are not subject to the csrf requirement."
    ),
}

CSRF_PARAMETER: dict[str, Any] = {
    "name": CSRF_HEADER,
    "in": "header",
    # Not `required: true`, and that is deliberate rather than lazy. The token is only
    # demanded of requests that authenticated with a cookie; the same operation is
    # perfectly legal with the station api key or basic credentials, and OpenAPI has no
    # way to express a requirement that depends on which security scheme was chosen. A
    # caller who reads `required` as the whole story will get a 403 and the description
    # below, which is a better outcome than a schema that claims the header is always
    # mandatory and is then wrong for half the traffic.
    "required": False,
    "description": (
        "Required for cookie-authenticated requests, including every request a browser "
        "makes on behalf of a logged-in user; ignored otherwise. Fetch the value for the "
        "current session with `GET /api/v2/session/csrf-token` and send it back "
        "verbatim. A form or multipart body may carry the same value in a `csrf_token` "
        "field instead. This is the token the legacy interface uses for the same purpose "
        "— one session, one token, both applications."
    ),
    "schema": {"type": "string"},
}

# The methods a session-authenticated caller has to produce a token for. Mirrors Django's
# own SAFE_METHODS, which is the list the enforcement path uses.
UNSAFE_METHODS = ("post", "put", "patch", "delete")
SAFE_METHODS = ("get", "head", "options", "trace")


def libretime_authentication_extensions(
    result: dict[str, Any], **_generated: Any
) -> dict[str, Any]:
    """
    Postprocessing hook, registered in ``SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"]``.

    Called by drf-spectacular as ``hook(result=..., generator=..., request=..., public=...)``,
    so the extra keyword arguments are accepted and ignored: the hook is a function of the
    finished document, not of the request that prompted it.

    Idempotent, because spectacular may call the generator more than once in a process
    (the test client and the management command both do), and a hook that appends twice
    produces a document with duplicate parameters — which is exactly the sort of
    technically-invalid detail that a validator catches and a reviewer shouldn't have to.
    """
    _add_security_scheme(result)
    _annotate_unsafe_operations(result)
    return result


def _add_security_scheme(result: dict[str, Any]) -> None:
    components = result.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})

    if API_KEY_SCHEME_NAME in schemes:
        return

    schemes[API_KEY_SCHEME_NAME] = API_KEY_SECURITY_SCHEME

    # Every operation that lists the cookie and basic credentials should also say the
    # station key is an option, since the permission class accepts it everywhere.
    for path in result.get("paths", {}).values():
        for operation in path.values():
            if not isinstance(operation, dict):
                continue

            security = operation.get("security")

            if not security:
                continue

            if not any(API_KEY_SCHEME_NAME in scheme for scheme in security):
                security.append({API_KEY_SCHEME_NAME: []})


def _annotate_unsafe_operations(result: dict[str, Any]) -> None:
    for path in result.get("paths", {}).values():
        for method, operation in path.items():
            if method not in UNSAFE_METHODS or not isinstance(operation, dict):
                continue

            parameters = operation.setdefault("parameters", [])

            if any(
                parameter.get("name") == CSRF_HEADER and parameter.get("in") == "header"
                for parameter in parameters
                if isinstance(parameter, dict)
            ):
                continue

            parameters.append(dict(CSRF_PARAMETER))
