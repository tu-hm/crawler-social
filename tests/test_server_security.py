"""Required tests from plans/v2/09-hardening.md."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from crawler_social import queries
from crawler_social.cli import app as cli_app
from crawler_social.config import Config
from crawler_social.server.app import create_app, validate_remote_bind
from crawler_social.server.hardening import APP_CSP, SESSION_COOKIE

from tests.conftest import make_db

TOKEN = "a" * 40
SHORT_TOKEN = "abcdefghij"
RAW_SNAPSHOT_CSP = "default-src 'none'; style-src 'unsafe-inline'"

HTML_ROUTES = ["/", "/posts", "/snapshots", "/runs", "/state", "/crawl"]

# The full route table, HTML and API, used for the 0o444 read-only sweep.
ALL_GET_ROUTES = [
    "/",
    "/healthz",
    "/posts",
    "/posts/p1",
    "/posts?q=hello",
    "/snapshots",
    "/snapshots/1",
    "/snapshots/1/source",
    "/snapshots/1/reparse",
    "/runs",
    "/runs/1",
    "/state",
    "/crawl",
    "/api/summary",
    "/api/posts",
    "/api/posts/p1",
    "/api/pages",
    "/api/runs",
    "/api/runs/1",
    "/api/snapshots",
    "/api/snapshots/1/raw",
    "/api/snapshots/1/download",
    "/api/state",
    "/api/crawl/status",
    "/api/export/posts.csv",
]

INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)", re.IGNORECASE)
INLINE_STYLE = re.compile(r"<style[\s>]", re.IGNORECASE)


def _output(result) -> str:
    """stdout + stderr, however this click version captured them."""
    text = result.output or ""
    try:
        text += result.stderr or ""
    except Exception:  # click < 8.2 raises when stderr is mixed in
        pass
    return text


def build_app(db_path: Path, *, token: str | None = None):
    config = Config(
        browser_binary=None,
        profile_dir=db_path.parent / "profile",
        db_path=db_path,
        page_url=None,
        serve_token=token,
    )
    return create_app(config)


def client_for(db_path: Path, **kwargs) -> TestClient:
    return TestClient(build_app(db_path, **kwargs))


def make_app_boom(*args, **kwargs):
    raise RuntimeError("secret /tmp/leak SELECT * FROM posts")


@pytest.fixture()
def seeded(db_file: Path) -> Path:
    make_db(
        db_file,
        posts=[
            (
                "p1",
                "https://www.facebook.com/ExamplePublicPage",
                "hello world",
                None,
                "2026-09-05T10:00:00+00:00",
                "2026-09-05T10:00:00+00:00",
            )
        ],
        runs=[("2026-09-05T09:00:00+00:00", "completed", "2026-09-05T09:01:00+00:00", None)],
        snapshots=[
            (
                0,
                "https://www.facebook.com/ExamplePublicPage",
                "2026-09-05T09:00:30+00:00",
                b"<html><body>raw snapshot</body></html>",
            )
        ],
        states=[
            (
                "https://www.facebook.com/ExamplePublicPage",
                "p1",
                "2026-09-05T10:00:00+00:00",
                "2026-09-05T10:00:00+00:00",
            )
        ],
    )
    return db_file


# --- Binding ----------------------------------------------------------------


def test_non_loopback_without_allow_remote_refuses_naming_flag():
    runner = CliRunner()
    result = runner.invoke(
        cli_app, ["serve", "--host", "0.0.0.0"], env={"CRAWLER_SERVE_TOKEN": ""}
    )
    assert result.exit_code == 2
    assert "--allow-remote" in _output(result)


def test_non_loopback_without_token_refuses_naming_variable():
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        ["serve", "--host", "0.0.0.0", "--allow-remote"],
        env={"CRAWLER_SERVE_TOKEN": ""},
    )
    assert result.exit_code == 2
    assert "CRAWLER_SERVE_TOKEN" in _output(result)


def test_short_token_refused():
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        ["serve", "--host", "0.0.0.0", "--allow-remote"],
        env={"CRAWLER_SERVE_TOKEN": SHORT_TOKEN},
    )
    assert result.exit_code == 2
    text = _output(result)
    assert "CRAWLER_SERVE_TOKEN" in text
    assert "32" in text


def test_validate_remote_bind_matrix():
    assert validate_remote_bind("127.0.0.1", allow_remote=False, token=None) is None
    assert validate_remote_bind("::1", allow_remote=False, token=None) is None
    refusal = validate_remote_bind("0.0.0.0", allow_remote=False, token=None)
    assert refusal and "--allow-remote" in refusal
    no_token = validate_remote_bind("0.0.0.0", allow_remote=True, token=None)
    assert no_token and "CRAWLER_SERVE_TOKEN" in no_token
    short = validate_remote_bind("0.0.0.0", allow_remote=True, token="a" * 31)
    assert short and "CRAWLER_SERVE_TOKEN" in short
    assert validate_remote_bind("0.0.0.0", allow_remote=True, token="a" * 32) is None


# --- Token auth -------------------------------------------------------------


def test_unauthenticated_401_authenticated_200(seeded):
    client = client_for(seeded, token=TOKEN)
    resp = client.get("/posts")
    assert resp.status_code == 401
    resp = client.get("/posts", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    # The first token presentation sets the session cookie, so plain links
    # keep working afterwards.
    set_cookie = resp.headers["set-cookie"]
    assert SESSION_COOKIE in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=strict" in set_cookie.lower()
    assert TOKEN not in set_cookie, "cookie must not carry the raw token"
    assert client.get("/runs").status_code == 200


def test_token_via_query_param_also_works(seeded):
    client = client_for(seeded, token=TOKEN)
    resp = client.get("/posts?token=" + TOKEN)
    assert resp.status_code == 200
    assert "set-cookie" in resp.headers


def test_token_prefix_is_rejected(seeded):
    client = client_for(seeded, token=TOKEN)
    prefix = TOKEN[:-1] + "b"  # same length, shares a long prefix
    resp = client.get("/posts", headers={"Authorization": f"Bearer {prefix}"})
    assert resp.status_code == 401
    resp = client.get("/posts?token=" + prefix)
    assert resp.status_code == 401
    # A truncated token (different length) is rejected just as surely.
    resp = client.get("/posts?token=" + TOKEN[:-1])
    assert resp.status_code == 401


def test_healthz_answers_without_token(seeded):
    client = client_for(seeded, token=TOKEN)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_401_shape_follows_path_prefix(seeded):
    client = client_for(seeded, token=TOKEN)
    api = client.get("/api/posts")
    assert api.status_code == 401
    assert api.headers["content-type"].startswith("application/json")
    page = client.get("/posts")
    assert page.status_code == 401
    assert "text/html" in page.headers["content-type"]


# --- Response headers -------------------------------------------------------


def test_html_responses_carry_security_headers(seeded):
    client = client_for(seeded)
    for path in HTML_ROUTES:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-security-policy"] == APP_CSP, path
        assert resp.headers["x-content-type-options"] == "nosniff", path
        assert resp.headers["referrer-policy"] == "no-referrer", path
        assert resp.headers["x-frame-options"] == "DENY", path


def test_raw_snapshot_keeps_its_own_csp(seeded):
    client = client_for(seeded)
    resp = client.get("/api/snapshots/1/raw")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert csp == RAW_SNAPSHOT_CSP
    assert csp != APP_CSP
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"


def test_no_inline_script_or_style_on_any_page(seeded):
    client = client_for(seeded)
    pages = HTML_ROUTES + [
        "/posts/p1",
        "/snapshots/1",
        "/snapshots/1/source",
        "/snapshots/1/reparse",
        "/runs/1",
    ]
    for path in pages:
        html = client.get(path).text
        assert not INLINE_SCRIPT.search(html), f"inline <script> on {path}"
        assert not INLINE_STYLE.search(html), f"inline <style> on {path}"


# --- Errors -----------------------------------------------------------------


def test_html_500_carries_request_id_and_no_details(seeded, capsys, monkeypatch):
    monkeypatch.setattr(queries, "list_posts", make_app_boom)
    client = TestClient(build_app(seeded), raise_server_exceptions=False)
    resp = client.get("/posts")
    assert resp.status_code == 500
    assert "/tmp/leak" not in resp.text
    assert "SELECT" not in resp.text
    assert "RuntimeError" not in resp.text
    match = re.search(r"\b[0-9a-f]{16}\b", resp.text)
    assert match, "request id missing from the error page"
    request_id = match.group(0)
    err = capsys.readouterr().err
    assert request_id in err, "the id must also be on the server log"
    assert "/posts" in err, "the log must carry the request path"
    assert "Traceback" in err, "the full traceback goes to stderr only"


def test_api_500_is_json_with_request_id(seeded, monkeypatch):
    monkeypatch.setattr(queries, "list_posts", make_app_boom)
    client = TestClient(build_app(seeded), raise_server_exceptions=False)
    resp = client.get("/api/posts")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "internal"
    assert re.fullmatch(r"[0-9a-f]{16}", body["request_id"])
    assert "/tmp/leak" not in resp.text


# --- Access log -------------------------------------------------------------


def test_access_log_redacts_token_query_parameter(seeded, capsys):
    client = client_for(seeded)
    client.get("/posts?token=supersecret")
    err = capsys.readouterr().err
    assert "supersecret" not in err
    assert "token=%5BREDACTED%5D" in err


# --- Limits -----------------------------------------------------------------


def test_q_over_500_characters_returns_400(seeded):
    client = client_for(seeded)
    assert client.get("/posts?q=" + "x" * 600).status_code == 400
    assert client.get("/api/posts?q=" + "x" * 600).status_code == 400


def test_query_string_over_4kb_returns_400(seeded):
    client = client_for(seeded)
    assert client.get("/posts?filler=" + "x" * 5000).status_code == 400


# --- Read-only enforcement --------------------------------------------------


def test_every_route_returns_non_5xx_on_readonly_db(seeded):
    os.chmod(seeded, 0o444)
    try:
        client = client_for(seeded)
        for path in ALL_GET_ROUTES:
            resp = client.get(path)
            assert resp.status_code < 500, f"GET {path} -> {resp.status_code}"
        # State-changing routes are POST-only and refuse without the CSRF
        # token; they must fail loudly but never with a 5xx.
        for path in ("/crawl", "/crawl/stop"):
            resp = client.post(path, data={})
            assert resp.status_code < 500, f"POST {path} -> {resp.status_code}"
    finally:
        os.chmod(seeded, 0o644)
