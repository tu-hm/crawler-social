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
