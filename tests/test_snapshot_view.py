"""Required tests from plans/v2/06-post-detail-and-snapshots.md."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_config, make_db

FIXTURE = Path(__file__).parent / "fixtures" / "facebook_page_sample.html"
PAGE_URL = "https://www.facebook.com/ExamplePublicPage"
# The bytes a capture returns. They are hashed and measured on the way in,
# never stored, so this only ever reaches the database as a size.
RAW_SNAPSHOT = (
    b'<html lang="xx-zz"><script>track("x")</script>'
    b'<body class="captured">' + FIXTURE.read_bytes() + b"</body></html>"
)


@pytest.fixture()
def client(db_file: Path) -> TestClient:
    make_db(
        db_file,
        posts=[
            (
                "p1", PAGE_URL, "hello world", "Ann",
                "2026-02-04T11:00:00+00:00", "2026-02-04T11:00:00+00:00",
            ),
            (
                "p2", PAGE_URL, "</pre><script>alert(1)</script> sneaky",
                None, None, "2026-02-05T09:00:00+00:00",
            ),
        ],
        runs=[("2026-02-04T11:30:00+00:00", "completed", "2026-02-04T11:35:00+00:00", None)],
        snapshots=[(0, PAGE_URL, "2026-02-04T11:40:00+00:00", RAW_SNAPSHOT)],
    )
    from crawler_social.server.app import create_app

    return TestClient(create_app(make_config(db_file)))


def test_post_detail_renders_every_field_and_full_text(client: TestClient):
    resp = client.get("/posts/p1")
    assert resp.status_code == 200
    for label in ("Post ID", "Page", "Author", "Published (UTC)",
                  "First seen", "Last seen"):
        assert label in resp.text
    assert "hello world" in resp.text
    assert "2026-02-04T11:00:00+00:00" in resp.text
    assert "ago)" in resp.text  # relative age beside the seen timestamps
    assert 'data-copy-target="#post-text"' in resp.text


def test_post_text_with_markup_stays_escaped_in_pre(client: TestClient):
    resp = client.get("/posts/p2")
    assert resp.status_code == 200
    assert "&lt;/pre&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in resp.text
    assert "</pre><script>alert(1)</script>" not in resp.text


def test_unknown_post_is_404_page(client: TestClient):
    resp = client.get("/posts/zzz-unknown")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    assert "Not found" in resp.text


def test_snapshots_list_is_metadata_only(client: TestClient):
    resp = client.get("/snapshots")
    assert resp.status_code == 200
    assert b"777001" not in resp.content  # a post id that only exists in the blob
    assert b"xx-zz" not in resp.content  # the blob's own <html> tag
    assert "#1" in resp.text


def test_snapshots_list_filters(client: TestClient):
    assert client.get("/snapshots", params={"run_id": 1}).status_code == 200
    empty = client.get("/snapshots", params={"run_id": 999})
    assert empty.status_code == 200
    # The wording now distinguishes "filtered to nothing" from
    # "nothing stored at all", which used to read the same.
    assert "No snapshots match these filters." in empty.text
    assert "Clear filters" in empty.text


def test_snapshot_detail_shows_the_capture_record(client: TestClient):
    resp = client.get("/snapshots/1")
    assert resp.status_code == 200
    assert "Captured size" in resp.text
    assert "SHA-256" in resp.text
    # Nothing renders captured markup any more, so there is no iframe to
    # sandbox and no raw route to point one at.
    assert "<iframe" not in resp.text
    assert "/api/snapshots/1/raw" not in resp.text


def test_unknown_snapshot_page_is_404_page(client: TestClient):
    resp = client.get("/snapshots/999")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
