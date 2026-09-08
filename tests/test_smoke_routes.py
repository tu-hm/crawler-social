"""Verification gate for the web surface.

Two jobs:

1. Walk every registered route with representative parameters against a
   seeded temporary database and assert none returns 5xx. The count
   assertion at the bottom means a new route cannot be added without
   passing through this smoke walk.
2. A fixture-backed end-to-end path with no browser and no network: the
   sample HTML is parsed, its text stored through `db.py`, and the posts
   must appear on `/posts` and on their detail pages. The markup itself is
   never stored, so the database is checked to be sure it holds none.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from crawler_social import db, parser
from crawler_social.server.app import create_app

from tests.conftest import make_config, make_db

PAGE_URL = "https://www.facebook.com/ExamplePublicPage"
CAPTURED_AT = datetime(2026, 2, 4, 12, 0, 0, tzinfo=timezone.utc)

PATH_PARAMS = {
    "post_id": "p1",
    "snapshot_id": "1",
    "run_id": "1",
}

ROUTE_QUERIES = {
    "/posts": "?q=hello&order=oldest&limit=25&offset=0&since=2026-01-01&until=2026-12-31",
    "/api/posts": "?q=hello&order=oldest&limit=25&offset=0&since=2026-01-01",
    "/api/export/posts.csv": "?q=hello",
    "/": "?since=2026-01-01",
}


@pytest.fixture()
def seeded_app(db_file: Path):
    make_db(
        db_file,
        posts=[
            (
                "p1",
                PAGE_URL,
                "hello world",
                None,
                "2026-02-04T12:00:00+00:00",
                "2026-02-04T12:00:00+00:00",
            )
        ],
        runs=[("2026-02-04T11:59:00+00:00", "completed", "2026-02-04T11:59:30+00:00", None)],
        snapshots=[(0, PAGE_URL, "2026-02-04T12:00:00+00:00", b"<html><body>raw</body></html>")],
        states=[(PAGE_URL, "p1", "2026-02-04T12:00:00+00:00", "2026-02-04T12:00:00+00:00")],
    )
    return create_app(make_config(db_file))


def _fill(path: str) -> str:
    for name, value in PATH_PARAMS.items():
        path = path.replace("{" + name + ":path}", value)
        path = path.replace("{" + name + "}", value)
    return path


def test_every_registered_route_is_smoke_tested(seeded_app):
    client = TestClient(seeded_app)
    # FastAPI hides include_router()'ed routes behind an _IncludedRouter wrapper.
    api_routes: list[APIRoute] = []

    def walk(routes) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                api_routes.append(route)
            elif isinstance(route, Mount):
                continue
            elif hasattr(route, "original_router"):
                walk(route.original_router.routes)
            else:  # pragma: no cover - a new route kind must be handled here
                raise AssertionError(f"unrecognized route object: {route!r}")

    walk(seeded_app.routes)
    assert api_routes, "no routes discovered"

    exercised: set[tuple[str, tuple[str, ...]]] = set()
    for route in api_routes:
        url = _fill(route.path) + ROUTE_QUERIES.get(route.path, "")
        assert "{" not in url, f"unfilled path parameter in {route.path}"
        methods = tuple(sorted(route.methods - {"HEAD", "OPTIONS"}))
        for method in methods:
            if method == "GET":
                resp = client.get(url)
            else:
                # POST routes are CSRF-protected; the refusal is the point.
                resp = client.request(method, url, data={})
            assert resp.status_code < 500, f"{method} {url} -> {resp.status_code}"
        exercised.add((route.path, methods))

    assert len(exercised) == len(api_routes)
    for route in seeded_app.routes:
        if not isinstance(route, APIRoute):
            assert isinstance(route, Mount) or hasattr(route, "original_router"), (
                f"unaccounted route entry: {route!r}"
            )


def test_fixture_snapshot_end_to_end(db_file: Path):
    """parse -> db -> viewer, with no browser and no network."""
    fixture = Path(__file__).parent / "fixtures" / "facebook_page_sample.html"
    raw_html = fixture.read_bytes()
    posts, _diagnostics = parser.parse(raw_html, PAGE_URL, CAPTURED_AT)
    ids = [p.post_id for p in posts]
    assert [i for i in ids if not i.startswith("h-")] == [
        "777001",
        "777002",
        "777003",
        "777004",
        "6006",
    ]

    captured_at = CAPTURED_AT.isoformat()
    conn = db.connect(db_file)
    try:
        run_id = db.start_run(conn, "2026-02-04T11:59:00+00:00")
        db.save_snapshot(conn, run_id, PAGE_URL, captured_at, raw_html)
        with db.post_transaction(conn):
            for post in posts:
                db.upsert_post(
                    conn,
                    post.post_id,
                    post.page_url,
                    post.text,
                    post.author,
                    post.published_at,
                    captured_at,
                )
        db.finish_run(
            conn, run_id, "completed", None, finished_at="2026-02-04T12:00:10+00:00"
        )
    finally:
        conn.close()

    client = TestClient(create_app(make_config(db_file)))

    listing = client.get("/posts")
    assert listing.status_code == 200
    for post in posts:
        assert f"/posts/{post.post_id}" in listing.text

    for post in posts:
        detail = client.get("/posts/" + quote(post.post_id, safe=""))
        assert detail.status_code == 200, post.post_id
        first_sentence = (post.text or "").split(".")[0]
        assert first_sentence in detail.text

    snapshot = client.get("/snapshots/1")
    assert snapshot.status_code == 200
    assert str(len(raw_html))[:2] in snapshot.text or "KB" in snapshot.text

    ro = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    try:
        assert ro.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        ro.close()
