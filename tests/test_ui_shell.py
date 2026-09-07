"""Tests for the UI shell."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_config, make_db


@pytest.fixture()
def client(db_file: Path) -> TestClient:
    make_db(
        db_file,
        posts=[
            ("p1", "https://a.example", "a normal post about cats", "Ann",
             "2026-01-02T10:00:00+00:00", "2026-01-02T09:00:00+00:00"),
            ("p2", "https://a.example", "<script>alert(1)</script> sneaky",
             None, None, "2026-01-01T09:00:00+00:00"),
        ],
        runs=[("2026-01-01T00:00:00+00:00", "completed", "2026-01-01T00:05:00+00:00", None)],
    )
    from crawler_social.server.app import create_app

    return TestClient(create_app(make_config(db_file)))


def test_home_renders_post_count(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert "2" in resp.text
    assert "posts stored" in resp.text


def test_home_missing_database_renders_empty_state(tmp_path: Path):
    from crawler_social.server.app import create_app

    client = TestClient(create_app(make_config(tmp_path / "missing.db")))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No database yet" in resp.text
    assert "uv run crawler crawl" in resp.text


def test_home_empty_database_renders_empty_state(tmp_path: Path):
    make_db(tmp_path / "empty.db")
    from crawler_social.server.app import create_app

    client = TestClient(create_app(make_config(tmp_path / "empty.db")))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No posts stored yet" in resp.text


def test_static_css_served(client):
    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/css")


def test_static_js_served(client):
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/javascript"
    ) or resp.headers["content-type"].startswith("text/javascript")


def test_post_text_renders_escaped(client):
    resp = client.get("/")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in resp.text
    assert "<script>alert" not in resp.text


def test_unknown_html_path_is_404_page(client):
    resp = client.get("/definitely-not-a-page")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "Not found" in resp.text


def test_unknown_api_path_is_json(client):
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "error" in resp.json()


def test_no_external_urls_referenced(client):
    for path in ("/", "/definitely-not-a-page"):
        html = client.get(path).text
        assert 'href="http' not in html
        assert 'src="http' not in html
        assert "http://cdn" not in html
        assert "https://fonts" not in html


def test_no_inline_script_or_style(client):
    html = client.get("/").text
    assert "<script>" not in html  # only <script src=...>
    assert "<style" not in html
