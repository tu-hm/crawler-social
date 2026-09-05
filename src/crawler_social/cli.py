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
    typer.echo(f"status: {summary.status}")


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
