"""Ways a caller can prove who it is to this API."""

from .php_session import (
    COOKIE_NAME,
    PhpSessionAuthentication,
    csrf_token,
    identity,
)

__all__ = ["COOKIE_NAME", "PhpSessionAuthentication", "csrf_token", "identity"]
