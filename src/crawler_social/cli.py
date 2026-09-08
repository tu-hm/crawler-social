"""Command-line interface for crawler-social."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from . import __version__, paths
from .config import load_config

#: Enough to see the shape of a failure without burying the summary.
DIAGNOSTICS_SHOWN = 10

app = typer.Typer(
    add_completion=False,
    help="Simple, safe crawler for one public Facebook Page.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"crawler-social {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """Simple, safe crawler for one public Facebook Page."""


#: `--full` means "collect everything this crawler knows how to collect".
#: The comment ceiling is deliberately generous rather than unbounded: a
#: thread of thousands is not what "full post" means to anyone.
FULL_TOP_COMMENTS = 50


def _resolve_targets(page_urls: list[str], targets_file: Optional[Path], config):
    """Every URL this invocation should crawl, in order, without duplicates.

    Three sources, most explicit first: the URLs on the command line, a
    `--targets` file, and the `.env` defaults. They combine rather than
    override, so `crawl <url> --targets list.txt` crawls both.
    """
    from .targets import TargetsError, load_targets, normalize_target

    resolved: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        url = normalize_target(raw)
        if url is None:
            typer.secho(
                f"skipping {raw!r}: not a Facebook page or group URL.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            return
        if url not in seen:
            seen.add(url)
            resolved.append(url)

    for raw in page_urls or []:
        add(raw)
    chosen_file = targets_file or (None if page_urls else config.targets_file)
    if chosen_file is not None:
        try:
            for url in load_targets(chosen_file):
                if url not in seen:
                    seen.add(url)
                    resolved.append(url)
        except TargetsError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from exc
    if not resolved and config.page_url:
        add(config.page_url)
    return resolved


def _report(summary) -> None:
    """Print one run's counts, then whatever it could not do."""
    typer.echo(
        f"run: {summary.run_id} | snapshots: {summary.snapshots_captured} "
        f"({summary.snapshots_total} total) | new posts: {summary.new_posts} "
        f"| existing posts: {summary.existing_posts} | errors: {summary.errors}"
    )
    if summary.posts_hydrated or summary.posts_with_comments or summary.comments_captured:
        typer.echo(
            f"full posts: {summary.posts_hydrated} | comments: "
            f"{summary.comments_captured} across {summary.posts_with_comments} posts"
        )
    # The count alone cannot say whether the markup moved again or the posts
    # simply had nothing on them, and that is the first question every time.
    if summary.diagnostics:
        shown = summary.diagnostics[:DIAGNOSTICS_SHOWN]
        typer.secho("diagnostics:", fg=typer.colors.YELLOW, err=True)
        for line in shown:
            typer.secho(f"  {line}", fg=typer.colors.YELLOW, err=True)
        remaining = len(summary.diagnostics) - len(shown)
        if remaining > 0:
            typer.secho(f"  ... and {remaining} more", fg=typer.colors.YELLOW, err=True)
    if summary.blocked:
        typer.echo(f"status: {summary.status} (blocked: {summary.blocked})")
        typer.secho(summary.blocked_message or "", fg=typer.colors.YELLOW, err=True)
    else:
        typer.echo(f"status: {summary.status}")


