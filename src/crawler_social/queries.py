"""Read-only query layer for the v2 web viewer.

The server must never be able to write to the database, so this module is
the only door it uses: every connection is opened read-only through a URI
(`file:...?mode=ro`), and no function here runs anything but SELECT.
`db.connect()` -- which executes the schema -- is for the crawler only.

All timestamps are the UTC ISO-8601 strings the crawler stores; they are
passed through unchanged and compared lexicographically, which is correct
for the single normalized format the pipeline writes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

#: Clamped here too, so the HTTP layer is not the only bound on user input.
MAX_PAGE_SIZE = 200

#: Mirrors the runs.status CHECK constraint in schema.py.
RUN_STATUSES = frozenset({"running", "completed", "failed", "interrupted"})

_BASE_POST_COLUMNS = (
    "post_id, page_url, text, author, published_at, first_seen, last_seen"
)

_COMMENT_COLUMNS = (
    "comment_id, post_id, page_url, author, text, published_at, "
    "like_count, rank_index, first_seen, last_seen"
)


class DatabaseMissingError(RuntimeError):
    """The database file the viewer was pointed at does not exist."""


def connect_ro(path: Path) -> sqlite3.Connection:
    """Open the database strictly read-only, or fail with a clear error."""
    path = Path(path)
    if not path.exists():
        raise DatabaseMissingError(
            f"No database at {path}. Run `crawler crawl` first."
        )
    from urllib.parse import quote

    conn = sqlite3.connect(
        f"file:{quote(str(path))}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    # Defense in depth on top of mode=ro: a stray write fails, never lands.
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _post_columns(conn: sqlite3.Connection) -> str:
    """Post columns this database actually has.

    `posts.post_url` and the `comments` table arrived in v3, and the viewer
    opens the file read-only -- it cannot run the migration itself. A
    database written by an older crawler is read without the newer parts
    instead of failing, and picks them up the next time a crawl runs.
    """
    if "post_url" in _table_columns(conn, "posts"):
        return _BASE_POST_COLUMNS + ", post_url"
    return _BASE_POST_COLUMNS


def _has_comments(conn: sqlite3.Connection) -> bool:
    return bool(_table_columns(conn, "comments"))


def _snapshot_size(conn: sqlite3.Connection) -> str:
    """SQL for a snapshot's captured size, in bytes.

    Current databases record it in `size_bytes`. One written before that
    column existed only has the markup it used to store, and the read-only
    viewer never migrates -- so measure the old blob where that is all
    there is.
    """
    columns = _table_columns(conn, "snapshots")
    if "size_bytes" not in columns:
        return "length(html)"
    if "html" in columns:
        return "COALESCE(size_bytes, length(html))"
    return "size_bytes"


def _clamp(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), MAX_PAGE_SIZE)), max(0, int(offset))


def _like_escape(value: str) -> str:
    """Escape LIKE wildcards the same way db.list_posts does."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


def list_posts(
    conn: sqlite3.Connection,
    *,
    limit: int,
    offset: int = 0,
    contains: Optional[str] = None,
    page_url: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    order: str = "newest",
) -> tuple[list[dict], int]:
    """Filtered, paged posts with the total count behind the filter.

    Ordering matches db.list_posts: COALESCE(published_at, last_seen),
    newest first unless order="oldest".
    """
    where: list[str] = []
    params: list[object] = []
    if contains is not None:
        where.append("text LIKE ? ESCAPE '\\'")
        params.append(f"%{_like_escape(contains)}%")
    if page_url is not None:
        where.append("page_url = ?")
        params.append(page_url)
    if since is not None:
        where.append("COALESCE(published_at, last_seen) >= ?")
        params.append(since)
    if until is not None:
        where.append("COALESCE(published_at, last_seen) <= ?")
        params.append(until)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""

    total = int(
        conn.execute(f"SELECT COUNT(*) FROM posts{where_sql}", params).fetchone()[0]
    )
    direction = "ASC" if order == "oldest" else "DESC"
    # post_id makes the sort total: without it, LIMIT/OFFSET paging over equal
    # timestamps can repeat or skip rows and disagree with post_neighbors.
    rows = conn.execute(
        f"SELECT {_post_columns(conn)} FROM posts{where_sql} "
        f"ORDER BY COALESCE(published_at, last_seen) {direction}, "
        f"post_id {direction} "
        f"LIMIT ? OFFSET ?",
        [*params, *_clamp(limit, offset)],
    ).fetchall()
    return _rows_to_dicts(rows), total


