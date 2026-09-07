"""Database access for crawler-social.

All timestamps are explicit UTC ISO-8601 strings. Snapshot records commit
immediately in their own transaction; post upserts and state updates commit
together in a second transaction so state never advances past committed posts.
A snapshot row is capture metadata only: the page markup is never stored,
because the text parsed out of it is what this project is for.
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
    _migrate_columns(conn)
    dropped = _drop_snapshot_html(conn)
    conn.commit()
    if dropped:
        # VACUUM reclaims the dropped column's pages; it cannot run in a
        # transaction, so it runs after the commit above.
        conn.execute("VACUUM")
    return conn


#: CREATE TABLE IF NOT EXISTS does nothing to an existing table, so a column
#: added later needs an ALTER. Additive only, so it is safe on every connect.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("posts", "post_url", "ALTER TABLE posts ADD COLUMN post_url TEXT"),
    (
        "snapshots",
        "size_bytes",
        "ALTER TABLE snapshots ADD COLUMN size_bytes INTEGER",
    ),
)


def _snapshot_columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(snapshots)")]


def _drop_snapshot_html(conn: sqlite3.Connection) -> bool:
    """Remove the legacy `html` column, keeping every snapshot row.

    Databases written while markup was still stored carry megabytes per
    capture in a column nothing reads any more. Rebuilding the table
    without it -- rather than DROP COLUMN -- copies only the metadata, so
    the blobs are never rewritten on the way out. Returns True when the
    rebuild ran, which tells connect() to VACUUM.
    """
    columns = _snapshot_columns(conn)
    if "html" not in columns:
        return False
    size = "COALESCE(size_bytes, length(html))" if "size_bytes" in columns else "length(html)"
    # Off for the rebuild: it drops a table another table's keys point at.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with transaction(conn):
            conn.execute(
                """
                CREATE TABLE snapshots_new (
                    id           INTEGER PRIMARY KEY,
                    run_id       INTEGER NOT NULL REFERENCES runs(id),
                    page_url     TEXT NOT NULL,
                    captured_at  TEXT NOT NULL,
                    sha256       TEXT NOT NULL UNIQUE,
                    size_bytes   INTEGER
                )
                """
            )
            conn.execute(
                "INSERT INTO snapshots_new"
                " (id, run_id, page_url, captured_at, sha256, size_bytes)"
                f" SELECT id, run_id, page_url, captured_at, sha256, {size}"
                " FROM snapshots"
            )
            conn.execute("DROP TABLE snapshots")
            conn.execute("ALTER TABLE snapshots_new RENAME TO snapshots")
            # The old table's indexes went with it.
            conn.executescript(SCHEMA_SQL)
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    return True


def _migrate_columns(conn: sqlite3.Connection) -> list[str]:
    """Add any column schema.py declares that this file does not have yet."""
    applied: list[str] = []
    for table, column, statement in _ADDED_COLUMNS:
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if not existing or column in existing:
            continue
        conn.execute(statement)
        applied.append(f"{table}.{column}")
    if "snapshots.size_bytes" in applied and "html" in _snapshot_columns(conn):
        # The size lives only in the blob about to be dropped; copy it first.
        conn.execute(
            "UPDATE snapshots SET size_bytes = length(html)"
            " WHERE size_bytes IS NULL"
        )
    return applied


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
    """Commit one snapshot record immediately. Returns True when recorded.

    The row is capture metadata: run, page, time, sha256, and how many
    bytes came back. `html` is read, never written -- it is hashed and
    measured, then the caller parses it in memory and only the text it
    yields is stored. A second capture of an unchanged page hashes the
    same and is dropped here, which is what makes repeat runs cheap.
    """
    import hashlib

    sha = hashlib.sha256(html).hexdigest()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO snapshots"
                " (run_id, page_url, captured_at, sha256, size_bytes)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, page_url, captured_at, sha, len(html)),
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
    post_url: str | None = None,
) -> None:
    seen = seen_at or utc_now_iso()
    conn.execute(
        """
        INSERT INTO posts (post_id, page_url, text, author, published_at, post_url, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
            text = COALESCE(excluded.text, posts.text),
            author = COALESCE(excluded.author, posts.author),
            published_at = COALESCE(excluded.published_at, posts.published_at),
            post_url = COALESCE(excluded.post_url, posts.post_url),
            last_seen = excluded.last_seen
        """,
        (post_id, page_url, text, author, published_at, post_url, seen, seen),
    )


def upsert_comment(
    conn: sqlite3.Connection,
    comment_id: str,
    post_id: str,
    page_url: str,
    author: str | None,
    text: str | None,
    published_at: str | None,
    like_count: int | None,
    rank_index: int,
    seen_at: str | None = None,
) -> None:
    """Store one comment, keeping the best values seen so far.

    The nullable fields are COALESCEd like a post's, so a later, poorer
    parse never erases a better one. rank_index is overwritten on purpose:
    the newest observed position in Facebook's ordering is the useful one.
    """
    seen = seen_at or utc_now_iso()
    conn.execute(
        """
        INSERT INTO comments (comment_id, post_id, page_url, author, text,
                              published_at, like_count, rank_index,
                              first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(comment_id) DO UPDATE SET
            author = COALESCE(excluded.author, comments.author),
            text = COALESCE(excluded.text, comments.text),
            published_at = COALESCE(excluded.published_at, comments.published_at),
            like_count = COALESCE(excluded.like_count, comments.like_count),
            rank_index = excluded.rank_index,
            last_seen = excluded.last_seen
        """,
        (
            comment_id,
            post_id,
            page_url,
            author,
            text,
            published_at,
            like_count,
            rank_index,
            seen,
            seen,
        ),
    )


def comment_count(conn: sqlite3.Connection, post_id: str | None = None) -> int:
    if post_id is None:
        row = conn.execute("SELECT COUNT(*) FROM comments").fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE post_id = ?", (post_id,)
        ).fetchone()
    return int(row[0])


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


def list_comments(
    conn: sqlite3.Connection,
    post_id: str | None = None,
    limit: int = 20,
) -> list[tuple]:
    """List comments in Facebook's own order (rank_index), newest post first."""
    sql = (
        "SELECT comment_id, post_id, page_url, author, text, published_at,"
        " like_count, rank_index, first_seen, last_seen FROM comments"
    )
    params: list[object] = []
    if post_id is not None:
        sql += " WHERE post_id = ?"
        params.append(post_id)
    sql += " ORDER BY first_seen DESC, post_id ASC, rank_index ASC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()