@app.command()
def crawl(
    page_urls: Optional[list[str]] = typer.Argument(
        None,
        help="Facebook Page or group URLs to crawl, in order.",
    ),
    targets: Optional[Path] = typer.Option(
        None,
        "--targets",
        "-t",
        help="File of URLs to crawl, one per line. Headings and # comments "
        "are ignored, so a hand-written list works as it is.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Hard ceiling on newly emitted posts, per target.",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Collect whole posts: hover for the exact time and permalink, "
        "expand long bodies, then visit every post found for its comments "
        "and replies. Costs one page load per post.",
    ),
    comments: Optional[int] = typer.Option(
        None,
        "--comments",
        min=0,
        help="Top-level comments to collect per post (0 = none). Default: "
        "CRAWLER_TOP_COMMENTS, itself 0.",
    ),
    comments_max_posts: Optional[int] = typer.Option(
        None,
        "--comments-max-posts",
        min=1,
        help="Ceiling on how many posts get a permalink visit.",
    ),
    replies: Optional[bool] = typer.Option(
        None,
        "--replies/--no-replies",
        help="Store reply threads under their parent comment.",
    ),
    expand: Optional[bool] = typer.Option(
        None,
        "--expand/--no-expand",
        help='Click "See more" so long post bodies are captured in full.',
    ),
    hover: Optional[bool] = typer.Option(
        None,
        "--hover/--no-hover",
        help="Hover each story's timestamp to make its permalink and exact "
        "publication time exist. A feed carries neither.",
    ),
) -> None:
    """Capture each target, parse it, and store the posts it yields.

    Captured markup is never written to disk: it is hashed, measured, and
    parsed in memory, and the text is what the database keeps.

    A post is read twice. The feed pass finds it and reads what the feed
    shows -- author, a possibly clipped body, a relative age. The permalink
    pass then re-reads it whole, which is where the exact publication time
    and the comment thread come from, and is what `--full` turns on.
    """
    from .pipeline import run_crawl

    config = load_config()
    urls = _resolve_targets(list(page_urls or []), targets, config)
    if not urls:
        typer.secho(
            "No Page URL given. Pass one or more URLs, --targets FILE, or set "
            "CRAWLER_PAGE_URL in .env.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    if full:
        # --full is a shorthand for the flags below, so an explicit flag still
        # wins: `--full --comments 5` asks for five, not fifty.
        comments = FULL_TOP_COMMENTS if comments is None else comments
        # Every post the feed yielded, which is what --limit already caps.
        comments_max_posts = limit if comments_max_posts is None else comments_max_posts
        expand = True if expand is None else expand
        hover = True if hover is None else hover
        replies = True if replies is None else replies

    blocked = False
    failed: list[str] = []
    totals = {"new": 0, "hydrated": 0, "comments": 0}
    for index, url in enumerate(urls):
        if len(urls) > 1:
            typer.secho(
                f"\n[{index + 1}/{len(urls)}] {url}", fg=typer.colors.CYAN
            )
        try:
            summary = run_crawl(
                url,
                limit=limit,
                config=config,
                top_comments=comments,
                comments_max_posts=comments_max_posts,
                expand_text=expand,
                include_replies=replies,
                hover_timestamps=hover,
            )
        except Exception as exc:  # noqa: BLE001 - one bad target, not the run
            typer.secho(f"crawl failed: {exc}", fg=typer.colors.RED, err=True)
            failed.append(url)
            continue
        _report(summary)
        totals["new"] += summary.new_posts
        totals["hydrated"] += summary.posts_hydrated
        totals["comments"] += summary.comments_captured
        # A wall is the whole session's problem, not this target's: the next
        # target would meet the same one, and hammering it makes it worse.
        if summary.blocked:
            blocked = True
            if index + 1 < len(urls):
                typer.secho(
                    f"stopping: {len(urls) - index - 1} target(s) not crawled.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            break

    if len(urls) > 1:
        typer.echo(
            f"\ntotal: {totals['new']} new posts | {totals['hydrated']} full "
            f"posts | {totals['comments']} comments"
        )
    if blocked:
        raise typer.Exit(3)
    if failed:
        raise typer.Exit(1)


@app.command()
def login() -> None:
    """Open a browser and wait while you sign in to Facebook by hand.

    Credentials are never typed by this program. Doing the login in a window
    you control -- ideally with CRAWLER_ATTACH_MODE=cdp against your own
    Chrome -- is what keeps the sign-in from being treated as automated.
    """
    from . import facebook

    config = load_config()
    try:
        verdict = facebook.login(config)
    except Exception as exc:  # noqa: BLE001 - one clear message for the user
        typer.secho(f"login failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.secho(
        f"Signed in. Session verdict: {verdict.kind}.",
        fg=typer.colors.GREEN,
    )


@app.command()
def session() -> None:
    """Report whether the browser profile currently holds a live session."""
    from . import facebook, wall

    config = load_config()
    typer.echo(f"attach mode: {config.attach_mode}")
    typer.echo(f"profile:     {config.profile_dir}")
    if config.attach_mode == "cdp":
        typer.echo(f"cdp url:     {config.cdp_url}")
    try:
        verdict = facebook.check_session(config)
    except Exception as exc:  # noqa: BLE001 - one clear message for the user
        typer.secho(f"session check failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    if verdict.kind == wall.LOGIN_WALL:
        typer.secho("not logged in. Run `crawler login`.", fg=typer.colors.RED)
        raise typer.Exit(3)
    if verdict.blocking:
        typer.secho(verdict.message, fg=typer.colors.YELLOW)
        raise typer.Exit(3)
    typer.secho(f"session looks live ({verdict.kind}).", fg=typer.colors.GREEN)


@app.command()
def serve(
    host: Optional[str] = typer.Option(
        None,
        "--host",
        help="Address to bind. Loopback only unless --allow-remote is given.",
    ),
    port: Optional[int] = typer.Option(
        None,
        "--port",
        min=1,
        max=65535,
        help="Port to bind (default 8765, or CRAWLER_SERVE_PORT).",
    ),
    allow_remote: bool = typer.Option(
        False,
        "--allow-remote",
        help="Permit a non-loopback bind. Also requires CRAWLER_SERVE_TOKEN.",
    ),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Restart the server on code changes (development only).",
    ),
) -> None:
    """Serve the local read-only web viewer for the stored data."""
    from .server.app import build_app, create_app, validate_remote_bind

    config = load_config()
    host = host or config.serve_host
    port = port if port is not None else config.serve_port

    refusal = validate_remote_bind(
        host, allow_remote=allow_remote, token=config.serve_token
    )
    if refusal:
        typer.secho(refusal, fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    typer.echo(f"crawler-social web viewer: http://{host}:{port}")
    import uvicorn

    try:
        if reload:
            uvicorn.run(
                "crawler_social.server.app:build_app",
                host=host,
                port=port,
                reload=True,
                factory=True,
                timeout_keep_alive=5,
                access_log=False,
            )
        else:
            # uvicorn's access log prints the raw query string, token included.
            uvicorn.run(
                create_app(config),
                host=host,
                port=port,
                timeout_keep_alive=5,
                access_log=False,
            )
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


@app.command()
def posts(
    limit: int = typer.Option(
        10,
        "--limit",
        min=1,
        help="Maximum number of posts to show.",
    ),
    contains: Optional[str] = typer.Option(
        None,
        "--contains",
        help="Only show posts whose text contains TEXT.",
    ),
) -> None:
    """List stored posts, newest first."""
    from .db import connect, list_posts

    config = load_config()
    if not config.db_path.exists():
        typer.echo("No database yet. Run `crawler crawl` first.")
        raise typer.Exit(0)
    with connect(config.db_path) as conn:
        rows = list_posts(conn, limit=limit, contains=contains)
    if not rows:
        typer.echo("No posts stored yet. Run `crawler crawl` first.")
        return
    for (
        post_id,
        page_url,
        text,
        author,
        published_at,
        reaction_count,
        first_seen,
        last_seen,
    ) in rows:
        typer.echo(f"- {post_id}")
        typer.echo(f"  page:     {page_url}")
        typer.echo(f"  author:   {author if author is not None else '(none)'}")
        typer.echo(f"  time:     {published_at if published_at is not None else '(none)'}")
        typer.echo(
            f"  reactions:{reaction_count if reaction_count is not None else '(none)'}"
        )
        typer.echo(f"  seen:     {first_seen} -> {last_seen}")
        excerpt = (text or "").replace("\n", " ")[:200]
        typer.echo(f"  text:     {excerpt}")


@app.command()
def comments(
    post_id: Optional[str] = typer.Option(
        None,
        "--post-id",
        help="Only show comments on this post.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Maximum number of comments to show.",
    ),
) -> None:
    """List stored comments, in the order Facebook showed them."""
    from .db import connect, list_comments

    config = load_config()
    if not config.db_path.exists():
        typer.echo("No database yet. Run `crawler crawl --comments N` first.")
        raise typer.Exit(0)
    with connect(config.db_path) as conn:
        rows = list_comments(conn, post_id=post_id, limit=limit)
    if not rows:
        typer.echo(
            "No comments stored yet. Run `crawler crawl --comments N` first."
        )
        return
    for row in rows:
        (
            comment_id,
            row_post_id,
            _page_url,
            author,
            text,
            published_at,
            like_count,
            rank_index,
            parent_comment_id,
            _first_seen,
            _last_seen,
        ) = row
        # Replies sort directly under the comment they answer, so indenting
        # them is enough to show the thread in a flat listing.
        indent = "  " if parent_comment_id else ""
        typer.echo(f"{indent}- #{rank_index} {comment_id}")
        if parent_comment_id:
            typer.echo(f"{indent}  reply to: {parent_comment_id}")
        typer.echo(f"{indent}  post:     {row_post_id}")
        typer.echo(f"{indent}  author:   {author if author is not None else '(none)'}")
        typer.echo(
            f"{indent}  time:     "
            f"{published_at if published_at is not None else '(none)'}"
        )
        typer.echo(f"{indent}  likes:    {like_count if like_count is not None else '(none)'}")
        excerpt = (text or "").replace("\n", " ")[:200]
        typer.echo(f"{indent}  text:     {excerpt}")


if __name__ == "__main__":
    app()
