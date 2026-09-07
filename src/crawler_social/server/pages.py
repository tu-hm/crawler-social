"""Server-rendered HTML pages for the viewer.

Every page shares one parameter vocabulary with the JSON API (q, page_url,
since, until, order, limit, offset) so a UI URL differs from an API URL
only by its path. HTML responses are `Cache-Control: no-store` so a stale
page can never mask new crawl results.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse

from .. import __version__, queries
from ..facebook import check_gui_session
from ..queries import DatabaseMissingError
from .format import duration, highlight, relative_age, run_is_stale, snippet
from .jobs import JobRefused, crawl_job
from .params import normalize_when
from .templating import create_templates, excerpt

templates = create_templates()

PAGE_SIZES = (25, 50, 100, 200)
DEFAULT_PAGE_SIZE = 50
STALE_RUN_AFTER = timedelta(hours=1)
FRESH_WINDOW = timedelta(days=7)
CHART_DAYS = 30
DEFAULT_CRAWL_LIMIT = 20
MAX_CRAWL_LIMIT = 500
#: Ceiling on comments per post: each one costs a permalink navigation.
MAX_CRAWL_COMMENTS = 100


def render(
    request: Request,
    name: str,
    context: dict[str, Any],
    status_code: int = 200,
):
    """Render a template, filling in the shared header/footer data.

    base.html always shows the version, the database path and the stored
    counts, so a page rendered from a bare context (404, the error page,
    a 400 or 401 raised inside a middleware) would otherwise print empty
    chrome. A context that already carries `version` built its own -- and
    skips the extra queries.
    """
    if "version" not in context:
        merged = _chrome(request)
        merged.update(context)
        context = merged
    response = templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _chrome(request: Request) -> dict[str, Any]:
    """Header/footer data, reading the database only if it is readable.

    Called on the error paths, so it must never raise: a page that is
    already reporting a failure cannot afford a second one.
    """
    try:
        conn = _open_readonly(request)
    except Exception:
        conn = None
    try:
        return base_context(request, conn)
    except Exception:
        return base_context(request)
    finally:
        if conn is not None:
            conn.close()


def build_pagination(
    request: Request, total: int, limit: int, offset: int
) -> dict[str, Any] | None:
    """Previous/next links that preserve every current query parameter."""
    if total <= 0:
        return None

    def link(new_offset: int) -> str:
        params = dict(request.query_params)
        params["offset"] = str(new_offset)
        params.setdefault("limit", str(limit))
        return f"{request.url.path}?{urlencode(params)}"

    last_offset = ((max(total - 1, 0)) // limit) * limit
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "from": min(offset + 1, total),
        "to": min(offset + limit, total),
        "prev_url": link(max(offset - limit, 0)) if offset > 0 else None,
        "next_url": link(min(offset + limit, last_offset))
        if offset + limit < total
        else None,
    }


def last_page_redirect(
    request: Request, total: int, limit: int, offset: int
) -> RedirectResponse | None:
    """Send an offset past the end back to the last page of results.

    Without this, `?offset=100` on a five-post database rendered an empty
    table under "No posts stored yet." -- false, and a dead end with no
    pagination controls to get back. Terminates after one hop: the
    clamped offset is always inside the result set.
    """
    if total <= 0 or offset < total or limit <= 0:
        return None
    last_offset = ((total - 1) // limit) * limit
    if offset == last_offset:
        return None
    params = dict(request.query_params)
    params["offset"] = str(last_offset)
    return RedirectResponse(f"{request.url.path}?{urlencode(params)}", status_code=303)


def base_context(request: Request, conn=None) -> dict[str, Any]:
    """Header/footer data shared by every page: db path, last run, counts."""
    context: dict[str, Any] = {
        "version": __version__,
        "db_path": str(request.app.state.config.db_path),
        "db_present": conn is not None,
        "total_posts": 0,
        "total_snapshots": 0,
        "total_runs": 0,
        "last_run": None,
    }
    if conn is not None:
        summary = queries.summary(conn)
        context.update(
            {
                "total_posts": summary["total_posts"],
                "total_snapshots": summary["total_snapshots"],
                "total_runs": summary["total_runs"],
                "last_run": summary["last_run"],
            }
        )
    return context


def _int_param(value: str | None, default: int, *, low: int, high: int) -> int:
    """HTML routes never 422: an unusable number falls back to the default."""
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return min(max(parsed, low), high)


def _remove_param(request: Request, name: str) -> str:
    """A link URL for /posts with just this one filter removed."""
    params = {k: v for k, v in request.query_params.multi_items() if k != name}
    params.pop("offset", None)
    query = f"?{urlencode(params)}" if params else ""
    return f"/posts{query}"


def _clear_filters_url(request: Request) -> str:
    kept = {k: v for k, v in request.query_params.multi_items() if k == "limit"}
    query = f"?{urlencode(kept)}" if kept else ""
    return f"/posts{query}"


def posts_list(request: Request):
    config = request.app.state.config
    context = base_context(request, conn=None)
    context.update(
        nav="posts",
        rows=[],
        total=0,
        q="",
        page_url="",
        since_input="",
        until_input="",
        order="newest",
        pages=[],
        multiple_pages=False,
        page_sizes=PAGE_SIZES,
        limit=DEFAULT_PAGE_SIZE,
        chips=[],
        has_filters=False,
        csv_url="",
        clear_url="/posts",
        pagination=None,
        empty=None,
        no_matches=False,
        empty_message="No posts stored yet.",
        empty_command="uv run crawler crawl <page-url>",
        empty_extra=None,
    )
    try:
        conn = queries.connect_ro(config.db_path)
    except DatabaseMissingError:
        return render(request, "posts.html", context)

    try:
        params = request.query_params
        q = (params.get("q") or "").strip()
        page_url = params.get("page_url") or None
        order = params.get("order") if params.get("order") in ("newest", "oldest") else "newest"
        limit = _int_param(
            params.get("limit"), DEFAULT_PAGE_SIZE, low=1, high=queries.MAX_PAGE_SIZE
        )
        offset = _int_param(params.get("offset"), 0, low=0, high=2**31)
        since = until = None
        since_input = until_input = ""
        for name, end_of_day in (("since", False), ("until", True)):
            raw = (params.get(name) or "").strip()
            if not raw:
                continue
            try:
                normalized = normalize_when(raw, end_of_day=end_of_day)
            except ValueError:
                continue
            if name == "since":
                since, since_input = normalized, raw
            else:
                until, until_input = normalized, raw

        pages = queries.list_pages(conn)
        rows, total = queries.list_posts(
            conn,
            limit=limit,
            offset=offset,
            contains=q or None,
            page_url=page_url,
            since=since,
            until=until,
            order=order,
        )
        context.update(base_context(request, conn))
    finally:
        conn.close()

    past_end = last_page_redirect(request, total, limit, offset)
    if past_end is not None:
        return past_end

    enriched = []
    for row in rows:
        when = row["published_at"] or row["last_seen"]
        enriched.append(
            {
                **row,
                "text_html": highlight(snippet(row["text"], q, 180), q or None),
                "when": when,
                "when_age": relative_age(when),
                "url": "/posts/" + quote(row["post_id"], safe=""),
            }
        )

    chips = []
    if q:
        chips.append(("q", f"“{q}”", _remove_param(request, "q")))
    if page_url:
        chips.append(("page_url", page_url, _remove_param(request, "page_url")))
    if since:
        chips.append(("since", f"since {since_input}", _remove_param(request, "since")))
    if until:
        chips.append(("until", f"until {until_input}", _remove_param(request, "until")))
    if order != "newest":
        chips.append(("order", "oldest first", _remove_param(request, "order")))
    chips = [
        {"label": label, "remove_url": remove_url} for _key, label, remove_url in chips
    ]

    query = request.url.query
    context.update(
        rows=enriched,
        total=total,
        q=q,
        page_url=page_url or "",
        since_input=since_input,
        until_input=until_input,
        order=order,
        pages=pages,
        multiple_pages=len(pages) > 1,
        limit=limit,
        chips=chips,
        has_filters=bool(chips),
        csv_url="/api/export/posts.csv" + (f"?{query}" if query else ""),
        clear_url=_clear_filters_url(request),
        pagination=build_pagination(request, total, limit, offset),
        no_matches=total == 0 and bool(q or page_url or since or until),
    )
    return render(request, "posts.html", context)


def _open_readonly(request: Request):
    """Read-only connection, or None while the database does not exist."""
    try:
        return queries.connect_ro(request.app.state.config.db_path)
    except DatabaseMissingError:
        return None


def _no_database_page(request: Request, template: str, nav: str, **extra: Any):
    """A list page's own empty state before the first crawl.

    These routes used to answer 404 "There is no page at this address."
    when the database file did not exist yet, which is untrue -- the page
    exists, the data does not -- and it makes a nav link look broken. The
    home page and /posts already degraded this way; this makes /runs,
    /snapshots and /state agree.
    """
    context = base_context(request)
    context.update(
        nav=nav,
        rows=[],
        total=0,
        limit=DEFAULT_PAGE_SIZE,
        pagination=None,
        pages=[],
        run_id="",
        page_url="",
        any_stale=False,
    )
    context.update(extra)
    return render(request, template, context)


COMMENTS_ON_POST_PAGE = 50


def _int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def post_detail(request: Request, post_id: str):
    conn = _open_readonly(request)
    if conn is None:
        return render(request, "404.html", {}, status_code=404)
    try:
        post = queries.get_post(conn, post_id)
        if post is None:
            return render(request, "404.html", {}, status_code=404)
        order = (
            request.query_params.get("order")
            if request.query_params.get("order") in ("newest", "oldest")
            else "newest"
        )
        prev_post, next_post = queries.post_neighbors(conn, post, order)
        near = queries.snapshots_near_post(conn, post)
        comments, comment_total = queries.list_comments(
            conn, post_id=post_id, limit=COMMENTS_ON_POST_PAGE
        )
        context = base_context(request, conn)
    finally:
        conn.close()

    order_suffix = f"?order={order}" if order != "newest" else ""
    context.update(
        nav="posts",
        post=post,
        first_seen_age=relative_age(post["first_seen"]),
        last_seen_age=relative_age(post["last_seen"]),
        prev_url=(
            "/posts/" + quote(prev_post["post_id"], safe="") + order_suffix
            if prev_post
            else None
        ),
        next_url=(
            "/posts/" + quote(next_post["post_id"], safe="") + order_suffix
            if next_post
            else None
        ),
        snapshots_near=near,
        comments=comments,
        comment_total=comment_total,
        page_url_q=quote(post["page_url"], safe=""),
    )
    return render(request, "post.html", context)


def snapshots_list(request: Request):
    conn = _open_readonly(request)
    if conn is None:
        return _no_database_page(request, "snapshots.html", "snapshots")
    try:
        params = request.query_params
        run_id = _int_or_none(params.get("run_id"))
        page_url = params.get("page_url") or None
        limit = _int_param(
            params.get("limit"), DEFAULT_PAGE_SIZE, low=1, high=queries.MAX_PAGE_SIZE
        )
        offset = _int_param(params.get("offset"), 0, low=0, high=2**31)
        rows, total = queries.list_snapshots(
            conn, run_id=run_id, page_url=page_url, limit=limit, offset=offset
        )
        pages = queries.list_pages(conn)
        context = base_context(request, conn)
    finally:
        conn.close()

    past_end = last_page_redirect(request, total, limit, offset)
    if past_end is not None:
        return past_end

    context.update(
        nav="snapshots",
        rows=[{**row, "sha12": row["sha256"][:12]} for row in rows],
        total=total,
        run_id=run_id or "",
        page_url=page_url or "",
        pages=pages,
        limit=limit,
        pagination=build_pagination(request, total, limit, offset),
    )
    return render(request, "snapshots.html", context)


def _snapshot_context(request: Request, snapshot_id: str):
    """(snapshot, base context), or None for an unknown id."""
    numeric = _int_or_none(snapshot_id)
    if numeric is None:
        return None
    conn = _open_readonly(request)
    if conn is None:
        return None
    try:
        snapshot = queries.get_snapshot(conn, numeric)
        context = base_context(request, conn)
    finally:
        conn.close()
    if snapshot is None:
        return None
    return snapshot, context


def snapshot_detail(request: Request, snapshot_id: str):
    loaded = _snapshot_context(request, snapshot_id)
    if loaded is None:
        return render(request, "404.html", {}, status_code=404)
    snapshot, context = loaded
    context.update(
        nav="snapshots",
        snapshot=snapshot,
        sha12=snapshot["sha256"][:12],
        posts_url=f"/posts?page_url={quote(snapshot['page_url'], safe='')}",
    )
    return render(request, "snapshot.html", context)


def home(request: Request):
    config = request.app.state.config
    try:
        conn = queries.connect_ro(config.db_path)
    except DatabaseMissingError:
        context = base_context(request)
        context.update(
            db_present=False,
            empty_message="No database yet. Run a crawl to store the first snapshot.",
            empty_command="uv run crawler crawl <page-url>",
        )
        return render(request, "index.html", context)
    now = datetime.now(timezone.utc)
    try:
        summary = queries.summary(conn)
        pages = queries.list_pages(conn)
        recent, _ = queries.list_posts(conn, limit=5)
        context = base_context(request, conn)
        posts_24h = queries.count_posts_since(
            conn, (now - timedelta(hours=24)).isoformat(timespec="seconds")
        )
        posts_7d = queries.count_posts_since(
            conn, (now - timedelta(days=7)).isoformat(timespec="seconds")
        )
        per_day = queries.posts_per_day(
            conn,
            since=(now - timedelta(days=CHART_DAYS - 1))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat(timespec="seconds"),
        )
    finally:
        conn.close()

    counts_by_day = {row["day"]: row["count"] for row in per_day}
    series = [
        (day, counts_by_day.get(day, 0))
        for day in (
            (now - timedelta(days=offset)).date().isoformat()
            for offset in range(CHART_DAYS - 1, -1, -1)
        )
    ]
    newest_post_at = summary["newest_post_at"]
    context.update(
        summary=summary,
        pages=pages,
        recent=[
            {**row, "url": "/posts/" + quote(row["post_id"], safe="")}
            for row in recent
        ],
        db_size_bytes=config.db_path.stat().st_size if config.db_path.exists() else 0,
        empty=summary["total_posts"] == 0,
        empty_message="No posts stored yet.",
        empty_command="uv run crawler crawl <page-url>",
        posts_24h=posts_24h,
        posts_7d=posts_7d,
        last_run_age=relative_age(summary["last_run"]["started_at"])
        if summary["last_run"]
        else None,
        stale_warning=bool(
            newest_post_at
            and summary["total_posts"] > 0
            and newest_post_at < (now - timedelta(days=7)).isoformat(timespec="seconds")
        ),
        chart_bars=_build_chart(series),
    )
    return render(request, "index.html", context)


def _build_chart(series: list[tuple[str, int]]) -> list[dict]:
    """SVG bar geometry for the posts-per-day chart; quiet days get no bar."""
    if not any(count for _, count in series):
        return []
    max_count = max(count for _, count in series)
    step = 300 / len(series)
    bar_width = max(2.0, step * 0.7)
    bars = []
    for index, (day, count) in enumerate(series):
        if not count:
            continue
        height = max(2, round(count / max_count * 56))
        bars.append(
            {
                "x": round(index * step, 2),
                "w": round(bar_width, 2),
                "y": 60 - height,
                "h": height,
                "day": day,
                "count": count,
            }
        )
    return bars


def _enrich_run(run: dict) -> dict:
    run = dict(run)
    run["duration_text"] = duration(run["started_at"], run["finished_at"])
    run["stale"] = run_is_stale(run)
    run["status_ok"] = run["status"] in queries.RUN_STATUSES
    run["started_age"] = relative_age(run["started_at"])
    return run


def runs_list(request: Request):
    conn = _open_readonly(request)
    if conn is None:
        return _no_database_page(request, "runs.html", "runs")
    try:
        params = request.query_params
        limit = _int_param(
            params.get("limit"), DEFAULT_PAGE_SIZE, low=1, high=queries.MAX_PAGE_SIZE
        )
        offset = _int_param(params.get("offset"), 0, low=0, high=2**31)
        rows, total = queries.list_runs(conn, limit=limit, offset=offset)
        context = base_context(request, conn)
    finally:
        conn.close()
    past_end = last_page_redirect(request, total, limit, offset)
    if past_end is not None:
        return past_end
    enriched = [_enrich_run(run) for run in rows]
    context.update(
        nav="runs",
        rows=enriched,
        total=total,
        limit=limit,
        any_stale=any(run["stale"] for run in enriched),
        pagination=build_pagination(request, total, limit, offset),
    )
    return render(request, "runs.html", context)


def run_detail(request: Request, run_id: str):
    numeric = _int_or_none(run_id)
    conn = _open_readonly(request)
    if numeric is None or conn is None:
        if conn is not None:
            conn.close()
        return render(request, "404.html", {}, status_code=404)
    try:
        run = queries.get_run(conn, numeric)
        if run is None:
            return render(request, "404.html", {}, status_code=404)
        snapshots, _snap_total = queries.list_snapshots(
            conn, run_id=numeric, limit=queries.MAX_PAGE_SIZE
        )
        until = (
            run["finished_at"]
            or datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
        yield_posts, yield_total = queries.posts_first_seen_between(
            conn,
            started_at=run["started_at"],
            finished_at=until,
            limit=queries.MAX_PAGE_SIZE,
        )
        context = base_context(request, conn)
    finally:
        conn.close()
    context.update(
        nav="runs",
        run=_enrich_run(run),
        snapshots=snapshots,
        yield_posts=[
            {**post, "url": "/posts/" + quote(post["post_id"], safe="")}
            for post in yield_posts
        ],
        yield_total=yield_total,
        yield_truncated=yield_total > len(yield_posts),
    )
    return render(request, "run.html", context)


def state_view(request: Request):
    conn = _open_readonly(request)
    if conn is None:
        return _no_database_page(request, "state.html", "")
    try:
        rows = queries.get_state(conn)
        enriched = []
        for row in rows:
            known = (
                queries.get_post(conn, row["last_post_id"])
                if row["last_post_id"]
                else None
            )
            enriched.append(
                {
                    **row,
                    "post_url": (
                        "/posts/" + quote(row["last_post_id"], safe="")
                        if known is not None
                        else None
                    ),
                }
            )
        context = base_context(request, conn)
    finally:
        conn.close()
    context.update(nav="", rows=enriched)
    return render(request, "state.html", context)


def _csrf_ok(request: Request, form_token: str | None) -> bool:
    """Per-server-start token plus an Origin/Referer host check.

    The token is minted once per server start and embedded in every form.
    A cross-site page can read neither, but a browser form post always
    carries an Origin -- so its host must match the host the request was
    addressed to. Non-browser clients (no Origin/Referer) pass on the
    token alone.
    """
    expected = getattr(request.app.state, "csrf_token", None)
    if not expected or not form_token:
        return False
    if not hmac.compare_digest(form_token, expected):
        return False
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if origin:
        return urlsplit(origin).netloc == host
    referer = request.headers.get("referer")
    if referer:
        return urlsplit(referer).netloc == host
    return True


def _crawl_context(request: Request, **extra: Any) -> dict[str, Any]:
    status = crawl_job.status()
    gui_ready = True
    try:
        check_gui_session()
    except Exception:
        gui_ready = False
    conn = _open_readonly(request)
    known_pages: list[dict] = []
    last_run = None
    # base_context needs this connection or the footer counts render as zeros.
    context = base_context(request, conn)
    if conn is not None:
        try:
            known_pages = queries.list_pages(conn)
            recent_runs, _total = queries.list_runs(conn, limit=1)
            last_run = recent_runs[0] if recent_runs else None
        finally:
            conn.close()
    context.update(
        nav="crawl",
        status=status,
        csrf_token=request.app.state.csrf_token,
        page_url_value=request.app.state.config.page_url or "",
        limit_value=DEFAULT_CRAWL_LIMIT,
        comments_value=request.app.state.config.top_comments,
        pages=known_pages,
        gui_ready=gui_ready,
        last_run=last_run,
        reason=None,
    )
    context.update(extra)
    return context


def crawl_page(request: Request):
    return render(request, "crawl.html", _crawl_context(request))


async def crawl_start(request: Request):
    """Start a crawl. POST only: a GET must never start one."""
    form = await request.form()
    if not _csrf_ok(request, form.get("csrf_token")):
        context = _crawl_context(
            request, reason="Invalid or missing form token (CSRF check failed)."
        )
        return render(request, "crawl.html", context, status_code=403)
    config = request.app.state.config
    try:
        limit = int(str(form.get("limit") or DEFAULT_CRAWL_LIMIT))
    except ValueError:
        limit = DEFAULT_CRAWL_LIMIT
    limit = max(1, min(limit, MAX_CRAWL_LIMIT))
    try:
        comments = int(str(form.get("comments") or 0))
    except ValueError:
        comments = 0
    comments = max(0, min(comments, MAX_CRAWL_COMMENTS))
    page_url = str(form.get("page_url") or "").strip()
    try:
        crawl_job.start(
            page_url,
            limit,
            comments=comments,
            comments_max_posts=config.comments_max_posts if comments else None,
        )
    except JobRefused as exc:
        return render(
            request,
            "crawl.html",
            _crawl_context(request, reason=str(exc)),
            status_code=409,
        )
    return RedirectResponse("/crawl", status_code=303)


async def crawl_stop(request: Request):
    form = await request.form()
    if not _csrf_ok(request, form.get("csrf_token")):
        context = _crawl_context(
            request, reason="Invalid or missing form token (CSRF check failed)."
        )
        return render(request, "crawl.html", context, status_code=403)
    crawl_job.stop()
    return RedirectResponse("/crawl", status_code=303)
