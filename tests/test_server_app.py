"""Required tests from plans/v2/02-server-skeleton.md."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from crawler_social import db, queries
from crawler_social.cli import app as cli_app
from crawler_social.config import Config
from crawler_social.server import app as server_app
from crawler_social.server.app import create_app


def make_config(db_path: Path) -> Config:
    return Config(
        browser_binary=None,
        profile_dir=db_path.parent / "profile",
        db_path=db_path,
        page_url=None,
    )


def make_db(path: Path) -> None:
    conn = db.connect(path)
    try:
        run_id = db.start_run(conn, "2026-01-01T00:00:00+00:00")
        with db.post_transaction(conn):
            db.upsert_post(
                conn, "p1", "https://a.example", "hello world", "Ann",
                "2026-01-01T10:00:00+00:00", "2026-01-01T00:00:00+00:00",
            )
        db.finish_run(
            conn, run_id, "completed", finished_at="2026-01-01T00:01:00+00:00"
        )
    finally:
        conn.close()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "social.db"
    make_db(path)
    return path


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(make_config(db_path)))


def test_healthz_with_database(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db_present"] is True
    assert body["version"]


def test_healthz_without_database(tmp_path: Path):
    client = TestClient(create_app(make_config(tmp_path / "missing.db")))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["db_present"] is False


def test_data_endpoint_503_when_database_missing(tmp_path: Path):
    client = TestClient(create_app(make_config(tmp_path / "missing.db")))
    # A data route that exists by Step 03; healthz is the only always-on
    # route, so exercise the dependency directly through a probe route.
    app = create_app(make_config(tmp_path / "missing.db"))

    from fastapi import Depends

    @app.get("/_probe")
    def probe(conn: sqlite3.Connection = Depends(server_app.get_conn)):
        return {"ok": conn.execute("SELECT 1").fetchone()[0]}

    client = TestClient(app)
    resp = client.get("/_probe")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "database_missing"
    assert body["hint"] == "run `crawler crawl` first"
    assert "missing.db" in body["path"]


def test_connection_dependency_closes_after_request(db_path: Path):
    closed = []

    class RecordingConn:
        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        def execute(self, *args, **kwargs):
            return self._real.execute(*args, **kwargs)

        def close(self):
            closed.append(True)
            self._real.close()

    real_connect_ro = queries.connect_ro

    def fake_connect_ro(path):
        return RecordingConn(real_connect_ro(path))

    app = create_app(make_config(db_path))
    from fastapi import Depends

    @app.get("/_probe")
    def probe(conn: sqlite3.Connection = Depends(server_app.get_conn)):
        return {"ok": conn.execute("SELECT 1").fetchone()[0]}

    app_module = server_app
    original = app_module.connect_ro
    app_module.connect_ro = fake_connect_ro
    try:
        client = TestClient(app)
        resp = client.get("/_probe")
        assert resp.status_code == 200
        assert closed == [True]
    finally:
        app_module.connect_ro = original


def test_internal_error_returns_opaque_500(db_path: Path):
    app = create_app(make_config(db_path))

    @app.get("/_boom")
    def boom():
        raise RuntimeError("secret /tmp/leak SELECT * FROM posts")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/_boom")
    assert resp.status_code == 500
    # plans/v2/09: the page carries only a request id -- no path, no SQL,
    # no traceback. The id goes to stderr with the full traceback instead.
    assert "/tmp/leak" not in resp.text
    assert "SELECT" not in resp.text
    assert "RuntimeError" not in resp.text
    assert re.search(r"\b[0-9a-f]{16}\b", resp.text), "request id missing"


def test_serve_refuses_non_loopback_without_flag():
    runner = CliRunner()
    result = runner.invoke(cli_app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "--allow-remote" in result.output


def test_serve_loopback_host_is_accepted(monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    runner = CliRunner()
    result = runner.invoke(cli_app, ["serve", "--host", "127.0.0.1"])
    assert result.exit_code == 0
    assert "--allow-remote" not in result.output
    assert "http://127.0.0.1" in result.output
    assert calls, "uvicorn.run was reached with a loopback host"
