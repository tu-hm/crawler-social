"""Required tests from plans/v2/00-scope-and-preflight.md."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from crawler_social.config import DEFAULT_SERVE_HOST, DEFAULT_SERVE_PORT, load_config


def _env(**overrides: str) -> dict[str, str]:
    env = {k: v for k, v in {
        "CRAWLER_PROFILE_DIR": "/tmp/profile",
        "CRAWLER_DB_PATH": "/tmp/social.db",
    }.items()}
    env.update(overrides)
    return env


def test_defaults_when_no_server_variables_set():
    config = load_config(_env())
    assert config.serve_host == DEFAULT_SERVE_HOST == "127.0.0.1"
    assert config.serve_port == DEFAULT_SERVE_PORT == 8765
    assert config.serve_token is None


def test_reads_serve_variables_from_environment():
    config = load_config(
        _env(
            CRAWLER_SERVE_HOST="0.0.0.0",
            CRAWLER_SERVE_PORT="9000",
            CRAWLER_SERVE_TOKEN="x" * 32,
        )
    )
    assert config.serve_host == "0.0.0.0"
    assert config.serve_port == 9000
    assert config.serve_token == "x" * 32


def test_non_numeric_port_raises_clear_error():
    with pytest.raises(ValueError, match="CRAWLER_SERVE_PORT"):
        load_config(_env(CRAWLER_SERVE_PORT="not-a-port"))


def test_port_out_of_range_raises():
    with pytest.raises(ValueError, match="CRAWLER_SERVE_PORT"):
        load_config(_env(CRAWLER_SERVE_PORT="70000"))


def test_readonly_connection_selects_but_cannot_create(tmp_path: Path):
    path = tmp_path / "social.db"
    with sqlite3.connect(path) as writer:
        writer.execute("CREATE TABLE posts (post_id TEXT PRIMARY KEY)")
        writer.execute("INSERT INTO posts VALUES ('p1')")
        writer.commit()

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE hack (x INTEGER)")
    finally:
        conn.close()