def get_post(conn: sqlite3.Connection, post_id: str) -> Optional[dict]:
    row = conn.execute(
        f"SELECT {_post_columns(conn)} FROM posts WHERE post_id = ?", (post_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_comments(
    conn: sqlite3.Connection,
    *,
    post_id: str,
    limit: int,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """One post's stored comments in Facebook's own order, top first."""
    if not _has_comments(conn):
        return [], 0
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM comments WHERE post_id = ?", (post_id,)
        ).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT {_COMMENT_COLUMNS} FROM comments WHERE post_id = ?"
        " ORDER BY rank_index ASC, comment_id ASC LIMIT ? OFFSET ?",
        (post_id, *_clamp(limit, offset)),
    ).fetchall()
    return _rows_to_dicts(rows), total


def comment_counts(
    conn: sqlite3.Connection, post_ids: list[str]
) -> dict[str, int]:
    """Comment counts for a batch of posts -- one query, not N."""
    if not post_ids or not _has_comments(conn):
        return {}
    placeholders = ", ".join("?" * len(post_ids))
    rows = conn.execute(
        f"SELECT post_id, COUNT(*) FROM comments WHERE post_id IN ({placeholders})"
        " GROUP BY post_id",
        post_ids,
    ).fetchall()
    return {row[0]: int(row[1]) for row in rows}


def list_pages(conn: sqlite3.Connection) -> list[dict]:
    """Distinct page URLs with post count and newest post time."""
    rows = conn.execute(
        "SELECT page_url, COUNT(*) AS post_count, "
        "MAX(COALESCE(published_at, last_seen)) AS newest_post_at "
        "FROM posts GROUP BY page_url ORDER BY page_url"
    ).fetchall()
    return _rows_to_dicts(rows)


def _run_columns() -> str:
    return (
        "r.id, r.started_at, r.finished_at, r.status, r.error, "
        "(SELECT COUNT(*) FROM snapshots s WHERE s.run_id = r.id) AS snapshot_count"
    )


def list_runs(
    conn: sqlite3.Connection, *, limit: int, offset: int = 0
) -> tuple[list[dict], int]:
    total = int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
    rows = conn.execute(
        f"SELECT {_run_columns()} FROM runs r ORDER BY r.id DESC LIMIT ? OFFSET ?",
        _clamp(limit, offset),
    ).fetchall()
    return _rows_to_dicts(rows), total


def get_run(conn: sqlite3.Connection, run_id: int) -> Optional[dict]:
    row = conn.execute(
        f"SELECT {_run_columns()} FROM runs r WHERE r.id = ?", (run_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_snapshots(
    conn: sqlite3.Connection,
    *,
    run_id: Optional[int] = None,
    page_url: Optional[str] = None,
    limit: int,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Snapshots for a list view: metadata is all a snapshot has."""
    where: list[str] = []
    params: list[object] = []
    if run_id is not None:
        where.append("run_id = ?")
        params.append(run_id)
    if page_url is not None:
        where.append("page_url = ?")
        params.append(page_url)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""

    total = int(
        conn.execute(
            f"SELECT COUNT(*) FROM snapshots{where_sql}", params
        ).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT id, run_id, page_url, captured_at, sha256,"
        f" {_snapshot_size(conn)} AS size_bytes"
        f" FROM snapshots{where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        [*params, *_clamp(limit, offset)],
    ).fetchall()
    return _rows_to_dicts(rows), total


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> Optional[dict]:
    """Metadata for one snapshot -- what a capture leaves behind."""
    row = conn.execute(
        f"SELECT id, run_id, page_url, captured_at, sha256,"
        f" {_snapshot_size(conn)} AS size_bytes FROM snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def post_neighbors(
    conn: sqlite3.Connection, post: dict, order: str = "newest"
) -> tuple[Optional[dict], Optional[dict]]:
    """Adjacent posts in the posts-view sort order: (previous, next).

    The sort key is COALESCE(published_at, last_seen), matching
    list_posts; post_id breaks ties in the same direction as the key so
    equal timestamps still walk forward and back.
    """
    key = post.get("published_at") or post.get("last_seen")
    post_id = post["post_id"]
    # "newest" sorts the key descending, so the next row has a smaller key.
    next_op, next_dir = ("<", "DESC") if order == "newest" else (">", "ASC")
    prev_op, prev_dir = (">", "ASC") if order == "newest" else ("<", "DESC")

    def neighbor(op: str, direction: str) -> Optional[dict]:
        row = conn.execute(
            f"SELECT {_post_columns(conn)} FROM posts"
            f" WHERE COALESCE(published_at, last_seen) {op} ?"
            f"    OR (COALESCE(published_at, last_seen) = ? AND post_id {op} ?)"
            f" ORDER BY COALESCE(published_at, last_seen) {direction},"
            f" post_id {direction} LIMIT 1",
            (key, key, post_id),
        ).fetchone()
        return dict(row) if row is not None else None

    return neighbor(prev_op, prev_dir), neighbor(next_op, next_dir)


def snapshots_near_post(
    conn: sqlite3.Connection, post: dict, limit: int = 5
) -> list[dict]:
    """Snapshots of the same page, closest in capture time to the post."""
    reference = post.get("published_at") or post.get("last_seen")
    rows = conn.execute(
        f"SELECT id, run_id, page_url, captured_at,"
        f" {_snapshot_size(conn)} AS size_bytes"
        " FROM snapshots WHERE page_url = ?"
        " ORDER BY ABS(julianday(captured_at) - julianday(?)) ASC, id DESC"
        " LIMIT ?",
        (post["page_url"], reference, _clamp(limit, 0)[0]),
    ).fetchall()
    return _rows_to_dicts(rows)


def count_posts_since(conn: sqlite3.Connection, cutoff: str) -> int:
    """Posts first seen at/after the cutoff -- the health-panel counts."""
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM posts WHERE first_seen >= ?", (cutoff,)
        ).fetchone()[0]
    )


def posts_per_day(
    conn: sqlite3.Connection, *, since: str
) -> list[dict]:
    """Posts grouped by day of first_seen, ascending. Chart data only."""
    rows = conn.execute(
        "SELECT date(first_seen) AS day, COUNT(*) AS count FROM posts"
        " WHERE first_seen >= ? GROUP BY day ORDER BY day",
        (since,),
    ).fetchall()
    return _rows_to_dicts(rows)


def posts_first_seen_between(
    conn: sqlite3.Connection, *, started_at: str, finished_at: str, limit: int
) -> tuple[list[dict], int]:
    """Posts first seen during a run's window -- its approximate yield.

    posts has no run foreign key, so this is correlation by time, not
    attribution; the UI must label it approximate.
    """
    where = "first_seen >= ? AND first_seen <= ?"
    params: list[object] = [started_at, finished_at]
    total = int(
        conn.execute(f"SELECT COUNT(*) FROM posts WHERE {where}", params).fetchone()[0]
    )
    rows = conn.execute(
        f"SELECT {_post_columns(conn)} FROM posts WHERE {where}"
        f" ORDER BY first_seen DESC LIMIT ?",
        [*params, _clamp(limit, 0)[0]],
    ).fetchall()
    return _rows_to_dicts(rows), total


def get_state(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT page_url, last_post_id, last_post_time, updated_at"
        " FROM state ORDER BY page_url"
    ).fetchall()
    return _rows_to_dicts(rows)


def summary(conn: sqlite3.Connection) -> dict:
    """Dashboard counts. Zeros on an empty database, never an error."""
    posts, snapshots, runs = (
        int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("posts", "snapshots", "runs")
    )
    last_run = conn.execute(
        "SELECT id, started_at, finished_at, status, error"
        " FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    newest_post = conn.execute(
        "SELECT MAX(COALESCE(published_at, last_seen)) FROM posts"
    ).fetchone()[0]
    snapshot_bytes = int(
        conn.execute(
            f"SELECT COALESCE(SUM({_snapshot_size(conn)}), 0) FROM snapshots"
        ).fetchone()[0]
    )
    total_comments = (
        int(conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0])
        if _has_comments(conn)
        else 0
    )
    return {
        "total_posts": posts,
        "total_snapshots": snapshots,
        "total_runs": runs,
        "total_comments": total_comments,
        "last_run": dict(last_run) if last_run is not None else None,
        "newest_post_at": newest_post,
        "total_snapshot_bytes": snapshot_bytes,
    }
