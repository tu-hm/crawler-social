"""Crawl pipeline: capture snapshots, parse, commit posts and state atomically.

Rules enforced here:
- raw HTML commits before parsing touches it;
- parsed posts and state commit together in one transaction;
- state never advances past committed posts;
- --limit is a hard ceiling on newly emitted posts.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import db, facebook
from .config import Config
from .lock import LockBusyError
from .parser import parse, parse_comments
from .paths import default_db_path

CONSECUTIVE_KNOWN_STOP = 5


@dataclass
class RunSummary:
    run_id: int
    status: str
    snapshots_captured: int
    snapshots_total: int
    new_posts: int
    existing_posts: int
    errors: int
    diagnostics: list[str]
    #: Set when Facebook served a wall instead of content: the verdict kind
    #: ("login_wall", "checkpoint", "rate_limited", "unavailable").
    blocked: Optional[str] = None
    blocked_message: Optional[str] = None
    #: Comment pass (v3). Both zero when the pass did not run.
    comments_captured: int = 0
    posts_with_comments: int = 0


class _StopRequest:
    """Signal-aware stop flag: first signal stops gracefully."""

    def __init__(self) -> None:
        self.requested = False
        self.signal_count = 0
        self._previous: dict[int, object] = {}

    def install(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._previous[sig] = signal.signal(sig, self._handle)

    def restore(self) -> None:
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]
        self._previous.clear()

    def _handle(self, signum, frame) -> None:
        self.signal_count += 1
        if self.signal_count == 1:
            self.requested = True
        else:
            raise KeyboardInterrupt


def _comment_targets(
    pending_posts: list[tuple],
    page_url: str,
    max_posts: int,
) -> list[tuple[str, str]]:
    """New posts first, then known ones, capped at `max_posts`.

    A post whose article carried no usable permalink falls back to the
    constructed `/posts/<id>` form.
    """
    ordered = [p for p, known in pending_posts if not known]
    ordered += [p for p, known in pending_posts if known]
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for post in ordered:
        if post.post_id in seen:
            continue
        seen.add(post.post_id)
        targets.append(
            (post.post_id, post.url or facebook.post_permalink(page_url, post.post_id))
        )
        if len(targets) >= max_posts:
            break
    return targets


def _collect_comments(
    conn,
    run_id: int,
    config: Config,
    summary: RunSummary,
    pending_posts: list[tuple],
    page_url: str,
    options,
    should_stop,
) -> Optional[facebook.BlockedError]:
    """Run the permalink pass and commit one transaction per post.

    Returns the BlockedError when a wall ended the pass, else None. Every
    other failure becomes a diagnostic: the posts are already stored, and a
    run that captured them is not a failed run just because a comment
    thread would not load.
    """
    targets = _comment_targets(pending_posts, page_url, options.max_posts)
    if not targets:
        return None
    try:
        for capture in facebook.capture_comments(
            targets,
            conn,
            run_id,
            config,
            options=options,
            should_stop=should_stop,
        ):
            if capture.error or capture.html is None:
                summary.diagnostics.append(
                    f"comments: {capture.post_id}: {capture.error or 'no html'}"
                )
                summary.errors += 1
                continue
            comments, diagnostics = parse_comments(
                capture.html,
                capture.post_id,
                datetime.now(timezone.utc),
                limit=options.top_n,
            )
            for diag in diagnostics:
                summary.diagnostics.append(f"{diag.reason}: {diag.context}")
            summary.errors += len(diagnostics)
            if not comments:
                continue
            # One transaction per post: a post's comments land whole or not
            # at all, and a later post's failure cannot undo an earlier one.
            with db.transaction(conn):
                for comment in comments:
                    db.upsert_comment(
                        conn,
                        comment.comment_id,
                        capture.post_id,
                        capture.post_url,
                        comment.author,
                        comment.text,
                        comment.published_at,
                        comment.like_count,
                        comment.rank,
                    )
            summary.comments_captured += len(comments)
            summary.posts_with_comments += 1
    except facebook.BlockedError as exc:
        summary.diagnostics.append(f"comments blocked: {exc.verdict.reason}")
        return exc
    except Exception as exc:  # noqa: BLE001 - the posts are already committed
        summary.diagnostics.append(f"comments: {type(exc).__name__}: {exc}")
        summary.errors += 1
    return None


def run_crawl(
    page_url: str,
    limit: int,
    config: Config,
    *,
    top_comments: Optional[int] = None,
    comments_max_posts: Optional[int] = None,
    expand_text: Optional[bool] = None,
) -> RunSummary:
    """Crawl `page_url`; the keyword options fall back to `config` when None."""
    top_comments = (
        config.top_comments if top_comments is None else max(0, top_comments)
    )
    comments_max_posts = (
        config.comments_max_posts
        if comments_max_posts is None
        else max(1, comments_max_posts)
    )
    capture_options = facebook.CaptureOptions(
        expand_text=config.expand_text if expand_text is None else expand_text
    )
    stop = _StopRequest()
    conn = db.connect(config.db_path)
    run_id = db.start_run(conn)
    summary = RunSummary(
        run_id=run_id,
        status="completed",
        snapshots_captured=0,
        snapshots_total=db.snapshot_count(conn, page_url),
        new_posts=0,
        existing_posts=0,
        errors=0,
        diagnostics=[],
    )
    status = "completed"
    error_text: Optional[str] = None

    try:
        stop.install()
        state = db.get_state(conn, page_url)
        pending_posts: list[tuple] = []
        seen_in_run: set[str] = set()
        consecutive_known = 0
        limit_reached = False
        first_snapshot: Optional[tuple[str, bytes]] = None
        blocked: Optional[facebook.BlockedError] = None

        # A wall stops capture but must not discard posts already parsed from
        # earlier, legitimate snapshots -- so it is caught around the loop and
        # the commit below still runs.
        try:
            for captured_at, html in facebook.capture_snapshots(
                page_url,
                conn,
                run_id,
                config,
                options=capture_options,
                should_stop=lambda: stop.requested,
            ):
                summary.snapshots_captured += 1
                summary.snapshots_total += 1
                if first_snapshot is None:
                    first_snapshot = (captured_at, html)
                    facebook.save_fixture(
                        html,
                        page_url,
                        captured_at,
                        "chrome",
                        config.db_path.parent / "fixtures",
                    )

                posts, diagnostics = parse(
                    html,
                    page_url,
                    datetime.now(timezone.utc),
                )
                for diag in diagnostics:
                    summary.diagnostics.append(f"{diag.reason}: {diag.context}")
                summary.errors += len(diagnostics)

                for post in posts:
                    if post.post_id in seen_in_run:
                        continue
                    seen_in_run.add(post.post_id)
                    known = db.has_post(conn, post.post_id)
                    pinned = (post.text or "").lower().startswith("pinned")
                    if known:
                        summary.existing_posts += 1
                        if not pinned:
                            consecutive_known += 1
                    else:
                        summary.new_posts += 1
                        consecutive_known = 0
                    pending_posts.append((post, known))
                    if summary.new_posts >= limit:
                        limit_reached = True
                        break
                    if consecutive_known >= CONSECUTIVE_KNOWN_STOP:
                        break
                if limit_reached or consecutive_known >= CONSECUTIVE_KNOWN_STOP:
                    break
                if stop.requested:
                    status = "interrupted"
                    break
        except facebook.BlockedError as exc:
            blocked = exc
            summary.blocked = exc.verdict.kind
            summary.blocked_message = exc.verdict.message
            summary.diagnostics.append(
                f"{exc.verdict.kind}: {exc.verdict.reason}"
            )

        if stop.requested and status != "interrupted":
            status = "interrupted"

        # Commit parsed posts and state together; on failure neither advances.
        with db.post_transaction(conn):
            for post, _known in pending_posts:
                db.upsert_post(
                    conn,
                    post.post_id,
                    post.page_url,
                    post.text,
                    post.author,
                    post.published_at,
                    post_url=post.url,
                )
            # A blocked run saw an incomplete feed, so its newest post is not
            # a trustworthy watermark. Store the posts, hold the state.
            if pending_posts and blocked is None:
                newest = pending_posts[0][0]
                db.set_state(conn, page_url, newest.post_id, newest.published_at)

        # Comments come after the post transaction, so a comment failure can
        # never cost posts that are already safely committed (D6).
        if blocked is None and top_comments > 0 and not stop.requested:
            comment_blocked = _collect_comments(
                conn,
                run_id,
                config,
                summary,
                pending_posts,
                page_url,
                facebook.CommentOptions(
                    top_n=top_comments, max_posts=comments_max_posts
                ),
                should_stop=lambda: stop.requested,
            )
            if comment_blocked is not None:
                blocked = comment_blocked
                summary.blocked = comment_blocked.verdict.kind
                summary.blocked_message = comment_blocked.verdict.message

        if blocked is not None:
            status = "failed"
            error_text = f"blocked:{blocked.verdict.kind}: {blocked}"
            summary.errors += 1
    except KeyboardInterrupt:
        status = "interrupted"
        error_text = "interrupted by signal"
    except LockBusyError as exc:
        status = "failed"
        error_text = str(exc)
        summary.errors += 1
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error_text = f"{type(exc).__name__}: {exc}"
        summary.errors += 1
    finally:
        stop.restore()
        db.finish_run(conn, run_id, status, error_text)
        conn.close()

    summary.status = status
    if status == "failed" and summary.blocked is None:
        raise facebook.CaptureError(error_text or "crawl failed")
    return summary
