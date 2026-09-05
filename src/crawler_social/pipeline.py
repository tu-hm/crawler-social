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
from .parser import parse
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


def run_crawl(page_url: str, limit: int, config: Config) -> RunSummary:
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

        for captured_at, html in facebook.capture_snapshots(
            page_url,
            conn,
            run_id,
            config,
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
                pending_posts.append(
                    (post, known)
                )
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
                )
            if pending_posts:
                newest = pending_posts[0][0]
                db.set_state(conn, page_url, newest.post_id, newest.published_at)
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
    if status == "failed":
        raise facebook.CaptureError(error_text or "crawl failed")
    return summary
