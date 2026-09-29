"""Refuse state-changing requests that another website sent us.

The app (and the maintainer's Release Console) binds to the loopback
interface and has no login, on the argument that nothing off-machine can
reach it. A browser breaks that argument: any page the user has open can POST
to ``http://localhost:8000``, and a "simple" request (form post, multipart
upload) needs no CORS preflight to *arrive* — CORS only stops the page from
reading the answer. Installing an Add-on or restarting the app must not be one
of those drive-by effects.

The rule (:func:`is_cross_site_write`): a request that is not ``GET``/``HEAD``/
``OPTIONS`` is refused when

* it carries an ``Origin`` that is not this server's own origin (same scheme,
  host and port as the request's ``Host``; the loopback names ``localhost``,
  ``127.0.0.1`` and ``[::1]`` count as one host), or
* the browser marks it ``Sec-Fetch-Site: cross-site``.

A request without ``Origin`` (the CLI, scripts, the test client) is allowed:
browsers always send ``Origin`` on a cross-origin write.

**DNS rebinding** (:func:`is_foreign_host`): a hostile name that resolves to
127.0.0.1 makes its page same-origin with itself, so the rule above cannot
see it. Both servers bind to loopback only, so any request — every method,
reads included — whose ``Host`` is not a loopback name is refused
(``error.bad_host``).

Neutral leaf module, stdlib only: the middleware speaks plain ASGI, so both
the app (``webapp.py``) and the Release Console use it without sharing more.
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable, Iterable, Mapping, Optional
from urllib.parse import urlsplit

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
# Starlette's TestClient sends ``Host: testserver``; it never reaches a socket.
TEST_HOST = "testserver"
ERROR_CODE = "error.cross_origin"
BAD_HOST_CODE = "error.bad_host"

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _split_host(host: str, scheme: str) -> tuple[str, Optional[int]]:
    """``(hostname, port)`` of a ``Host`` header value such as ``localhost:8000``."""
    host = (host or "").strip().lower()
    if host.startswith("["):  # IPv6 literal
        name, _, rest = host.partition("]")
        name += "]"
        port_text = rest[1:] if rest.startswith(":") else ""
    elif host.count(":") == 1:
        name, _, port_text = host.partition(":")
    else:
        name, port_text = host, ""
    try:
        port = int(port_text) if port_text else _DEFAULT_PORTS.get(scheme)
    except ValueError:
        port = None
    return name, port


def is_foreign_host(host: str) -> bool:
    """True when the request's ``Host`` does not name this machine's loopback.

    Both servers bind to 127.0.0.1 only, so every legitimate request says
    ``localhost``, ``127.0.0.1`` or ``[::1]``. Any other name means a browser
    was pointed here through a hostile DNS name (DNS rebinding): such a page is
    "same origin" with itself and could read the library — so every method is
    refused, reads included. A missing ``Host`` is refused too."""
    name, _port = _split_host(host, "http")
    return not name or (name not in LOOPBACK_NAMES and name != TEST_HOST)


def _same_host(a: str, b: str) -> bool:
    return a == b or (a in LOOPBACK_NAMES and b in LOOPBACK_NAMES)


def is_own_origin(origin: str, host: str, scheme: str) -> bool:
    """Whether ``origin`` is the origin the request's ``host`` is served at."""
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return False
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        return False  # "null", file://, anything unparsable
    if parts.scheme != scheme:
        return False
    origin_name = parts.netloc.rpartition("@")[2]
    name, port = _split_host(origin_name, parts.scheme)
    own_name, own_port = _split_host(host, scheme)
    return bool(own_name) and _same_host(name, own_name) and port == own_port


def is_cross_site_write(
    method: str,
    path: str,
    headers: Mapping[str, str],
    scheme: str = "http",
    prefix: str = "/api/",
) -> bool:
    """True when this request must be refused (see the module docstring).

    ``headers`` is keyed in lower case; ``prefix`` limits the check to a part
    of the app (``"/"`` for all of it)."""
    if method.upper() in SAFE_METHODS or not path.startswith(prefix):
        return False
    if (headers.get("sec-fetch-site") or "").strip().lower() == "cross-site":
        return True
    origin = headers.get("origin")
    if origin is None:
        return False
    return not is_own_origin(origin, headers.get("host", ""), scheme)


def _headers_of(raw: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in raw:
        out.setdefault(key.decode("latin-1").lower(), value.decode("latin-1"))
    return out


class OriginGuardMiddleware:
    """Pure ASGI middleware applying :func:`is_cross_site_write`.

    Pure ASGI rather than ``BaseHTTPMiddleware`` so streaming answers (the
    install SSE) pass through untouched. A refusal is ``403`` with the error
    code ``error.cross_origin`` (ADR-0018: the backend sends codes)."""

    def __init__(self, app: Callable[..., Awaitable[None]], prefix: str = "/api/") -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") in ("http", "websocket"):
            headers = _headers_of(scope.get("headers") or [])
            code = None
            if is_foreign_host(headers.get("host", "")):
                code = BAD_HOST_CODE
            elif scope["type"] == "http" and is_cross_site_write(
                scope.get("method", "GET"), scope.get("path", ""), headers,
                scope.get("scheme", "http"), self.prefix,
            ):
                code = ERROR_CODE
            if code is not None:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = json.dumps({"detail": {"code": code}}).encode("utf-8")
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode("ascii"))]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


__all__ = ["BAD_HOST_CODE", "ERROR_CODE", "OriginGuardMiddleware", "is_cross_site_write", "is_foreign_host",
           "is_own_origin"]
