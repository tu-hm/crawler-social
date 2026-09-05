"""Required tests from plans/v1/02-storage.md."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from crawler_social import db


@pytest.fixture()
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "social.db")
    yield c
    c.close()


def test_schema_applied_twice_is_safe(tmp_path: Path):
    path = tmp_path / "social.db"
    db.connect(path).close()
    db.connect(path).close()  # must not raise
    with sqlite3.connect(path) as c:
        names = {
            r[0]
            for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"runs", "snapshots", "posts", "state"} <= names


def test_identical_html_twice_stores_one_hash_without_raising(conn):
    run_id = db.start_run(conn)
    html = b"<html>same</html>"
    assert db.save_snapshot(conn, run_id, "https://example.com", "2026-01-01T00:00:00+00:00", html)
    assert not db.save_snapshot(conn, run_id, "https://example.com", "2026-01-01T00:01:00+00:00", html)
    count, = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
    assert count == 1


def test_upserting_same_post_twice_leaves_one_row(conn):
    with db.post_transaction(conn):
        db.upsert_post(conn, "p1", "https://example.com", "hello", None, None, "2026-01-01T00:00:00+00:00")
    with db.post_transaction(conn):
        db.upsert_post(conn, "p1", "https://example.com", "hello", None, None, "2026-01-02T00:00:00+00:00")
    rows = conn.execute("SELECT COUNT(*) FROM posts").fetchone()
    assert rows[0] == 1


def test_second_upsert_preserves_first_seen_and_advances_last_seen(conn):
    with db.post_transaction(conn):
        db.upsert_post(conn, "p1", "https://example.com", "hello", None, None, "2026-01-01T00:00:00+00:00")
    with db.post_transaction(conn):
        db.upsert_post(conn, "p1", "https://example.com", "hello", None, None, "2026-01-02T00:00:00+00:00")
    first_seen, last_seen = conn.execute(
        "SELECT first_seen, last_seen FROM posts WHERE post_id = 'p1'"
    ).fetchone()
    assert first_seen == "2026-01-01T00:00:00+00:00"
    assert last_seen == "2026-01-02T00:00:00+00:00"


def test_failed_parser_leaves_committed_snapshot_present(conn):
    run_id = db.start_run(conn)
    db.save_snapshot(conn, run_id, "https://example.com", "2026-01-01T00:00:00+00:00", b"<html>ok</html>")
    with pytest.raises(ValueError):
        with db.post_transaction(conn):
            raise ValueError("simulated parser failure")
    count, = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
    assert count == 1


def test_failed_post_transaction_leaves_previous_state_unchanged(conn):
    db.set_state(conn, "https://example.com", "old_id", "2025-12-01T00:00:00+00:00")
    with pytest.raises(RuntimeError):
        with db.post_transaction(conn):
            db.set_state(conn, "https://example.com", "new_id", "2026-01-01T00:00:00+00:00")
            raise RuntimeError("simulated crash")
    state = db.get_state(conn, "https://example.com")
    assert state["last_post_id"] == "old_id"


def test_pragma_integrity_check_is_ok(conn):
    row = conn.execute("PRAGMA integrity_check").fetchone()
    assert row[0] == "ok"


def test_list_posts_contains_is_parameterized(conn):
    with db.post_transaction(conn):
        db.upsert_post(conn, "p1", "u", "safe text", None, None)
        db.upsert_post(conn, "p2", "u", "other text", None, None)
    rows = db.list_posts(conn, limit=10, contains="%' OR 1=1 --")
    assert rows == []
    rows = db.list_posts(conn, limit=10, contains="safe")
    assert [r[0] for r in rows] == ["p1"]


# -- v3: comments and the additive column migration -------------------------

#: The v2 schema, verbatim in the shape a database written before v3 has:
#: `posts` with no post_url, and no `comments` table at all.
V2_SCHEMA = """
CREATE TABLE posts (
    post_id      TEXT PRIMARY KEY,
    page_url     TEXT NOT NULL,
    text         TEXT,
    author       TEXT,
    published_at TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL
);
"""


def test_connect_migrates_an_old_database_in_place(tmp_path: Path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.executescript(V2_SCHEMA)
        c.execute(
            "INSERT INTO posts VALUES ('p1','https://a.example','t',NULL,NULL,"
            "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
        )

    conn = db.connect(path)
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(posts)")}
        assert "post_url" in columns
        # The existing row survives the migration untouched.
        assert conn.execute("SELECT post_id, post_url FROM posts").fetchone() == (
            "p1",
            None,
        )
        assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0
    finally:
        conn.close()

    # Migrating twice is a no-op, not a duplicate-column error.
    db.connect(path).close()


def test_post_url_is_stored_and_preserved_when_a_later_capture_lacks_it(conn):
    db.upsert_post(
        conn, "p1", "https://a.example", "t", None, None,
        post_url="https://www.facebook.com/P/posts/p1",
    )
    db.upsert_post(conn, "p1", "https://a.example", "t", None, None)
    row = conn.execute("SELECT post_url FROM posts WHERE post_id='p1'").fetchone()
    assert row[0] == "https://www.facebook.com/P/posts/p1"


def test_upserting_a_comment_twice_leaves_one_row(conn):
    db.upsert_post(conn, "p1", "https://a.example", "t", None, None)
    for _ in range(2):
        db.upsert_comment(
            conn, "c1", "p1", "https://a.example/posts/p1", "Ann", "hi",
            None, 5, 1,
        )
    assert db.comment_count(conn, "p1") == 1


def test_a_comment_rank_is_overwritten_but_first_seen_is_not(conn):
    db.upsert_post(conn, "p1", "https://a.example", "t", None, None)
    db.upsert_comment(
        conn, "c1", "p1", "https://a.example/posts/p1", "Ann", "hi", None, 5, 3,
        seen_at="2026-01-01T00:00:00+00:00",
    )
    db.upsert_comment(
        conn, "c1", "p1", "https://a.example/posts/p1", "Ann", "hi", None, 9, 1,
        seen_at="2026-02-01T00:00:00+00:00",
    )
    rank, first_seen, last_seen, likes = conn.execute(
        "SELECT rank_index, first_seen, last_seen, like_count FROM comments"
    ).fetchone()
    # Ranking is Facebook's, and it changes; the row records the latest one.
    assert rank == 1
    assert likes == 9
    assert first_seen == "2026-01-01T00:00:00+00:00"
    assert last_seen == "2026-02-01T00:00:00+00:00"


def test_list_comments_filters_by_post(conn):
    db.upsert_post(conn, "p1", "https://a.example", "t", None, None)
    db.upsert_post(conn, "p2", "https://a.example", "t", None, None)
    db.upsert_comment(conn, "c1", "p1", "u", "Ann", "one", None, None, 1)
    db.upsert_comment(conn, "c2", "p2", "u", "Bob", "two", None, None, 1)
    assert [r[0] for r in db.list_comments(conn, post_id="p1")] == ["c1"]
    assert len(db.list_comments(conn)) == 2

