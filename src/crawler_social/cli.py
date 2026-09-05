"""Command-line interface for crawler-social."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from . import __version__, paths
from .config import load_config

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


@app.command()
def crawl(
    page_url: str = typer.Argument(
        None,
        help="Public Facebook Page URL to crawl.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        min=1,
        help="Hard ceiling on newly emitted posts.",
    ),
) -> None:
    """Capture raw HTML snapshots of the Page, then parse and store posts."""
    from .pipeline import run_crawl

    config = load_config()
    url = page_url or config.page_url
    if not url:
        typer.secho(
            "No Page URL given. Pass PAGE_URL or set CRAWLER_PAGE_URL in .env.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    try:
        summary = run_crawl(url, limit=limit, config=config)
    except Exception as exc:  # noqa: BLE001 - surfaced as failed run
        typer.secho(f"crawl failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"run: {summary.run_id} | snapshots: {summary.snapshots_captured} "
        f"({summary.snapshots_total} total) | new posts: {summary.new_posts} "
        f"| existing posts: {summary.existing_posts} | errors: {summary.errors}"
    )
    if summary.blocked:
        typer.echo(f"status: {summary.status} (blocked: {summary.blocked})")
        typer.secho(summary.blocked_message or "", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(3)
    typer.echo(f"status: {summary.status}")


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
            # uvicorn's own access log prints the raw query string, token
            # included; the app's redacting request log replaces it.
            uvicorn.run(
                create_app(config),
                host=host,
                port=port,
                timeout_keep_alive=5,
                access_log=False,
            )
    except KeyboardInterrupt:
        # Ctrl-C on a running server is a normal shutdown, not an error.
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
    for post_id, page_url, text, author, published_at, first_seen, last_seen in rows:
        typer.echo(f"- {post_id}")
        typer.echo(f"  page:     {page_url}")
        typer.echo(f"  author:   {author if author is not None else '(none)'}")
        typer.echo(f"  time:     {published_at if published_at is not None else '(none)'}")
        typer.echo(f"  seen:     {first_seen} -> {last_seen}")
        excerpt = (text or "").replace("\n", " ")[:200]
        typer.echo(f"  text:     {excerpt}")


if __name__ == "__main__":
    app()
