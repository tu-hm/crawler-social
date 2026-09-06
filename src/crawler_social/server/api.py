"""JSON API for the local viewer, mounted at /api.

Every endpoint delegates to `queries.py` and adds no SQL of its own.
Response models are declared once here so the shapes stay honest. All
stored timestamps are UTC ISO-8601 strings and are passed through
unchanged -- no datetime objects anywhere in a response.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from typing import Annotated, Generic, Literal, Optional, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import AfterValidator, BaseModel

from .. import queries
from .app import get_conn
from .params import normalize_when

router = APIRouter(prefix="/api")

Conn = Annotated[sqlite3.Connection, Depends(get_conn)]

T = TypeVar("T")


class PostOut(BaseModel):
    post_id: str
    page_url: str
    text: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[str] = None
    first_seen: str
    last_seen: str
    #: Absent from rows written before the v3 migration.
    post_url: Optional[str] = None


class CommentOut(BaseModel):
    comment_id: str
    post_id: str
    page_url: str
    author: Optional[str] = None
    text: Optional[str] = None
    published_at: Optional[str] = None
    like_count: Optional[int] = None
    rank_index: int
    first_seen: str
    last_seen: str


class RunInfo(BaseModel):
    id: int
    started_at: str
    finished_at: Optional[str] = None
    status: str
    error: Optional[str] = None


class RunOut(RunInfo):
    snapshot_count: int


class SnapshotOut(BaseModel):
    id: int
    run_id: int
    page_url: str
    captured_at: str
    sha256: str
    size_bytes: int


class RunDetailOut(RunInfo):
    snapshots: list[SnapshotOut]


class PageInfoOut(BaseModel):
    page_url: str
    post_count: int
    newest_post_at: Optional[str] = None


class StateOut(BaseModel):
    page_url: str
    last_post_id: Optional[str] = None
    last_post_time: Optional[str] = None
    updated_at: str


class SummaryOut(BaseModel):
    total_posts: int
    total_snapshots: int
    total_runs: int
    total_comments: int = 0
    last_run: Optional[RunInfo] = None
    newest_post_at: Optional[str] = None
    total_snapshot_bytes: int


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


SinceQuery = Annotated[
    Optional[str],
    Query(description="ISO-8601 date or datetime; posts at/after this time."),
    AfterValidator(
        lambda v: normalize_when(v, end_of_day=False) if v else v
    ),
]
UntilQuery = Annotated[
    Optional[str],
    Query(description="ISO-8601 date or datetime; posts at/before this time."),
    AfterValidator(lambda v: normalize_when(v, end_of_day=True) if v else v),
]

POST_CSV_COLUMNS = (
    "post_id", "page_url", "text", "author", "published_at",
    "first_seen", "last_seen",
)

#: Leading characters a spreadsheet reads as the start of a formula. Post
#: text and author names come from a third party, so a cell beginning with
#: one of these is prefixed with an apostrophe before it is written -- the
#: sheet then shows the original text instead of evaluating it. "-" is in
#: the set because the DDE vector starts with one; the cost is an
#: apostrophe in front of a line that opened with a dash.
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: object) -> object:
    """Neutralise a value that a spreadsheet would treat as a formula."""
    if isinstance(value, str) and value.startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


@router.get("/summary", response_model=SummaryOut)
def summary(conn: Conn) -> SummaryOut:
    return SummaryOut(**queries.summary(conn))


@router.get("/posts", response_model=Page[PostOut])
def list_posts(
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=queries.MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[Optional[str], Query()] = None,
    page_url: Annotated[Optional[str], Query()] = None,
    since: SinceQuery = None,
    until: UntilQuery = None,
    order: Annotated[Literal["newest", "oldest"], Query()] = "newest",
) -> Page[PostOut]:
    rows, total = queries.list_posts(
        conn,
        limit=limit,
        offset=offset,
        contains=q if q else None,
        page_url=page_url,
        since=since,
        until=until,
        order=order,
    )
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/posts/{post_id:path}/comments", response_model=Page[CommentOut])
def list_comments(
    conn: Conn,
    post_id: str,
    limit: Annotated[int, Query(ge=1, le=queries.MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CommentOut]:
    # Registered before /posts/{post_id:path}: the :path converter is greedy
    # and would otherwise swallow the trailing /comments as part of the id.
    rows, total = queries.list_comments(
        conn, post_id=post_id, limit=limit, offset=offset
    )
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/posts/{post_id:path}", response_model=PostOut)
def get_post(conn: Conn, post_id: str) -> PostOut:
    # post_id uses the :path converter: Facebook ids can contain characters
    # that arrive percent-encoded (a slash or a percent sign) and must
    # round-trip through the decoded path.
    post = queries.get_post(conn, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="post_not_found")
    return PostOut(**post)


@router.get("/pages", response_model=list[PageInfoOut])
def list_pages(conn: Conn) -> list[PageInfoOut]:
    return [PageInfoOut(**row) for row in queries.list_pages(conn)]


@router.get("/runs", response_model=Page[RunOut])
def list_runs(
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=queries.MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RunOut]:
    rows, total = queries.list_runs(conn, limit=limit, offset=offset)
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def get_run(conn: Conn, run_id: int) -> RunDetailOut:
    run = queries.get_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    snapshots, _total = queries.list_snapshots(
        conn, run_id=run_id, limit=queries.MAX_PAGE_SIZE
    )
    return RunDetailOut(**run, snapshots=snapshots)


@router.get("/snapshots", response_model=Page[SnapshotOut])
def list_snapshots(
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=queries.MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    run_id: Annotated[Optional[int], Query()] = None,
    page_url: Annotated[Optional[str], Query()] = None,
) -> Page[SnapshotOut]:
    rows, total = queries.list_snapshots(
        conn, run_id=run_id, page_url=page_url, limit=limit, offset=offset
    )
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/snapshots/{snapshot_id}/raw")
def get_snapshot_raw(conn: Conn, snapshot_id: int) -> Response:
    html = queries.get_snapshot_html(conn, snapshot_id)
    if html is None:
        raise HTTPException(status_code=404, detail="snapshot_not_found")
    # Snapshot HTML is untrusted third-party markup. These headers strip it
    # of scripts, images, frames, and network fetches before it is ever
    # rendered (plans/v2/06); the iframe that shows it is sandboxed too.
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )


@router.get("/snapshots/{snapshot_id}/download")
def download_snapshot(conn: Conn, snapshot_id: int) -> Response:
    snapshot = queries.get_snapshot(conn, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="snapshot_not_found")
    html = queries.get_snapshot_html(conn, snapshot_id)
    filename = f"snapshot-{snapshot_id}-{snapshot['sha256'][:12]}.html"
    return Response(
        content=html,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/state", response_model=list[StateOut])
def get_state(conn: Conn) -> list[StateOut]:
    return [StateOut(**row) for row in queries.get_state(conn)]


@router.get("/crawl/status")
def crawl_status() -> dict:
    """What the /crawl status panel polls every two seconds."""
    from .jobs import crawl_job

    return crawl_job.status()


@router.get("/export/posts.csv")
def export_posts_csv(
    conn: Conn,
    q: Annotated[Optional[str], Query()] = None,
    page_url: Annotated[Optional[str], Query()] = None,
    since: SinceQuery = None,
    until: UntilQuery = None,
    order: Annotated[Literal["newest", "oldest"], Query()] = "newest",
) -> StreamingResponse:
    """The current filter's full result set, streamed row by row."""
    filters = {
        "contains": q if q else None,
        "page_url": page_url,
        "since": since,
        "until": until,
        "order": order,
    }

    def rows():
        offset = 0
        while True:
            batch, total = queries.list_posts(
                conn, limit=queries.MAX_PAGE_SIZE, offset=offset, **filters
            )
            if not batch:
                break
            yield from batch
            offset += len(batch)
            if offset >= total:
                break

    def stream():
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(POST_CSV_COLUMNS)
        yield buffer.getvalue()
        for row in rows():
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(
                [csv_cell(row[column]) for column in POST_CSV_COLUMNS]
            )
            yield buffer.getvalue()

    return StreamingResponse(
        stream(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="posts.csv"',
            "Cache-Control": "no-store",
        },
    )
