"""Validate an optional task-scoped proxy without exposing credentials."""

from urllib.parse import urlparse


def validate_proxy(value):
    if value is None:
        return None
    message = "Proxy must be an HTTP(S) or SOCKS5 URL with host and port, without credentials or path"
    try:
        parsed = urlparse(value)
        if (parsed.scheme not in {"http", "https", "socks5", "socks5h"}
                or not parsed.hostname or not parsed.port
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or any(character.isspace() for character in value)):
            raise ValueError(message)
    except ValueError:
        raise ValueError(message) from None
    return value.rstrip("/")
