"""A wall must stop the run, keep its evidence, and hold the watermark.

The failure this guards against: the crawler scrolls a login wall for the full
time budget, stores snapshots with no posts, and reports success.
"""

from __future__ import annotations

import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

from crawler_social import db, facebook, pipeline, wall
from crawler_social.config import Config

PAGE_URL = "https://www.facebook.com/ExamplePublicPage"

LOGIN_WALL_HTML = (
    b"<html><body><h2>Log in to Facebook</h2>"
    b'<input type="password" name="pass"><a>Forgotten password?</a>'
    b"</body></html>"
)


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
          <h3><a href="/P/" role="link"><strong>Page Author Name</strong></a></h3>
          <div data-ad-preview="message"><div dir="auto">post body text {pid}</div></div>
          <abbr data-utime="1770000000">1d</abbr>
          <a href="/P/posts/{pid}">story</a>
        </div>
        """
        for pid in post_ids
    )
    return f"<html><body>{articles}</body></html>".encode()


def capture_then_block(snapshots: list[bytes], wall_html: bytes):
    """Yield good snapshots, then hit a wall -- as the real capture does."""

    def capture(page_url, conn, run_id, config, options=None, should_stop=None):
        for html in snapshots:
            captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.save_snapshot(conn, run_id, page_url, captured_at, html)
            yield captured_at, html
        captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.save_snapshot(conn, run_id, page_url, captured_at, wall_html)
        raise facebook.BlockedError(wall.classify(wall_html, page_url))

    return capture


def run(config, capture, limit=20):
    with unittest.mock.patch.object(facebook, "capture_snapshots", capture):
        with unittest.mock.patch.object(facebook, "save_fixture", lambda *a, **k: None):
            return pipeline.run_crawl(PAGE_URL, limit=limit, config=config)


def test_immediate_login_wall_does_not_raise_and_reports_blocked(tmp_path):
    config = make_config(tmp_path)
    summary = run(config, capture_then_block([], LOGIN_WALL_HTML))
    assert summary.blocked == wall.LOGIN_WALL
    assert summary.new_posts == 0
    assert "login" in (summary.blocked_message or "").lower()


def test_wall_snapshot_is_committed_as_evidence(tmp_path):
    config = make_config(tmp_path)
    run(config, capture_then_block([], LOGIN_WALL_HTML))
    conn = db.connect(config.db_path)
    stored = conn.execute("SELECT html FROM snapshots").fetchall()
    assert [row[0] for row in stored] == [LOGIN_WALL_HTML]
    conn.close()


def test_blocked_run_holds_the_watermark(tmp_path):
    config = make_config(tmp_path)
    summary = run(config, capture_then_block([page_html("1", "2")], LOGIN_WALL_HTML))
    assert summary.blocked == wall.LOGIN_WALL
    conn = db.connect(config.db_path)
    # Posts seen before the wall are kept...
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 2
    # ...but the feed was truncated, so state must not advance past them.
    assert db.get_state(conn, PAGE_URL) is None
    conn.close()


def test_blocked_run_is_recorded_as_failed_with_a_blocked_reason(tmp_path):
    config = make_config(tmp_path)
    summary = run(config, capture_then_block([], LOGIN_WALL_HTML))
    conn = db.connect(config.db_path)
    status, error = conn.execute(
        "SELECT status, error FROM runs WHERE id = ?", (summary.run_id,)
    ).fetchone()
    assert status == "failed"
    assert error.startswith("blocked:login_wall")
    conn.close()


def test_clean_run_still_advances_the_watermark(tmp_path):
    """The guard above must not break the normal path."""
    config = make_config(tmp_path)

    def capture(page_url, conn, run_id, cfg, options=None, should_stop=None):
        html = page_html("1", "2")
        captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.save_snapshot(conn, run_id, page_url, captured_at, html)
        yield captured_at, html

    summary = run(config, capture)
    assert summary.blocked is None
    assert summary.status == "completed"
    conn = db.connect(config.db_path)
    assert db.get_state(conn, PAGE_URL)["last_post_id"] == "1"
    conn.close()
