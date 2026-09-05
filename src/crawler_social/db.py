"""Database access for crawler-social.

All timestamps are explicit UTC ISO-8601 strings. Raw snapshots commit
immediately in their own transaction; post upserts and state updates commit
together in a second transaction so state never advances past committed posts.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .schema import SCHEMA_SQL


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    """Open the database with the project's standard pragmas."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction: all-or-nothing commit."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def start_run(conn: sqlite3.Connection, started_at: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, status) VALUES (?, 'running')",
        (started_at or utc_now_iso(),),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    error: str | None = None,
    finished_at: str | None = None,
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, error = ? WHERE id = ?",
        (finished_at or utc_now_iso(), status, error, run_id),
    )
    conn.commit()


def save_snapshot(
    conn: sqlite3.Connection,
    run_id: int,
    page_url: str,
    captured_at: str,
    html: bytes,
) -> bool:
    """Commit one raw snapshot immediately. Returns True when stored."""
    import hashlib

    sha = hashlib.sha256(html).hexdigest()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO snapshots (run_id, page_url, captured_at, sha256, html)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, page_url, captured_at, sha, sqlite3.Binary(html)),
            )
    except sqlite3.IntegrityError:
        return False
    return True


@contextmanager
def post_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transaction that commits post upserts and state together."""
    with transaction(conn) as tx:
        yield tx


def upsert_post(
    conn: sqlite3.Connection,
    post_id: str,
    page_url: str,
    text: str | None,
    author: str | None,
    published_at: str | None,
    seen_at: str | None = None,
) -> None:
    seen = seen_at or utc_now_iso()
    conn.execute(
        """
        INSERT INTO posts (post_id, page_url, text, author, published_at, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
            text = COALESCE(excluded.text, posts.text),
            author = COALESCE(excluded.author, posts.author),
            published_at = COALESCE(excluded.published_at, posts.published_at),
            last_seen = excluded.last_seen
        """,
        (post_id, page_url, text, author, published_at, seen, seen),
    )


def get_state(conn: sqlite3.Connection, page_url: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT page_url, last_post_id, last_post_time, updated_at"
        " FROM state WHERE page_url = ?",
        (page_url,),
    ).fetchone()
    if row is None:
        return None
    return {
        "page_url": row[0],
        "last_post_id": row[1],
        "last_post_time": row[2],
        "updated_at": row[3],
    }


def set_state(
    conn: sqlite3.Connection,
    page_url: str,
    last_post_id: str | None,
    last_post_time: str | None,
    updated_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO state (page_url, last_post_id, last_post_time, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(page_url) DO UPDATE SET
            last_post_id = excluded.last_post_id,
            last_post_time = excluded.last_post_time,
            updated_at = excluded.updated_at
        """,
        (page_url, last_post_id, last_post_time, updated_at or utc_now_iso()),
    )


def has_post(conn: sqlite3.Connection, post_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM posts WHERE post_id = ?", (post_id,)
    ).fetchone()
    return row is not None


def snapshot_count(conn: sqlite3.Connection, page_url: str | None = None) -> int:
    if page_url is None:
        row = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE page_url = ?", (page_url,)
        ).fetchone()
    return int(row[0])


def list_posts(
    conn: sqlite3.Connection,
    limit: int = 10,
    contains: str | None = None,
) -> list[tuple]:
    """List posts newest first with a parameterized optional text filter."""
    sql = (
        "SELECT post_id, page_url, text, author, published_at, first_seen, last_seen"
        " FROM posts"
    )
    params: list[object] = []
    if contains is not None:
        sql += " WHERE text LIKE ? ESCAPE '\\'"
        escaped = contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    sql += " ORDER BY COALESCE(published_at, last_seen) DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()
