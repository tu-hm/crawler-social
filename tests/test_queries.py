"""Required tests from plans/v2/01-read-query-layer.md."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from crawler_social import db, queries
from crawler_social.queries import DatabaseMissingError


def seed_db(path: Path) -> None:
    """A known dataset: 4 posts on 2 pages, 2 runs, 3 snapshots, 1 state row."""
    conn = db.connect(path)
    try:
        run1 = db.start_run(conn, "2026-01-01T00:00:00+00:00")
        db.finish_run(conn, run1, "completed", finished_at="2026-01-01T00:05:00+00:00")
        run2 = db.start_run(conn, "2026-01-02T00:00:00+00:00")
        db.finish_run(
            conn, run2, "failed", "boom", finished_at="2026-01-02T00:05:00+00:00"
        )
        db.save_snapshot(conn, run1, "https://a.example", "2026-01-01T00:01:00+00:00", b"<html>a1</html>")
        db.save_snapshot(conn, run1, "https://b.example", "2026-01-01T00:02:00+00:00", b"<html>b1</html>")
        db.save_snapshot(conn, run2, "https://a.example", "2026-01-02T00:01:00+00:00", b"<html>a2</html>")

        posts = [
            # post_id, page, text, author, published_at, first/last seen
            ("p-old", "https://a.example", "old post about cats", "Ann",
             "2026-01-01T10:00:00+00:00", "2026-01-01T00:03:00+00:00"),
            ("p-mid", "https://a.example", "middle post about dogs", "Bob",
             "2026-01-02T10:00:00+00:00", "2026-01-02T00:03:00+00:00"),
            ("p-new", "https://b.example", "newest post 100% done", None,
             "2026-01-03T10:00:00+00:00", "2026-01-03T00:03:00+00:00"),
            ("p-nodate", "https://b.example", "no published time here", "Dan",
             None, "2026-01-01T12:00:00+00:00"),
        ]
        with db.post_transaction(conn):
            for post_id, page, text, author, published, seen in posts:
                db.upsert_post(conn, post_id, page, text, author, published, seen)
            db.set_state(conn, "https://a.example", "p-mid",
                         "2026-01-02T10:00:00+00:00", "2026-01-02T00:03:00+00:00")
    finally:
        conn.close()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "social.db"
    seed_db(path)
    return path


@pytest.fixture()
def ro(db_path: Path):
    conn = queries.connect_ro(db_path)
    yield conn
    conn.close()


def test_connect_ro_missing_file_raises_naming_path(tmp_path: Path):
    missing = tmp_path / "nope.db"
    with pytest.raises(DatabaseMissingError) as excinfo:
        queries.connect_ro(missing)
    assert str(missing) in str(excinfo.value)
    assert "crawler crawl" in str(excinfo.value)


def test_connect_ro_cannot_create_or_insert(ro, db_path: Path):
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("CREATE TABLE hack (x INTEGER)")
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO posts VALUES ('x','y',NULL,NULL,NULL,'a','a')")


def test_list_posts_newest_first_and_oldest_reverses(ro):
    rows, total = queries.list_posts(ro, limit=10)
    assert total == 4
    assert [r["post_id"] for r in rows] == ["p-new", "p-mid", "p-nodate", "p-old"]
    rows_old, _ = queries.list_posts(ro, limit=10, order="oldest")
    assert [r["post_id"] for r in rows_old] == list(reversed(
        [r["post_id"] for r in rows]
    ))


def test_list_posts_paging_stable(ro):
    first, total = queries.list_posts(ro, limit=2, offset=0)
    second, _ = queries.list_posts(ro, limit=2, offset=2)
    wide, _ = queries.list_posts(ro, limit=4, offset=0)
    assert total == 4
    assert {r["post_id"] for r in first}.isdisjoint({r["post_id"] for r in second})
    assert [r["post_id"] for r in first + second] == [r["post_id"] for r in wide]


def test_list_posts_contains_matches_substring(ro):
    rows, total = queries.list_posts(ro, limit=10, contains="dogs")
    assert total == 1
    assert [r["post_id"] for r in rows] == ["p-mid"]


def test_list_posts_contains_percent_and_underscore_are_literal(ro):
    rows, total = queries.list_posts(ro, limit=10, contains="100%")
    assert [r["post_id"] for r in rows] == ["p-new"]
    assert total == 1
    rows, _ = queries.list_posts(ro, limit=10, contains="_done")
    assert rows == []  # no literal "_done" in any post


def test_list_posts_filters_by_page_url(ro):
    rows, total = queries.list_posts(ro, limit=10, page_url="https://a.example")
    assert total == 2
    assert {r["post_id"] for r in rows} == {"p-old", "p-mid"}
    for row in rows:
        assert row["page_url"] == "https://a.example"


def test_list_posts_since_until(ro):
    rows, total = queries.list_posts(
        ro, limit=10, since="2026-01-02T00:00:00+00:00"
    )
    assert total == 2
    assert {r["post_id"] for r in rows} == {"p-new", "p-mid"}
    rows, total = queries.list_posts(
        ro, limit=10, until="2026-01-02T00:00:00+00:00"
    )
    assert {r["post_id"] for r in rows} == {"p-old", "p-nodate"}


def test_limit_and_offset_are_clamped(ro):
    # Only 4 rows exist, so a clamped limit of 200 cannot be observed by row
    # count alone; clamp behavior is checked through the module constant.
    rows, _ = queries.list_posts(ro, limit=10_000)
    assert len(rows) == 4
    rows, _ = queries.list_posts(ro, limit=10, offset=-5)
    assert len(rows) == 4  # offset clamped to 0, not an error


def test_limit_clamped_to_max_page_size(ro, monkeypatch):
    monkeypatch.setattr(queries, "MAX_PAGE_SIZE", 2)
    rows, _ = queries.list_posts(ro, limit=10_000)
    assert len(rows) == 2


def test_get_post_roundtrip_and_missing(ro):
    post = queries.get_post(ro, "p-mid")
    assert post["text"] == "middle post about dogs"
    assert post["author"] == "Bob"
    assert queries.get_post(ro, "nope") is None


def test_list_pages_counts(ro):
    pages = queries.list_pages(ro)
    assert len(pages) == 2
    by_url = {p["page_url"]: p for p in pages}
    assert by_url["https://a.example"]["post_count"] == 2
    assert by_url["https://b.example"]["post_count"] == 2
    assert by_url["https://b.example"]["newest_post_at"] == "2026-01-03T10:00:00+00:00"


def test_list_runs_newest_first_with_snapshot_counts(ro):
    rows, total = queries.list_runs(ro, limit=10)
    assert total == 2
    assert [r["id"] for r in rows] == [2, 1]
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == "boom"
    assert rows[0]["snapshot_count"] == 1
    assert rows[1]["snapshot_count"] == 2


def test_get_run(ro):
    run = queries.get_run(ro, 1)
    assert run["status"] == "completed"
    assert run["snapshot_count"] == 2
    assert queries.get_run(ro, 99) is None


def test_list_snapshots_metadata_only(ro):
    rows, total = queries.list_snapshots(ro, limit=10)
    assert total == 3
    assert rows[0]["size_bytes"] == len(b"<html>a2</html>")
    for row in rows:
        assert "html" not in row
        assert set(row) == {
            "id", "run_id", "page_url", "captured_at", "sha256", "size_bytes"
        }


def test_list_snapshots_filters(ro):
    rows, total = queries.list_snapshots(ro, limit=10, run_id=2)
    assert total == 1
    assert rows[0]["page_url"] == "https://a.example"
    rows, total = queries.list_snapshots(ro, limit=10, page_url="https://b.example")
    assert total == 1


def test_get_snapshot_html_exact_bytes(ro):
    html = queries.get_snapshot_html(ro, 1)
    assert html == b"<html>a1</html>"
    assert queries.get_snapshot_html(ro, 99) is None


def test_get_state_rows(ro):
    state = queries.get_state(ro)
    assert len(state) == 1
    assert state[0]["last_post_id"] == "p-mid"
    assert state[0]["page_url"] == "https://a.example"


def test_summary_on_empty_database(tmp_path: Path):
    path = tmp_path / "empty.db"
    db.connect(path).close()
    conn = queries.connect_ro(path)
    try:
        s = queries.summary(conn)
    finally:
        conn.close()
    assert s["total_posts"] == 0
    assert s["total_snapshots"] == 0
    assert s["total_runs"] == 0
    assert s["last_run"] is None
    assert s["newest_post_at"] is None
    assert s["total_snapshot_bytes"] == 0


def test_summary_counts(db_path: Path):
    conn = queries.connect_ro(db_path)
    try:
        s = queries.summary(conn)
    finally:
        conn.close()
    assert s["total_posts"] == 4
    assert s["total_snapshots"] == 3
    assert s["total_runs"] == 2
    assert s["last_run"]["id"] == 2
    assert s["newest_post_at"] == "2026-01-03T10:00:00+00:00"
    assert s["total_snapshot_bytes"] == (
        len(b"<html>a1</html>") + len(b"<html>b1</html>") + len(b"<html>a2</html>")
    )


def test_extended_schema_applies_twice_safely(tmp_path: Path):
    path = tmp_path / "social.db"
    db.connect(path).close()
    db.connect(path).close()  # the new indexes are IF NOT EXISTS
    with sqlite3.connect(path) as conn:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert {"idx_posts_page_url", "idx_posts_sort", "idx_snapshots_run",
            "idx_snapshots_page"} <= names


# -- v3: comments, and reading a database an older crawler wrote -------------


def seed_comments(path: Path) -> None:
    conn = db.connect(path)
    try:
        with db.transaction(conn):
            db.upsert_comment(conn, "c2", "p-old", "u", "Bea", "second", None, 2, 2)
            db.upsert_comment(conn, "c1", "p-old", "u", "Ann", "first", None, 9, 1)
            db.upsert_comment(conn, "c3", "p-mid", "u", "Cid", "other", None, 0, 1)
    finally:
        conn.close()


def test_list_comments_is_ordered_by_facebooks_own_rank(db_path: Path):
    seed_comments(db_path)
    conn = queries.connect_ro(db_path)
    try:
        rows, total = queries.list_comments(conn, post_id="p-old", limit=10)
    finally:
        conn.close()
    assert total == 2
    assert [r["comment_id"] for r in rows] == ["c1", "c2"]
    assert rows[0]["like_count"] == 9


def test_comment_counts_are_one_query_for_many_posts(db_path: Path):
    seed_comments(db_path)
    conn = queries.connect_ro(db_path)
    try:
        counts = queries.comment_counts(conn, ["p-old", "p-mid", "p-new"])
    finally:
        conn.close()
    assert counts["p-old"] == 2
    assert counts["p-mid"] == 1
    assert counts.get("p-new", 0) == 0


def test_summary_counts_comments(db_path: Path):
    seed_comments(db_path)
    conn = queries.connect_ro(db_path)
    try:
        assert queries.summary(conn)["total_comments"] == 3
    finally:
        conn.close()


def test_the_viewer_reads_a_database_written_before_v3(tmp_path: Path):
    """The viewer opens the file read-only, so it can never migrate it."""
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.executescript(
            """
            CREATE TABLE posts (
                post_id TEXT PRIMARY KEY, page_url TEXT NOT NULL, text TEXT,
                author TEXT, published_at TEXT, first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL, error TEXT
            );
            CREATE TABLE snapshots (
                id INTEGER PRIMARY KEY, run_id INTEGER, page_url TEXT NOT NULL,
                captured_at TEXT NOT NULL, html BLOB NOT NULL,
                sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL
            );
            CREATE TABLE state (
                page_url TEXT PRIMARY KEY, last_post_id TEXT,
                last_post_time TEXT, updated_at TEXT NOT NULL
            );
            INSERT INTO posts VALUES ('p1','https://a.example','t',NULL,NULL,
                '2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00');
            """
        )
    conn = queries.connect_ro(path)
    try:
        rows, total = queries.list_posts(conn, limit=10)
        assert total == 1
        assert "post_url" not in rows[0]
        assert queries.get_post(conn, "p1") is not None
        # The comments table does not exist; asking for comments is empty,
        # not an error.
        assert queries.list_comments(conn, post_id="p1", limit=10) == ([], 0)
        assert queries.comment_counts(conn, ["p1"]) == {}
        assert queries.summary(conn)["total_comments"] == 0
    finally:
        conn.close()

