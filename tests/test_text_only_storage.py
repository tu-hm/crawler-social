"""Text-only storage: a crawl keeps parsed text, never page markup.

One Facebook capture is several megabytes of HTML and every one of them
used to land in the database. Nothing stores markup any more: a capture is
hashed and measured on the way past, the parser reads it in memory, and
what survives is the text -- posts and comments -- plus one metadata row
saying a page was fetched.
"""

from __future__ import annotations

import sqlite3
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crawler_social import db, facebook, pipeline
from crawler_social.config import Config
from tests.conftest import make_config, make_db

PAGE_URL = "https://www.facebook.com/ExamplePublicPage"
HTML = b"<html><body>" + b"x" * 5000 + b"</body></html>"
#: What one real capture weighs, near enough: the point of the change.
BIG_HTML = b"<html><body>" + b"x" * 400_000 + b"</body></html>"


@pytest.fixture()
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "social.db")
    yield c
    c.close()


def test_the_snapshots_table_has_no_markup_column(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    assert "html" not in columns
    assert {"run_id", "page_url", "captured_at", "sha256", "size_bytes"} <= columns


def test_snapshot_records_the_capture_without_keeping_it(conn):
    run_id = db.start_run(conn)
    assert db.save_snapshot(conn, run_id, PAGE_URL, "2026-01-01T00:00:00+00:00", HTML)
    page_url, size, sha = conn.execute(
        "SELECT page_url, size_bytes, sha256 FROM snapshots"
    ).fetchone()
    assert page_url == PAGE_URL
    assert size == len(HTML), "the captured size is still recorded"
    assert len(sha) == 64


def test_checksum_still_deduplicates_an_unchanged_page(conn):
    """The sha256 is over the captured bytes, which is what repeat runs need."""
    run_id = db.start_run(conn)
    assert db.save_snapshot(conn, run_id, PAGE_URL, "2026-01-01T00:00:00+00:00", HTML)
    assert not db.save_snapshot(
        conn, run_id, PAGE_URL, "2026-01-01T00:01:00+00:00", HTML
    )
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def _legacy_db(path: Path) -> None:
    """A database from when snapshots carried their page's markup."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL CHECK (status IN
                ('running', 'completed', 'failed', 'interrupted')),
            error TEXT
        );
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES runs(id),
            page_url TEXT NOT NULL, captured_at TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE, html BLOB NOT NULL
        );
        INSERT INTO runs (started_at, status) VALUES ('2026-01-01T00:00:00+00:00', 'completed');
        """
    )
    for index in range(3):
        conn.execute(
            "INSERT INTO snapshots (run_id, page_url, captured_at, sha256, html)"
            " VALUES (1, ?, ?, ?, ?)",
            (PAGE_URL, f"2026-01-01T00:0{index}:00+00:00", f"{index:064d}", BIG_HTML),
        )
    conn.commit()
    conn.close()


def test_an_old_database_loses_its_markup_and_keeps_its_snapshots(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    before = path.stat().st_size

    conn = db.connect(path)  # the migration runs here
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
        rows = conn.execute(
            "SELECT id, page_url, sha256, size_bytes FROM snapshots ORDER BY id"
        ).fetchall()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()

    assert "html" not in columns
    assert [row[0] for row in rows] == [1, 2, 3], "every snapshot row survives"
    assert all(row[1] == PAGE_URL for row in rows)
    assert [row[3] for row in rows] == [len(BIG_HTML)] * 3, "sizes come from the blob"
    assert before > 1_000_000, "the legacy database really did hold the markup"
    assert path.stat().st_size < 100_000, "VACUUM hands the pages back"

    db.connect(path).close()  # a second connect is a no-op, not a re-migration


def test_migrating_an_old_database_keeps_its_indexes(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    conn = db.connect(path)
    try:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
                " AND tbl_name='snapshots'"
            )
        }
    finally:
        conn.close()
    assert {"idx_snapshots_run", "idx_snapshots_page"} <= indexes


