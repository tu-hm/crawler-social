"""Tests for the comment pass in `pipeline.run_crawl` (plans/v3/03).

`capture_comments` is stubbed the way `capture_snapshots` already is in
tests/test_repeat_runs.py: no browser, no network. What is under test is
the pipeline contract -- comments are committed per post, a comment
failure never costs posts that are already stored, and the pass is off
unless it is asked for.
"""

from __future__ import annotations

import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

from crawler_social import db, facebook, parser, pipeline, wall
from crawler_social.config import Config

PAGE_URL = "https://www.facebook.com/ExamplePublicPage"
FIXTURE = Path(__file__).parent / "fixtures" / "facebook_post_comments.html"


def make_config(tmp_path: Path, **extra) -> Config:
    return Config(
        browser_binary=None,
        profile_dir=tmp_path / "chrome" / "default",
        db_path=tmp_path / "social.db",
        page_url=PAGE_URL,
        **extra,
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


def fake_capture(snapshots):
    def capture(page_url, conn, run_id, config, options=None, should_stop=None):
        for html in snapshots:
            captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.save_snapshot(conn, run_id, page_url, captured_at, html)
            yield captured_at, html

    return capture


def fake_comments(html: bytes | None = None, *, error=None, blocked=False):
    """Stub for facebook.capture_comments; records the targets it received."""
    seen: list[tuple[str, str]] = []

    def capture(targets, conn, run_id, config, options=None, should_stop=None):
        for post_id, post_url in targets:
            seen.append((post_id, post_url))
            captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if blocked:
                verdict = wall.Verdict(wall.RATE_LIMITED, "slow down", "rate limited")
                raise facebook.BlockedError(verdict)
            if error is not None:
                yield facebook.CommentCapture(post_id, post_url, error=error)
                continue
            db.save_snapshot(conn, run_id, post_url, captured_at, html)
            yield facebook.CommentCapture(post_id, post_url, captured_at, html)

    capture.seen = seen
    return capture


def run(config, capture, comments_stub, *, top_comments=3, limit=20):
    with unittest.mock.patch.object(facebook, "capture_snapshots", capture):
        with unittest.mock.patch.object(
            facebook, "capture_comments", comments_stub
        ):
            return pipeline.run_crawl(
                PAGE_URL,
                limit=limit,
                config=config,
                top_comments=top_comments,
            )


def stored_comments(db_path: Path) -> list[tuple]:
    conn = db.connect(db_path)
    try:
        return conn.execute(
            "SELECT post_id, comment_id, author, rank_index FROM comments"
            " ORDER BY post_id, rank_index"
        ).fetchall()
    finally:
        conn.close()


def test_comments_are_stored_for_each_captured_post(tmp_path):
    config = make_config(tmp_path)
    summary = run(
        config, fake_capture([page_html("777001")]), fake_comments(FIXTURE.read_bytes())
    )
    assert summary.status == "completed"
    assert summary.posts_with_comments == 1
    assert summary.comments_captured == 3  # top_comments=3 truncates the five
    rows = stored_comments(config.db_path)
    assert [r[3] for r in rows] == [1, 2, 3]
    assert rows[0][2] == "Alice Nguyen"


def test_the_pass_is_off_unless_asked_for(tmp_path):
    config = make_config(tmp_path)
    comments = fake_comments(FIXTURE.read_bytes())
    summary = run(
        config, fake_capture([page_html("777001")]), comments, top_comments=0
    )
    assert comments.seen == []
    assert summary.comments_captured == 0
    assert stored_comments(config.db_path) == []


def test_max_posts_caps_the_permalink_visits(tmp_path):
    config = make_config(tmp_path)
    comments = fake_comments(FIXTURE.read_bytes())
    with unittest.mock.patch.object(
        facebook, "capture_snapshots", fake_capture([page_html("1", "2", "3", "4")])
    ):
        with unittest.mock.patch.object(facebook, "capture_comments", comments):
            pipeline.run_crawl(
                PAGE_URL,
                limit=20,
                config=config,
                top_comments=2,
                comments_max_posts=2,
            )
    assert len(comments.seen) == 2


def test_a_post_without_a_permalink_falls_back_to_a_constructed_one():
    """Not every article carries a usable link; the pass still needs a URL."""
    linkless = parser.Post(
        post_id="999",
        page_url=PAGE_URL,
        text="t",
        author=None,
        published_at=None,
        url=None,
    )
    targets = pipeline._comment_targets([(linkless, False)], PAGE_URL, 10)
    assert targets == [("999", f"{PAGE_URL}/posts/999")]


def test_new_posts_are_visited_before_already_known_ones():
    def post(pid, url=None):
        return parser.Post(pid, PAGE_URL, "t", None, None, url=url)

    pending = [
        (post("known1"), True),
        (post("new1", "https://www.facebook.com/P/posts/new1"), False),
        (post("known2"), True),
        (post("new2"), False),
    ]
    targets = pipeline._comment_targets(pending, PAGE_URL, 3)
    assert [pid for pid, _ in targets] == ["new1", "new2", "known1"]
    assert targets[0][1] == "https://www.facebook.com/P/posts/new1"


def test_a_per_post_failure_is_a_diagnostic_not_a_failed_run(tmp_path):
    config = make_config(tmp_path)
    summary = run(
        config,
        fake_capture([page_html("777001")]),
        fake_comments(error="goto failed: timeout"),
    )
    assert summary.status == "completed"
    assert summary.comments_captured == 0
    assert any("goto failed" in d for d in summary.diagnostics)
    # The post itself survived the comment failure.
    conn = db.connect(config.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_wall_during_the_comment_pass_keeps_the_posts(tmp_path):
    config = make_config(tmp_path)
    summary = run(
        config, fake_capture([page_html("777001")]), fake_comments(blocked=True)
    )
    assert summary.blocked == wall.RATE_LIMITED
    assert summary.status == "failed"
    conn = db.connect(config.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
    finally:
        conn.close()


def test_an_unexpected_error_in_the_pass_does_not_fail_the_run(tmp_path):
    config = make_config(tmp_path)

    def exploding(targets, conn, run_id, config, options=None, should_stop=None):
        raise RuntimeError("playwright exploded")
        yield  # pragma: no cover - generator marker

    summary = run(config, fake_capture([page_html("777001")]), exploding)
    assert summary.status == "completed"
    assert any("playwright exploded" in d for d in summary.diagnostics)


def test_comments_survive_a_second_run_without_duplicating(tmp_path):
    config = make_config(tmp_path)
    for _ in range(2):
        run(
            config,
            fake_capture([page_html("777001")]),
            fake_comments(FIXTURE.read_bytes()),
        )
    rows = stored_comments(config.db_path)
    assert len(rows) == 3
    assert len({r[1] for r in rows}) == 3


# -- the `crawler comments` read command ------------------------------------


def test_comments_command_lists_stored_comments(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from crawler_social import cli, config as config_module

    db_path = tmp_path / "social.db"
    conn = db.connect(db_path)
    try:
        with db.transaction(conn):
            db.upsert_post(conn, "p1", PAGE_URL, "t", None, None)
            db.upsert_post(conn, "p2", PAGE_URL, "t", None, None)
            db.upsert_comment(conn, "c1", "p1", "u", "Ann", "hello", None, 4, 1)
            db.upsert_comment(conn, "c2", "p2", "u", "Bob", "elsewhere", None, 0, 1)
    finally:
        conn.close()

    monkeypatch.setattr(
        cli, "load_config", lambda: make_config(tmp_path), raising=False
    )
    result = CliRunner().invoke(cli.app, ["comments", "--post-id", "p1"])
    assert result.exit_code == 0, result.output
    assert "Ann" in result.output and "hello" in result.output
    assert "elsewhere" not in result.output


def test_comments_command_without_a_database_says_so(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from crawler_social import cli

    monkeypatch.setattr(
        cli, "load_config", lambda: make_config(tmp_path), raising=False
    )
    result = CliRunner().invoke(cli.app, ["comments"])
    assert result.exit_code == 0
    assert "No database yet" in result.output

