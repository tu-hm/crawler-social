"""Regression tests for the server and UI defects fixed in this pass.

Each test names the wrong behaviour it locks out, so a future change that
reintroduces it fails here rather than in someone's browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crawler_social.server.api import csv_cell
from crawler_social.server.format import format_bytes, snippet
from crawler_social.server.hardening import MAX_BODY_BYTES
from tests.conftest import make_config, make_db

PAGE = "https://a.example"


def build(db_file: Path, **kwargs) -> TestClient:
    from crawler_social.server.app import create_app

    make_db(db_file, **kwargs)
    return TestClient(create_app(make_config(db_file)))


@pytest.fixture()
def client(db_file: Path) -> TestClient:
    return build(
        db_file,
        posts=[
            ("p1", PAGE, "a post about cats", "Ann",
             "2026-01-02T10:00:00+00:00", "2026-01-02T09:00:00+00:00"),
        ],
        runs=[("2026-01-01T00:00:00+00:00", "completed",
               "2026-01-01T00:05:00+00:00", None)],
        snapshots=[(0, PAGE, "2026-01-01T00:01:00+00:00", b"<html></html>")],
    )


# -- shared page chrome -----------------------------------------------------


def _footer_counts(body: str) -> str:
    match = re.search(r"<footer.*?</footer>", body, re.S)
    assert match, "no footer in the response"
    return " ".join(match.group(0).split())


@pytest.mark.parametrize("path", ["/nope", "/posts/does-not-exist", "/runs/999"])
def test_not_found_pages_render_complete_chrome(client: TestClient, path: str):
    # 404 and error pages used to be rendered from a bare context, so
    # base.html printed "crawler-social v" with no version, an empty
    # database path, and "  posts ·   snapshots ·   runs".
    resp = client.get(path)
    assert resp.status_code == 404
    counts = _footer_counts(resp.text)
    assert "crawler-social v0." in counts
    assert "1 post ·" in counts
    assert "1 snapshot ·" in counts
    assert "1 run" in counts
    assert re.search(r'class="db-path"[^>]*>[^<\s]', resp.text), "db path is blank"


def test_error_page_renders_complete_chrome(client: TestClient):
    # A 400 raised inside LimitsMiddleware renders through the same path.
    resp = client.get("/posts", params={"q": "x" * 600})
    assert resp.status_code == 400
    assert "crawler-social v0." in _footer_counts(resp.text)


def test_crawl_page_footer_counts_come_from_the_database(client: TestClient):
    # _crawl_context built its chrome without a connection, so /crawl
    # always claimed "0 posts · 0 snapshots · 0 runs".
    counts = _footer_counts(client.get("/crawl").text)
    assert "1 post ·" in counts and "1 run" in counts
    assert "0 post" not in counts


def test_footer_counts_are_singular_for_one(client: TestClient):
    assert "1 posts" not in client.get("/").text
    assert "1 runs" not in client.get("/").text


# -- accessibility / markup -------------------------------------------------


def test_active_nav_link_is_marked_current(client: TestClient):
    body = client.get("/posts").text
    assert re.search(r'href="/posts"[^>]*aria-current="page"', body)
    # ...and only the active one.
    assert body.count('aria-current="page"') == 1


def test_every_page_offers_a_skip_link_to_the_content(client: TestClient):
    body = client.get("/").text
    assert 'class="skip-link" href="#main"' in body
    assert 'id="main"' in body


@pytest.mark.parametrize(
    "path", ["/posts/p1", "/runs/1", "/snapshots/1"]
)
def test_description_lists_are_dl_elements(client: TestClient, path: str):
    # dt/dd are only valid inside a dl. These three pages wrapped them in
    # a div, which drops the list semantics a screen reader relies on.
    body = client.get(path).text
    assert '<dl class="detail-grid">' in body
    assert '<div class="detail-grid">' not in body
    detail = body.split('<dl class="detail-grid">', 1)[1].split("</dl>", 1)[0]
    assert "<dt>" in detail and "<dd" in detail


def test_home_post_links_survive_a_slash_in_the_post_id(db_file: Path):
    # index.html used Jinja's | urlencode, which leaves "/" alone, so a
    # post id containing one linked to the wrong path.
    client = build(
        db_file,
        posts=[("123/456", PAGE, "sliced id", "Ann", None,
                "2026-01-01T09:00:00+00:00")],
    )
    home = client.get("/").text
    assert "/posts/123%2F456" in home
    assert 'href="/posts/123/456"' not in home
    assert client.get("/posts/123%2F456").status_code == 200


# -- paging -----------------------------------------------------------------


@pytest.mark.parametrize("path", ["/posts", "/snapshots", "/runs"])
def test_paging_past_the_end_returns_to_the_last_page(db_file: Path, path: str):
    # An offset past the end rendered an empty table under "No posts
    # stored yet." -- false, and a dead end with no pagination controls.
    client = build(
        db_file,
        posts=[(f"p{i}", PAGE, f"text {i}", "Ann", None,
                f"2026-01-{i + 1:02d}T09:00:00+00:00") for i in range(5)],
        runs=[("2026-01-01T00:00:00+00:00", "completed",
               "2026-01-01T00:05:00+00:00", None)],
        snapshots=[(0, PAGE, "2026-01-01T00:01:00+00:00", b"<html></html>")],
    )
    hop = client.get(f"{path}?limit=2&offset=100", follow_redirects=False)
    assert hop.status_code == 303
    landed = client.get(f"{path}?limit=2&offset=100")
    assert landed.status_code == 200
    assert "stored yet" not in landed.text
    # One hop only: the clamped offset is inside the result set.
    assert client.get(
        hop.headers["location"], follow_redirects=False
    ).status_code == 200


def test_paging_inside_the_result_set_is_not_redirected(client: TestClient):
    assert client.get("/posts?offset=0", follow_redirects=False).status_code == 200


# -- search -----------------------------------------------------------------


def test_search_hit_beyond_the_excerpt_is_still_highlighted(db_file: Path):
    # The excerpt was taken from the head of the text before highlighting,
    # so a match past 180 characters produced a row with no visible
    # reason it matched.
    text = "filler words here " * 30 + "NEEDLE " + "trailing words " * 30
    client = build(
        db_file,
        posts=[("p1", PAGE, text, "Ann", None, "2026-01-01T09:00:00+00:00")],
    )
    body = client.get("/posts", params={"q": "NEEDLE"}).text
    cell = re.search(r'class="post-link excerpt"[^>]*>(.*?)</a>', body, re.S)
    assert cell, "no result row"
    assert "<mark>NEEDLE</mark>" in cell.group(1)


def test_snippet_falls_back_to_the_head_without_a_match():
    assert snippet("a" * 300, "zzz", 20).startswith("a")
    assert snippet("a" * 300, None, 20).endswith("…")
    assert snippet(None, "x", 20) == ""
    assert snippet("short", "short", 20) == "short"


def test_search_text_is_still_escaped_when_it_is_the_match(db_file: Path):
    client = build(
        db_file,
        posts=[("p1", PAGE, "<script>alert(1)</script>", "Ann", None,
                "2026-01-01T09:00:00+00:00")],
    )
    body = client.get("/posts", params={"q": "script"}).text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;" in body


# -- units ------------------------------------------------------------------


def test_format_bytes_climbs_past_gigabytes():
    # The ladder stopped at GB and had an unreachable trailing return.
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1024 ** 3) == "1.0 GB"
    assert format_bytes(1024 ** 4) == "1.0 TB"
    assert format_bytes(5 * 1024 ** 4) == "5.0 TB"


# -- CSV export -------------------------------------------------------------


def test_csv_cells_that_look_like_formulas_are_neutralised():
    assert csv_cell("=1+1") == "'=1+1"
    assert csv_cell("+A1") == "'+A1"
    assert csv_cell("-1+cmd|'/c calc'!A1") == "'-1+cmd|'/c calc'!A1"
    assert csv_cell("@SUM(A1)") == "'@SUM(A1)"
    assert csv_cell("ordinary text") == "ordinary text"
    assert csv_cell(None) is None
    assert csv_cell(7) == 7


def test_csv_export_neutralises_a_formula_in_post_text(db_file: Path):
    client = build(
        db_file,
        posts=[("p1", PAGE, "=cmd|'/c calc'!A1", "Ann", None,
                "2026-01-01T09:00:00+00:00")],
    )
    body = client.get("/api/export/posts.csv").text
    assert "'=cmd" in body
    assert not re.search(r"(^|,)=cmd", body, re.M)


def test_csv_export_streams_every_page_of_a_large_result_set(db_file: Path):
    # More rows than MAX_PAGE_SIZE, so the paging loop in rows() runs more
    # than once and the connection must outlive the handler.
    total = 450
    client = build(
        db_file,
        posts=[(f"p{i:04d}", PAGE, f"text {i}", "Ann", None,
                "2026-01-01T09:00:00+00:00") for i in range(total)],
    )
    body = client.get("/api/export/posts.csv").text
    rows = [line for line in body.splitlines() if line.strip()]
    assert len(rows) == total + 1  # + the header
    ids = {line.split(",", 1)[0] for line in rows[1:]}
    assert len(ids) == total, "the CSV repeated or skipped rows"


# -- limits -----------------------------------------------------------------


def test_oversized_request_body_is_refused(client: TestClient):
    resp = client.post(
        "/crawl",
        data={"csrf_token": "x", "page_url": "https://www.facebook.com/x",
              "pad": "A" * (MAX_BODY_BYTES + 1)},
    )
    assert resp.status_code == 413


def test_a_normal_form_post_is_not_caught_by_the_body_limit(client: TestClient):
    # No CSRF token, so it stops at 403 -- the point is that it is not 413.
    resp = client.post("/crawl", data={"page_url": "https://www.facebook.com/x"})
    assert resp.status_code == 403


def test_bad_content_length_is_a_400(client: TestClient):
    resp = client.request(
        "POST", "/crawl", content=b"", headers={"Content-Length": "not-a-number"}
    )
    assert resp.status_code == 400


# -- older databases --------------------------------------------------------


def test_pages_render_against_a_database_written_before_v3(tmp_path: Path):
    # posts.post_url and the comments table arrived in v3; the viewer opens
    # the file read-only and cannot migrate it, so it must read without them.
    import sqlite3

    from crawler_social.server.app import create_app

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
            finished_at TEXT, status TEXT NOT NULL, error TEXT);
        CREATE TABLE snapshots (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
            page_url TEXT NOT NULL, captured_at TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE, html BLOB NOT NULL);
        CREATE TABLE posts (post_id TEXT PRIMARY KEY, page_url TEXT NOT NULL,
            text TEXT, author TEXT, published_at TEXT,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
        CREATE TABLE state (page_url TEXT PRIMARY KEY, last_post_id TEXT,
            last_post_time TEXT, updated_at TEXT NOT NULL);
        INSERT INTO runs VALUES (1, '2026-01-01T00:00:00+00:00',
            '2026-01-01T00:05:00+00:00', 'completed', NULL);
        INSERT INTO snapshots VALUES (1, 1, 'https://a.example',
            '2026-01-01T00:01:00+00:00', 'deadbeef', X'3c68746d6c3e3c2f68746d6c3e');
        INSERT INTO posts VALUES ('p1', 'https://a.example', 'old row', 'Ann',
            NULL, '2026-01-01T09:00:00+00:00', '2026-01-01T09:00:00+00:00');
        INSERT INTO state VALUES ('https://a.example', 'p1',
            '2026-01-01T09:00:00+00:00', '2026-01-01T09:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    client = TestClient(create_app(make_config(path)))
    for route in ("/", "/posts", "/posts/p1", "/runs", "/runs/1", "/snapshots",
                  "/snapshots/1", "/state", "/crawl",
                  "/api/summary", "/api/posts", "/api/posts/p1",
                  "/api/posts/p1/comments", "/api/runs", "/api/state",
                  "/api/export/posts.csv"):
        resp = client.get(route)
        assert resp.status_code == 200, f"{route} -> {resp.status_code}"
    assert client.get("/api/summary").json()["total_comments"] == 0


# -- degrading instead of failing -------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", "No database yet"),
        ("/posts", "No posts stored yet"),
        ("/runs", "No runs stored yet"),
        ("/snapshots", "No snapshots stored yet"),
        ("/state", "No watermarks stored yet"),
    ],
)
def test_list_pages_show_an_empty_state_before_the_first_crawl(
    tmp_path: Path, path: str, expected: str
):
    # /runs, /snapshots and /state used to answer 404 "There is no page at
    # this address." when the database file did not exist yet, which makes
    # a working nav link look broken.
    from crawler_social.server.app import create_app

    client = TestClient(create_app(make_config(tmp_path / "absent.db")))
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    assert expected in resp.text


def test_a_genuinely_missing_address_is_still_a_404(tmp_path: Path):
    from crawler_social.server.app import create_app

    client = TestClient(create_app(make_config(tmp_path / "absent.db")))
    assert client.get("/nope").status_code == 404
    # ...and so is a specific row that does not exist.
    assert client.get("/runs/1").status_code == 404
    assert client.get("/snapshots/1").status_code == 404


def test_reparse_survives_an_unreadable_capture_time(db_file: Path):
    # datetime.fromisoformat on a bad stored value took the whole page
    # down with a 500; it now reparses and says the timestamp is unusable.
    import sqlite3

    client = build(
        db_file,
        runs=[("2026-01-01T00:00:00+00:00", "completed",
               "2026-01-01T00:05:00+00:00", None)],
        snapshots=[(0, PAGE, "2026-01-01T00:01:00+00:00",
                    b"<html><body>hi</body></html>")],
    )
    conn = sqlite3.connect(db_file)
    conn.execute("UPDATE snapshots SET captured_at = 'not-a-date' WHERE id = 1")
    conn.commit()
    conn.close()

    resp = client.get("/snapshots/1/reparse")
    assert resp.status_code == 200
    assert "not an ISO-8601 timestamp" in resp.text
    # The metadata and source views were already tolerant; keep them so.
    assert client.get("/snapshots/1").status_code == 200
    assert client.get("/snapshots/1/source").status_code == 200


# -- HTTP method handling ---------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/healthz", "/", "/posts", "/posts/p1", "/runs", "/runs/1",
        "/snapshots", "/snapshots/1", "/snapshots/1/source",
        "/snapshots/1/reparse", "/state", "/crawl",
        "/api/summary", "/api/posts", "/api/posts/p1", "/api/runs",
        "/api/runs/1", "/api/snapshots", "/api/snapshots/1/raw",
        "/api/snapshots/1/download", "/api/state", "/api/pages",
        "/api/crawl/status", "/api/export/posts.csv",
    ],
)
def test_head_answers_wherever_get_does(client: TestClient, path: str):
    # FastAPI's APIRoute does not add HEAD to a GET route the way
    # Starlette's plain Route does, so every path answered 405 -- most
    # awkwardly /healthz, which exists for supervisor probes.
    assert client.get(path).status_code == 200, f"GET {path} is not 200"
    assert client.head(path).status_code == 200, f"HEAD {path} -> 405"


def test_post_only_routes_still_refuse_head(client: TestClient):
    # /crawl/stop is POST-only, so HEAD must stay a 405. (/crawl itself
    # has a GET route as well, so HEAD there is a 200 on purpose.)
    assert client.head("/crawl/stop").status_code == 405
    assert client.head("/crawl").status_code == 200


def test_head_keeps_the_snapshot_routes_own_strict_csp(client: TestClient):
    # The untrusted snapshot blob must not inherit the permissive app CSP.
    for method in (client.get, client.head):
        resp = method("/api/snapshots/1/raw")
        csp = resp.headers["content-security-policy"]
        assert csp == "default-src 'none'; style-src 'unsafe-inline'"
        assert resp.headers["x-frame-options"] == "SAMEORIGIN"
