"""FastAPI application factory for the local web viewer.

The server only ever reads: each request opens its own read-only SQLite
connection through `queries.connect_ro` and closes it in a `finally`.
Connections are never cached -- SQLite connections are not safe to share
across threads, and a per-request connection sees committed writes
immediately thanks to WAL.
"""

from __future__ import annotations

import secrets
import sqlite3
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import Config
from ..queries import DatabaseMissingError, connect_ro
from .hardening import MIN_TOKEN_CHARS, install_hardening, wants_json

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback_host(host: str) -> bool:
    """True for the addresses that only ever reach this machine."""
    return host in LOOPBACK_HOSTS


def validate_remote_bind(
    host: str, *, allow_remote: bool, token: str | None
) -> str | None:
    """Why a non-loopback bind may not start, or None if it may.

    Both guards must pass, and the refusal names the missing one so the
    operator is never left guessing (plans/v2/09).
    """
    if is_loopback_host(host):
        return None
    if not allow_remote:
        return (
            f"Refusing to bind {host}: that address is reachable from the "
            "network. Pass --allow-remote if you really mean it."
        )
    if not token:
        return (
            f"Refusing to bind {host}: remote access needs authentication. "
            f"Set CRAWLER_SERVE_TOKEN to at least {MIN_TOKEN_CHARS} characters."
        )
    if len(token) < MIN_TOKEN_CHARS:
        return (
            f"Refusing to bind {host}: CRAWLER_SERVE_TOKEN must be at least "
            f"{MIN_TOKEN_CHARS} characters; this one is {len(token)}."
        )
    return None


class DatabaseUnavailable(Exception):
    """Raised inside the connection dependency when the db file is absent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path


@asynccontextmanager
async def _lifespan(app: FastAPI) -> Iterator[None]:
    # Nothing to open at startup: connections are per-request on purpose.
    yield


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """One read-only connection per request, always closed in a finally."""
    config = request.app.state.config
    try:
        conn = connect_ro(config.db_path)
    except DatabaseMissingError:
        raise DatabaseUnavailable(config.db_path) from None
    try:
        yield conn
    finally:
        conn.close()


def create_app(config: Config) -> FastAPI:
    """Build the viewer app. Opens no database connection at import time."""
    app = FastAPI(
        title="crawler-social",
        version=__version__,
        # The viewer is a local tool; exposing an interactive docs surface
        # (and an OpenAPI schema that lists every route) is not worth it,
        # especially on a non-loopback bind.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.config = config
    # One CSRF token per server start: forms embed it, POSTs require it.
    app.state.csrf_token = secrets.token_urlsafe(32)

    from fastapi.staticfiles import StaticFiles

    from .api import router as api_router
    from .pages import (
        crawl_page,
        crawl_start,
        crawl_stop,
        home,
        post_detail,
        posts_list,
        render,
        run_detail,
        runs_list,
        snapshot_detail,
        snapshot_reparse,
        snapshot_source,
        snapshots_list,
        state_view,
    )
    from .templating import STATIC_DIR

    app.include_router(api_router)
    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static",
    )
    app.get("/", response_model=None)(home)
    app.get("/posts", response_model=None)(posts_list)
    app.get("/posts/{post_id:path}", response_model=None)(post_detail)
    app.get("/snapshots", response_model=None)(snapshots_list)
    app.get("/snapshots/{snapshot_id}", response_model=None)(snapshot_detail)
    app.get("/snapshots/{snapshot_id}/source", response_model=None)(snapshot_source)
    app.get("/snapshots/{snapshot_id}/reparse", response_model=None)(snapshot_reparse)
    app.get("/runs", response_model=None)(runs_list)
    app.get("/runs/{run_id}", response_model=None)(run_detail)
    app.get("/state", response_model=None)(state_view)
    app.get("/crawl", response_model=None)(crawl_page)
    app.post("/crawl", response_model=None)(crawl_start)
    app.post("/crawl/stop", response_model=None)(crawl_stop)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        # JSON error shape under /api, a rendered page everywhere else --
        # decided by path prefix, not the Accept header.
        if wants_json(request):
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": str(exc.detail) if exc.detail else "error"},
                headers=exc.headers,
            )
        if exc.status_code == 404:
            return render(request, "404.html", {}, status_code=404)
        return render(
            request,
            "error.html",
            {"title": f"Error {exc.status_code}", "message": str(exc.detail or "")},
            status_code=exc.status_code,
        )

    @app.exception_handler(DatabaseUnavailable)
    async def _database_unavailable(request: Request, exc: DatabaseUnavailable):
        return JSONResponse(
            status_code=503,
            content={
                "error": "database_missing",
                "path": str(exc.db_path),
                "hint": "run `crawler crawl` first",
            },
        )

    @app.exception_handler(Exception)
    async def _internal_error(request: Request, exc: Exception):
        # Full traceback, path and request id go to the server log; the
        # client gets an opaque body plus only that id to quote (plans/v2/09).
        request_id = getattr(request.state, "request_id", None) or secrets.token_hex(8)
        sys.stderr.write(
            f"request {request_id} failed: {request.method} {request.url.path}\n"
        )
        traceback.print_exception(
            type(exc), exc, exc.__traceback__, file=sys.stderr
        )
        if wants_json(request):
            return JSONResponse(
                status_code=500,
                content={"error": "internal", "request_id": request_id},
            )
        return render(
            request,
            "error.html",
            {
                "title": "Server error",
                "message": "The server hit an unexpected error and logged it. "
                "No details are shown here; quote the request id if you report it.",
                "request_id": request_id,
            },
            status_code=500,
        )

    install_hardening(app, token=config.serve_token)

    @app.get("/healthz")
    def healthz(request: Request) -> dict:
        # Must answer even without a database, so it never touches get_conn.
        config_ = request.app.state.config
        return {
            "status": "ok",
            "db": str(config_.db_path),
            "db_present": config_.db_path.exists(),
            "version": __version__,
        }

    return app


def build_app() -> FastAPI:
    """App factory for `uvicorn --reload`, which needs an import string."""
    from ..config import load_config

    return create_app(load_config())
