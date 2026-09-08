"""Tests for the JSON API."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_config, make_db


@pytest.fixture()
def seeded(db_file: Path) -> Path:
    make_db(
        db_file,
        posts=[
            # 60 ordinary posts across two pages, one per calendar day.
            *[
                (
                    f"p{i:03d}",
                    "https://a.example" if i % 2 == 0 else "https://b.example",
                    f"post number {i} body",
                    f"author{i % 3}",
                    f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}T10:00:00+00:00",
                    f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}T09:00:00+00:00",
                )
                for i in range(60)
            ],
            (
                "12/3%4", "https://a.example", "<b>bold</b> & text",
                "Ann", "2026-01-05T10:00:00+00:00", "2026-01-05T09:00:00+00:00",
            ),
        ],
        runs=[
            ("2026-01-01T00:00:00+00:00", "completed", "2026-01-01T00:05:00+00:00", None),
            ("2026-01-02T00:00:00+00:00", "failed", "2026-01-02T00:05:00+00:00", "boom"),
        ],
        snapshots=[
            (0, "https://a.example", "2026-01-01T00:01:00+00:00", b"<html>a</html>"),
            (0, "https://b.example", "2026-01-01T00:02:00+00:00", b"<html>b</html>"),
            (1, "https://a.example", "2026-01-02T00:01:00+00:00", b"<html>c</html>"),
        ],
        states=[("https://a.example", "p000", "2026-01-01T10:00:00+00:00", "2026-01-01T00:05:00+00:00")],
    )
    return db_file


@pytest.fixture()
def client(seeded: Path) -> TestClient:
    from crawler_social.server.app import create_app

    return TestClient(create_app(make_config(seeded)))


def test_summary_matches_seed(client):
    body = client.get("/api/summary").json()
    assert body["total_posts"] == 61
    assert body["total_snapshots"] == 3
    assert body["total_runs"] == 2
    assert body["last_run"]["status"] == "failed"
    assert body["last_run"]["error"] == "boom"
    assert body["total_snapshot_bytes"] == len(b"<html>a</html>") + len(
        b"<html>b</html>"
    ) + len(b"<html>c</html>")
    assert body["newest_post_at"] == "2026-03-04T10:00:00+00:00"  # i = 59


def test_posts_default_limit_50_newest_first(client):
    body = client.get("/api/posts").json()
    assert body["limit"] == 50
    assert body["total"] == 61
    assert len(body["items"]) == 50
    times = [i["published_at"] for i in body["items"]]
    assert times == sorted(times, reverse=True)


def test_limit_and_offset_validation(client):
    assert client.get("/api/posts", params={"limit": 201}).status_code == 422
    assert client.get("/api/posts", params={"limit": 0}).status_code == 422
    assert client.get("/api/posts", params={"offset": -1}).status_code == 422


def test_q_filters_and_total_reflects_filter(client):
    body = client.get("/api/posts", params={"q": "number 7 "}).json()
    assert body["total"] == len(body["items"])
    assert body["total"] < 61
    assert all("number 7" in item["text"] for item in body["items"])


def test_page_url_filter(client):
    body = client.get("/api/posts", params={"page_url": "https://a.example"}).json()
    assert body["total"] == 31  # 30 even-indexed + the escaped-id post
    assert all(i["page_url"] == "https://a.example" for i in body["items"])


def test_since_excludes_older_and_bad_value_422(client):
    body = client.get("/api/posts", params={"since": "2026-01-04"}).json()
    assert body["total"] >= 1
    assert all(i["published_at"] >= "2026-01-04T00:00:00+00:00" for i in body["items"])
    resp = client.get("/api/posts", params={"since": "not-a-date"})
    assert resp.status_code == 422
    assert "since" in resp.text


def test_until_end_of_day_is_inclusive(client):
    body = client.get(
        "/api/posts", params={"since": "2026-01-01", "until": "2026-01-01"}
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["post_id"] == "p000"


def test_order_validation(client):
    body = client.get("/api/posts", params={"order": "oldest"}).json()
    times = [i["published_at"] for i in body["items"]]
    assert times == sorted(times)
    assert client.get("/api/posts", params={"order": "sideways"}).status_code == 422


def test_two_windows_cover_wide_window(client):
    wide = client.get("/api/posts", params={"limit": 200}).json()
    first = client.get("/api/posts", params={"limit": 50, "offset": 0}).json()
    second = client.get("/api/posts", params={"limit": 50, "offset": 50}).json()
    first_ids = [i["post_id"] for i in first["items"]]
    second_ids = [i["post_id"] for i in second["items"]]
    assert first_ids == [i["post_id"] for i in wide["items"]][:50]
    assert second_ids == [i["post_id"] for i in wide["items"]][50:100]
    assert not set(first_ids) & set(second_ids)


def test_get_post_and_unknown_404(client):
    resp = client.get("/api/posts/p001")
    assert resp.status_code == 200
    assert resp.json()["text"] == "post number 1 body"
    resp = client.get("/api/posts/nope")
    assert resp.status_code == 404
    assert resp.json()["error"] == "post_not_found"


def test_post_id_with_slash_and_percent_round_trips(client):
    resp = client.get(f"/api/posts/{quote('12/3%4', safe='')}")
    assert resp.status_code == 200
    assert resp.json()["post_id"] == "12/3%4"
    assert resp.json()["text"] == "<b>bold</b> & text"


def test_pages_endpoint(client):
    pages = client.get("/api/pages").json()
    by_url = {p["page_url"]: p for p in pages}
    assert by_url["https://a.example"]["post_count"] == 31
    assert by_url["https://b.example"]["post_count"] == 30


def test_runs_endpoint(client):
    body = client.get("/api/runs").json()
    assert body["total"] == 2
    assert [r["id"] for r in body["items"]] == [2, 1]
    assert body["items"][0]["snapshot_count"] == 1


def test_run_detail_includes_snapshots_and_404(client):
    body = client.get("/api/runs/1").json()
    assert body["status"] == "completed"
    assert [s["size_bytes"] for s in body["snapshots"]] == [
        len(b"<html>a</html>"),
        len(b"<html>b</html>"),
    ]
    assert client.get("/api/runs/99").status_code == 404


def test_snapshots_metadata_only(client):
    resp = client.get("/api/snapshots")
    body = resp.json()
    assert body["total"] == 3
    for item in body["items"]:
        assert "size_bytes" in item
        assert "html" not in item
    assert b"<html>" not in resp.content


def test_run_id_filter_on_snapshots(client):
    body = client.get("/api/snapshots", params={"run_id": 2}).json()
    assert body["total"] == 1
    assert body["items"][0]["run_id"] == 2


def test_state_endpoint(client):
    state = client.get("/api/state").json()
    assert len(state) == 1
    assert state[0]["last_post_id"] == "p000"


def test_export_csv_filtered_with_header_and_disposition(client):
    resp = client.get(
        "/api/export/posts.csv", params={"page_url": "https://b.example"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == 'attachment; filename="posts.csv"'
    text = resp.text
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == [
        "post_id", "page_url", "text", "author", "published_at",
        "first_seen", "last_seen",
    ]
    data = rows[1:]
    assert len(data) == 30
    assert all(row[1] == "https://b.example" for row in data)


def seed_comments(db_file: Path) -> None:
    from crawler_social import db

    conn = db.connect(db_file)
    try:
        with db.transaction(conn):
            db.upsert_comment(conn, "c2", "p001", "u", "Bea", "second", None, 2, 2)
            db.upsert_comment(conn, "c1", "p001", "u", "Ann", "first", None, 9, 1)
            db.upsert_comment(conn, "cx", "12/3%4", "u", "Cid", "escaped", None, 0, 1)
    finally:
        conn.close()


def test_comments_endpoint_is_ordered_and_paged(seeded: Path):
    from crawler_social.server.app import create_app

    seed_comments(seeded)
    client = TestClient(create_app(make_config(seeded)))
    body = client.get("/api/posts/p001/comments").json()
    assert body["total"] == 2
    assert [c["comment_id"] for c in body["items"]] == ["c1", "c2"]
    assert body["items"][0]["author"] == "Ann"


def test_comments_route_wins_over_the_greedy_post_id_converter(seeded: Path):
    """/posts/{post_id:path} would otherwise swallow the trailing segment."""
    from crawler_social.server.app import create_app

    seed_comments(seeded)
    client = TestClient(create_app(make_config(seeded)))
    resp = client.get(f"/api/posts/{quote('12/3%4', safe='')}/comments")
    assert resp.status_code == 200
    assert [c["comment_id"] for c in resp.json()["items"]] == ["cx"]


def test_comments_come_back_threaded_with_each_reply_under_its_parent(seeded: Path):
    from crawler_social import db
    from crawler_social.server.app import create_app

    conn = db.connect(seeded)
    try:
        with db.transaction(conn):
            db.upsert_comment(conn, "c2", "p001", "u", "Bea", "second", None, 0, 2)
            db.upsert_comment(conn, "c1", "p001", "u", "Ann", "first", None, 0, 1)
            # Inserted last and named "r1": neither insertion order nor id
            # sorting would put it where it belongs on its own.
            db.upsert_comment(
                conn, "r1", "p001", "u", "Cy", "reply to Ann", None, 0, 1,
                parent_comment_id="c1",
            )
    finally:
        conn.close()

    client = TestClient(create_app(make_config(seeded)))
    items = client.get("/api/posts/p001/comments").json()["items"]
    assert [(c["author"], c["parent_comment_id"]) for c in items] == [
        ("Ann", None),
        ("Cy", "c1"),
        ("Bea", None),
    ]


def test_comments_for_a_post_without_any_are_empty_not_404(client):
    body = client.get("/api/posts/p002/comments").json()
    assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}