def _fake_capture(html: bytes):
    def capture(page_url, conn, run_id, config, options=None, should_stop=None):
        captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.save_snapshot(conn, run_id, page_url, captured_at, html)
        yield captured_at, html

    return capture


def _page_html(*post_ids: str) -> bytes:
    articles = "".join(
        f"""
        <div role="article" id="a{pid}">
          <h3><a href="/P/" role="link"><strong>P</strong></a></h3>
          <div data-ad-preview="message"><div dir="auto">text {pid}</div></div>
          <abbr data-utime="1770000000">1d</abbr>
          <a href="/P/posts/{pid}">story</a>
        </div>
        """
        for pid in post_ids
    )
    return f"<html><body>{articles}</body></html>".encode()


def test_a_crawl_stores_the_text_and_writes_nothing_else_to_disk(tmp_path):
    config = Config(
        browser_binary=None,
        profile_dir=tmp_path / "chrome" / "default",
        db_path=tmp_path / "data" / "social.db",
        page_url=PAGE_URL,
    )
    html = _page_html("1", "2")
    with unittest.mock.patch.object(
        facebook, "capture_snapshots", _fake_capture(html)
    ):
        summary = pipeline.run_crawl(PAGE_URL, limit=20, config=config)

    assert summary.new_posts == 2
    conn = db.connect(config.db_path)
    try:
        texts = [
            row[0] for row in conn.execute("SELECT text FROM posts ORDER BY post_id")
        ]
        size = conn.execute("SELECT size_bytes FROM snapshots").fetchone()[0]
    finally:
        conn.close()

    assert texts == ["text 1", "text 2"], "the text is what a crawl is for"
    assert size == len(html)
    # The per-run HTML fixture dump was the same markup by another route.
    assert not (config.db_path.parent / "fixtures").exists()
    assert not hasattr(facebook, "save_fixture")


def test_the_database_stays_small_across_many_captures(tmp_path):
    """The size that used to grow by megabytes a capture now barely moves."""
    path = tmp_path / "social.db"
    conn = db.connect(path)
    try:
        run_id = db.start_run(conn)
        big = b"<html>" + bytes(500_000) + b"</html>"
        for index in range(20):
            db.save_snapshot(
                conn, run_id, PAGE_URL, f"2026-01-01T00:{index:02d}:00+00:00",
                big + str(index).encode(),
            )
    finally:
        conn.close()
    assert path.stat().st_size < 100_000, "20 captures of 500 KB, under 100 KB stored"


@pytest.fixture()
def client(db_file: Path) -> TestClient:
    make_db(
        db_file,
        posts=[("p1", PAGE_URL, "hello world", "Ann",
                "2026-02-04T11:00:00+00:00", "2026-02-04T11:00:00+00:00")],
        runs=[("2026-02-04T11:30:00+00:00", "completed",
               "2026-02-04T11:35:00+00:00", None)],
        snapshots=[(0, PAGE_URL, "2026-02-04T11:40:00+00:00", HTML)],
    )
    from crawler_social.server.app import create_app

    return TestClient(create_app(make_config(db_file)))


def test_snapshot_page_reports_the_capture_and_links_to_its_posts(client):
    resp = client.get("/snapshots/1")
    assert resp.status_code == 200
    assert "Captured size" in resp.text
    assert "<iframe" not in resp.text
    assert f"page_url={PAGE_URL.replace(':', '%3A').replace('/', '%2F')}" in resp.text


@pytest.mark.parametrize(
    "path",
    [
        "/snapshots/1/source",
        "/snapshots/1/reparse",
        "/api/snapshots/1/raw",
        "/api/snapshots/1/download",
    ],
)
def test_the_markup_routes_are_gone(client, path):
    assert client.get(path).status_code == 404


def test_api_reports_the_captured_size_and_no_markup_flag(client):
    row = client.get("/api/snapshots").json()["items"][0]
    assert row["size_bytes"] == len(HTML)
    assert "has_html" not in row
    assert "html" not in row
