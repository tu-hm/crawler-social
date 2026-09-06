"""Shared fixtures and seeders for the v2 server tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from crawler_social import db
from crawler_social.config import Config
from crawler_social.server.app import create_app


def make_config(db_path: Path) -> Config:
    return Config(
        browser_binary=None,
        profile_dir=db_path.parent / "profile",
        db_path=db_path,
        page_url=None,
    )


def make_db(
    path: Path,
    *,
    posts: list[tuple] | None = None,
    runs: list[tuple] | None = None,
    snapshots: list[tuple] | None = None,
    states: list[tuple] | None = None,
) -> None:
    """Seed a temporary database.

    posts:   (post_id, page_url, text, author, published_at, first_seen[, last_seen])
    runs:    (started_at, status, finished_at, error)
    snapshots: (run_index, page_url, captured_at, html). The html is
               measured and hashed, as a real capture's is, not stored.
    states:  (page_url, last_post_id, last_post_time, updated_at)
    """
    conn = db.connect(path)
    try:
        run_ids = []
        for started_at, status, finished_at, error in runs or []:
            run_id = db.start_run(conn, started_at)
            if status == "running" and finished_at is None:
                # Leave the row genuinely running: finish_run would stamp a
                # finished_at, and a real running row has none.
                run_ids.append(run_id)
                continue
            db.finish_run(conn, run_id, status, error, finished_at=finished_at)
            run_ids.append(run_id)

        for run_index, page_url, captured_at, html in snapshots or []:
            db.save_snapshot(
                conn, run_ids[run_index], page_url, captured_at, html
            )

        with db.post_transaction(conn):
            for post in posts or []:
                if len(post) == 6:
                    post_id, page_url, text, author, published, first_seen = post
                    last_seen = first_seen
                else:
                    post_id, page_url, text, author, published, first_seen, last_seen = post
                db.upsert_post(
                    conn, post_id, page_url, text, author, published, first_seen
                )
                if last_seen != first_seen:
                    db.upsert_post(
                        conn, post_id, page_url, text, author, published, last_seen
                    )
            for page_url, last_post_id, last_post_time, updated_at in states or []:
                db.set_state(conn, page_url, last_post_id, last_post_time, updated_at)
    finally:
        conn.close()


@pytest.fixture()
def app_factory(tmp_path: Path):
    def build(db_name: str = "social.db"):
        return create_app(make_config(tmp_path / db_name))

    return build


@pytest.fixture()
def db_file(tmp_path: Path) -> Path:
    return tmp_path / "social.db"
