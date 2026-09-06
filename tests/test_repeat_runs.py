"""Required tests from plans/v1/05-safe-repeat-runs.md."""

from __future__ import annotations

import os
import signal
import sqlite3
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

import pytest

from crawler_social import db, facebook, pipeline
from crawler_social.config import Config
from crawler_social.lock import FileLock, LockBusyError

PAGE_URL = "https://www.facebook.com/ExamplePublicPage"


def make_config(tmp_path: Path) -> Config:
    return Config(
        browser_binary=None,
        profile_dir=tmp_path / "chrome" / "default",
        db_path=tmp_path / "social.db",
        page_url=PAGE_URL,
    )


def page_html(*post_ids: str) -> bytes:
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


def fake_capture(conn, snapshots, fail_after=None):
    """Build a fake capture_snapshots replacement.

    snapshots is a list of html bytes; fail_after=1 raises after the first
    yield; send_sig sends SIGTERM to self after the first yield.
    """

    def capture(page_url, c, run_id, config, options=None, should_stop=None):
        should_stop = should_stop or (lambda: False)
        for index, html in enumerate(snapshots):
            captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.save_snapshot(c, run_id, page_url, captured_at, html)
            yield captured_at, html
            if fail_after is not None and index + 1 >= fail_after:
                raise facebook.CaptureError("simulated browser crash")
            if should_stop():
                return

    return capture


def post_ids_in_db(conn):
    return [r[0] for r in conn.execute("SELECT post_id FROM posts ORDER BY post_id")]


def test_two_identical_runs_leave_one_row_per_post(tmp_path):
    config = make_config(tmp_path)
    html = page_html("1", "2", "3")
    with unittest.mock.patch.object(
        facebook, "capture_snapshots", fake_capture(None, [html])
    ):
        pipeline.run_crawl(PAGE_URL, limit=20, config=config)
        with unittest.mock.patch.object(
            facebook, "capture_snapshots", fake_capture(None, [html])
        ):
            summary = pipeline.run_crawl(PAGE_URL, limit=20, config=config)
    assert summary.new_posts == 0
    assert summary.existing_posts == 3
    conn = sqlite3.connect(config.db_path)
    assert post_ids_in_db(conn) == ["1", "2", "3"]


def test_second_run_advances_last_seen(tmp_path):
    config = make_config(tmp_path)
    html = page_html("1")
    with unittest.mock.patch.object(
        facebook, "capture_snapshots", fake_capture(None, [html])
    ):
        pipeline.run_crawl(PAGE_URL, limit=20, config=config)
    conn = sqlite3.connect(config.db_path)
    first_seen, last_seen = conn.execute(
        "SELECT first_seen, last_seen FROM posts WHERE post_id = '1'"
    ).fetchone()
    import time as _time

    _time.sleep(1.1)
    with unittest.mock.patch.object(
        facebook, "capture_snapshots", fake_capture(None, [html])
    ):
        pipeline.run_crawl(PAGE_URL, limit=20, config=config)
    first_seen2, last_seen2 = conn.execute(
        "SELECT first_seen, last_seen FROM posts WHERE post_id = '1'"
    ).fetchone()
    assert first_seen2 == first_seen
    assert last_seen2 > last_seen


def test_failure_after_snapshot_commit_preserves_snapshot(tmp_path):
    config = make_config(tmp_path)
    html = page_html("1")
    with unittest.mock.patch.object(
        facebook, "capture_snapshots", fake_capture(None, [html], fail_after=1)
    ):
        with pytest.raises(facebook.CaptureError):
            pipeline.run_crawl(PAGE_URL, limit=20, config=config)
    conn = sqlite3.connect(config.db_path)
    count, = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
    assert count == 1
    status, error = conn.execute(
        "SELECT status, error FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert status == "failed"
    assert "simulated browser crash" in error


def test_failure_before_post_commit_preserves_old_state(tmp_path):
    config = make_config(tmp_path)
    conn = db.connect(config.db_path)
    db.set_state(conn, PAGE_URL, "old", "2025-01-01T00:00:00+00:00")
    conn.close()

    html = page_html("1", "2")
    real_upsert = db.upsert_post

    def exploding_upsert(*args, **kwargs):
        real_upsert(*args, **kwargs)
        raise RuntimeError("simulated crash before commit")

    with unittest.mock.patch.object(
        facebook, "capture_snapshots", fake_capture(None, [html])
    ), unittest.mock.patch.object(
        db, "upsert_post", exploding_upsert
    ):
        with pytest.raises(facebook.CaptureError):
            pipeline.run_crawl(PAGE_URL, limit=20, config=config)

    conn = sqlite3.connect(config.db_path)
    state = dict(zip(("page_url", "last_post_id", "last_post_time"), conn.execute(
        "SELECT page_url, last_post_id, last_post_time FROM state"
    ).fetchone()))
    assert state["last_post_id"] == "old"
    assert post_ids_in_db(conn) == []


def test_limit_is_hard_ceiling_on_new_posts(tmp_path):
    config = make_config(tmp_path)
    html = page_html("1", "2", "3", "4", "5")
    with unittest.mock.patch.object(
        facebook, "capture_snapshots", fake_capture(None, [html])
    ):
        summary = pipeline.run_crawl(PAGE_URL, limit=3, config=config)
    assert summary.new_posts == 3
    conn = sqlite3.connect(config.db_path)
    assert len(post_ids_in_db(conn)) == 3


def test_simulated_signal_closes_resources_and_keeps_db_valid(tmp_path):
    config = make_config(tmp_path)

    def capture(page_url, c, run_id, cfg, options=None, should_stop=None):
        html = page_html("1")
        captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.save_snapshot(c, run_id, page_url, captured_at, html)
        yield captured_at, html
        os.kill(os.getpid(), signal.SIGTERM)

    with unittest.mock.patch.object(facebook, "capture_snapshots", capture):
        summary = pipeline.run_crawl(PAGE_URL, limit=20, config=config)
    assert summary.status == "interrupted"
    conn = sqlite3.connect(config.db_path)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert post_ids_in_db(conn) == ["1"]
    row = conn.execute(
        "SELECT status FROM runs WHERE id = ?", (summary.run_id,)
    ).fetchone()
    assert row[0] == "interrupted"


def test_second_process_receives_clear_lock_message(tmp_path):
    lock = FileLock(tmp_path / "browser.lock")
    lock.acquire()
    try:
        second = FileLock(tmp_path / "browser.lock")
        with pytest.raises(LockBusyError) as excinfo:
            second.acquire()
        assert "lock" in str(excinfo.value)
    finally:
        lock.release()
    # After release the lock is available again.
    FileLock(tmp_path / "browser.lock").acquire()


def test_run_prints_counts(tmp_path):
    config = make_config(tmp_path)
    html = page_html("1", "2")
    with unittest.mock.patch.object(
        facebook, "capture_snapshots", fake_capture(None, [html])
    ):
        summary = pipeline.run_crawl(PAGE_URL, limit=20, config=config)
    assert (summary.snapshots_captured, summary.new_posts, summary.existing_posts) == (1, 2, 0)
