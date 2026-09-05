"""Required tests from plans/v2/06-post-detail-and-snapshots.md."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_config, make_db

FIXTURE = Path(__file__).parent / "fixtures" / "facebook_page_sample.html"
PAGE_URL = "https://www.facebook.com/ExamplePublicPage"
# The stored capture, as a full document: the markers (xx-zz lang, inline
# script) must never appear unescaped or outside the sandboxed raw route.
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
    assert "No snapshots match." in empty.text


def test_snapshot_detail_embeds_sandboxed_iframe(client: TestClient):
    resp = client.get("/snapshots/1")
    assert resp.status_code == 200
    assert '<iframe class="snapshot-frame"' in resp.text
    assert 'sandbox=""' in resp.text
    assert "allow-scripts" not in resp.text
    assert 'src="/api/snapshots/1/raw"' in resp.text
    assert 'referrerpolicy="no-referrer"' in resp.text
    assert 'loading="lazy"' in resp.text


def test_raw_serves_exact_stored_bytes(client: TestClient):
    resp = client.get("/api/snapshots/1/raw")
    assert resp.status_code == 200
    assert resp.content == RAW_SNAPSHOT


def test_raw_carries_sandbox_headers(client: TestClient):
    resp = client.get("/api/snapshots/1/raw")
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    assert (
        resp.headers["content-security-policy"]
        == "default-src 'none'; style-src 'unsafe-inline'"
    )
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["cache-control"] == "no-store"


def test_download_is_attachment_naming_the_id(client: TestClient):
    resp = client.get("/api/snapshots/1/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="snapshot-1-')
    assert disposition.endswith('.html"')
    assert resp.content == RAW_SNAPSHOT


def test_source_view_escapes_the_markup(client: TestClient):
    resp = client.get("/snapshots/1/source")
    assert resp.status_code == 200
    assert '&lt;html lang=&#34;xx-zz&#34;&gt;' in resp.text
    assert '<html lang="xx-zz"' not in resp.text  # no raw markup from the snapshot
    assert "&lt;script&gt;track(&#34;x&#34;)&lt;/script&gt;" in resp.text
    assert "<script>track" not in resp.text
    assert 'class="ln"' in resp.text  # line numbers


def test_reparse_reports_parser_results_and_writes_nothing(
    client: TestClient, db_file: Path
):
    from crawler_social import queries

    before = queries.summary(queries.connect_ro(db_file))["total_posts"]
    resp = client.get("/snapshots/1/reparse")
    assert resp.status_code == 200
    assert "Posts found: 5" in resp.text  # same as the parser tests expect
    assert "777001" in resp.text
    assert "777004" in resp.text
    assert "Read-only" in resp.text
    after = queries.summary(queries.connect_ro(db_file))["total_posts"]
    assert after == before  # nothing was written


def test_unknown_snapshot_raw_is_404(client: TestClient):
    resp = client.get("/api/snapshots/999/raw")
    assert resp.status_code == 404
    assert resp.json() == {"error": "snapshot_not_found"}


def test_unknown_snapshot_page_is_404_page(client: TestClient):
    resp = client.get("/snapshots/999")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
