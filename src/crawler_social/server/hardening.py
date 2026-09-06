"""Server hardening (plans/v2/09).

With no `CRAWLER_SERVE_TOKEN` configured and a loopback bind, this module
adds only response headers, an access log, and request-size limits. A
configured token turns authentication on for every path except `/healthz`.
The middlewares are installed innermost-first, so the request passes
headers <- logging <- limits <- auth and every response -- including 401s,
400s and 500s -- still gets the header set and a log line.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
import time
from urllib.parse import parse_qsl, urlencode

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

#: CSP for app pages. The snapshot raw route sets its own stricter CSP and
#: the headers middleware uses setdefault, so that one is never overwritten.
APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; frame-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'self'"
)

SESSION_COOKIE = "crawler_session"
TOKEN_PARAM = "token"

MIN_TOKEN_CHARS = 32
MAX_QUERY_BYTES = 4096
MAX_Q_CHARS = 500
#: The only bodies this server reads are the two small /crawl forms, so a
#: body larger than this is refused before anything reads it.
MAX_BODY_BYTES = 64 * 1024

_ERROR_TITLES = {
    400: "Bad request",
    401: "Sign-in required",
    413: "Request too large",
}


def wants_json(request) -> bool:
    """JSON error bodies under /api and /static, rendered pages elsewhere."""
    path = request.url.path
    return path.startswith("/api") or path.startswith("/static")


def redact_query(query_string: bytes) -> str:
    """The query string for the access log, with any token value removed."""
    if not query_string:
        return ""
    text = query_string.decode("ascii", "replace")
    pairs = [
        (name, "[REDACTED]" if name == TOKEN_PARAM else value)
        for name, value in parse_qsl(text, keep_blank_values=True)
    ]
    return urlencode(pairs)


def _error_response(request, status_code: int, code: str, detail: str):
    from .pages import render

    if wants_json(request):
        return JSONResponse(status_code=status_code, content={"error": code})
    return render(
        request,
        "error.html",
        {"title": _ERROR_TITLES.get(status_code, "Error"), "message": detail},
        status_code=status_code,
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """The Step 09 header set on every response; routes keep their own."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", APP_CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not wants_json(request):
            response.headers.setdefault("X-Frame-Options", "DENY")
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """method, path, status and duration to stderr; token values redacted.

    Also mints the per-request id that the 500 handler quotes back to the
    user and writes to the server log. Cookie values are never logged.
    """

    async def dispatch(self, request, call_next):
        request.state.request_id = secrets.token_hex(8)
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        target = request.url.path
        query = redact_query(request.scope.get("query_string", b""))
        if query:
            target = f"{target}?{query}"
        sys.stderr.write(
            f"{request.method} {target} {response.status_code} "
            f"{elapsed_ms:.1f}ms id={request.state.request_id}\n"
        )
        return response


class LimitsMiddleware(BaseHTTPMiddleware):
    """Reject oversized query strings, `q` values and request bodies."""

    async def dispatch(self, request, call_next):
        query_string = request.scope.get("query_string", b"")
        if len(query_string) > MAX_QUERY_BYTES:
            return _error_response(
                request, 400, "query_too_long", "Query string exceeds 4 KiB."
            )
        q = request.query_params.get("q")
        if q is not None and len(q) > MAX_Q_CHARS:
            return _error_response(
                request,
                400,
                "q_too_long",
                f"q exceeds {MAX_Q_CHARS} characters.",
            )
        # A declared length is refused up front so the body is never read.
        # An undeclared (chunked) body still can't grow past the cap
        # because nothing downstream reads more than the form.
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                return _error_response(
                    request, 400, "bad_content_length", "Content-Length is not a number."
                )
            if length > MAX_BODY_BYTES:
                return _error_response(
                    request,
                    413,
                    "body_too_large",
                    f"Request body exceeds {MAX_BODY_BYTES // 1024} KiB.",
                )
        return await call_next(request)


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Bearer header or ?token= on the first request, a cookie afterwards.

    The cookie carries an HMAC of the token under a per-start salt, never
    the token itself, so the two secrets are not interchangeable.
    """

    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token.encode("utf-8")
        salt = secrets.token_bytes(32)
        self._session_value = hmac.new(
            salt, self._token, hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _equal(candidate: str | None, expected: bytes) -> bool:
        if not candidate:
            return False
        return hmac.compare_digest(candidate.encode("utf-8"), expected)

    def _presented_fresh(self, request) -> bool:
        header = request.headers.get("authorization", "")
        if header[:7].lower() == "bearer " and self._equal(
            header[7:].strip(), self._token
        ):
            return True
        return self._equal(request.query_params.get(TOKEN_PARAM), self._token)

    async def dispatch(self, request, call_next):
        if request.url.path == "/healthz":
            # A supervisor probe carries no token.
            return await call_next(request)
        fresh = self._presented_fresh(request)
        if not fresh and not self._equal(
            request.cookies.get(SESSION_COOKIE), self._session_value.encode("ascii")
        ):
            return _error_response(
                request,
                401,
                "unauthorized",
                "This server requires its access token. Pass it as "
                "Authorization: Bearer <token> or ?token=<token> once; a "
                "cookie keeps you signed in.",
            )
        response = await call_next(request)
        if fresh:
            response.set_cookie(
                SESSION_COOKIE,
                self._session_value,
                httponly=True,
                samesite="strict",
                path="/",
            )
        return response


def install_hardening(app, *, token: str | None) -> None:
    """Add the middlewares; each add wraps the ones before it."""
    if token:
        app.add_middleware(TokenAuthMiddleware, token=token)
    app.add_middleware(LimitsMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
