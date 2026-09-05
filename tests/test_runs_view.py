"""Required tests from plans/v2/07-runs-dashboard.md."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_config, make_db

PAGE_URL = "https://a.example"
NOW = datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@pytest.fixture()
def runs_client(db_file: Path) -> TestClient:
    make_db(
        db_file,
        posts=[
            # first seen during run 1's window
            ("p1", PAGE_URL, "caught by run one", "Ann", None,
             iso(NOW - timedelta(hours=3) + timedelta(minutes=1))),
            # first seen long before any run
            ("p2", PAGE_URL, "ancient", "Bo", None,
             iso(NOW - timedelta(days=20))),
        ],
        runs=[
            (iso(NOW - timedelta(hours=3)), "completed",
             iso(NOW - timedelta(hours=3) + timedelta(minutes=5)), None),
            (iso(NOW - timedelta(hours=2)), "failed",
             iso(NOW - timedelta(hours=2) + timedelta(minutes=1)),
             "boom: captcha wall"),
            (iso(NOW - timedelta(hours=1)), "interrupted",
             iso(NOW - timedelta(hours=1) + timedelta(seconds=30)), None),
            (iso(NOW - timedelta(minutes=1)), "running", None, None),
        ],
        snapshots=[(0, PAGE_URL, iso(NOW - timedelta(hours=3) + timedelta(minutes=2)), b"<div>cap</div>")],
    )
    from crawler_social.server.app import create_app

    return TestClient(create_app(make_config(db_file)))


def test_runs_listed_newest_first_with_statuses(runs_client: TestClient):
    resp = runs_client.get("/runs")
    assert resp.status_code == 200
    first = resp.text.index('href="/runs/4"')
    last = resp.text.index('href="/runs/1"')
    assert first < last  # newest first
    for status in ("completed", "failed", "interrupted", "running"):
        assert f"badge-{status}" in resp.text
    assert "boom: captcha wall" in resp.text


def test_stale_running_run_is_flagged_but_not_modified(
    runs_client: TestClient, db_file: Path
):
    make_db(
        db_file,
        runs=[
            (iso(NOW - timedelta(hours=2)), "running", None, None),
            (iso(NOW - timedelta(minutes=1)), "running", None, None),
        ],
    )
    resp = runs_client.get("/runs")
    assert resp.status_code == 200
    assert ">running (stale)</span>" in resp.text
    assert resp.text.count(">running (stale)</span>") == 1  # fresh run not flagged
    assert ">running</span>" in resp.text
    assert "almost certainly died" in resp.text  # the explanation
    # The stored row is untouched: status stays exactly "running".
    from crawler_social import queries

    conn = queries.connect_ro(db_file)
    try:
        rows = queries.list_runs(conn, limit=10)[0]
    finally:
        conn.close()
    running = [r for r in rows if r["status"] == "running"]
    assert len(running) == 3  # the stale one plus the fixture's two fresh ones
    assert all(r["finished_at"] is None for r in running)  # rows left as stored


def test_run_detail_shows_snapshots_and_approximate_yield(runs_client: TestClient):
    resp = runs_client.get("/runs/1")
    assert resp.status_code == 200
    assert "approximate" in resp.text.lower()
    assert 'href="/snapshots/1"' in resp.text
    assert "/posts/p1" in resp.text  # first_seen inside the window
    assert "caught by run one" in resp.text
    assert "ancient" not in resp.text  # first_seen outside the window


def test_run_detail_unknown_id_is_404_page(runs_client: TestClient):
    resp = runs_client.get("/runs/999")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]


def test_state_lists_watermarks_and_links_existing_posts(
    runs_client: TestClient, db_file: Path
):
    make_db(
        db_file,
        states=[
            (PAGE_URL, "p1", iso(NOW - timedelta(hours=3)), iso(NOW - timedelta(hours=3))),
            ("https://b.example", "ghost-id", iso(NOW - timedelta(days=2)), iso(NOW - timedelta(days=2))),
        ],
    )
    resp = runs_client.get("/state")
    assert resp.status_code == 200
    assert 'href="/posts/p1"' in resp.text  # existing post is linked
    assert "post no longer stored" in resp.text  # unknown id stays plain text


def test_home_health_panel_counts_from_first_seen(runs_client: TestClient, db_file: Path):
    # The fixture already stored p1 (first seen 3h ago) and p2 (20d ago);
    # make_db appends to the same file, so both sets count.
    make_db(
        db_file,
        posts=[
            ("r1", PAGE_URL, "fresh", None, None, iso(NOW - timedelta(hours=1))),
            ("r2", PAGE_URL, "this week", None, None, iso(NOW - timedelta(days=3))),
            ("r3", PAGE_URL, "old", None, None, iso(NOW - timedelta(days=10))),
        ],
    )
    resp = runs_client.get("/")
    assert resp.status_code == 200
    count_24h = re.search(r"last 24 hours</dt>\s*<dd>(\d+)</dd>", resp.text)
    count_7d = re.search(r"last 7 days</dt>\s*<dd>(\d+)</dd>", resp.text)
    assert count_24h and count_24h.group(1) == "2"  # p1 + r1
    assert count_7d and count_7d.group(1) == "3"  # p1 + r1 + r2


def test_staleness_warning_appears_at_8_days_not_2(tmp_path: Path):
    from crawler_social.server.app import create_app

    for age_days, expected in ((8, True), (2, False)):
        db_path = tmp_path / f"age{age_days}.db"
        make_db(
            db_path,
            posts=[
                ("only", PAGE_URL, "hello", "Ann",
                 iso(NOW - timedelta(days=age_days)), iso(NOW - timedelta(days=age_days))),
            ],
        )
        client = TestClient(create_app(make_config(db_path)))
        resp = client.get("/")
        warning = "usually means the parser broke" in resp.text
        assert warning is expected, f"age {age_days} days: warning={warning}"


def test_chart_renders_one_bar_per_day_with_posts(runs_client: TestClient, db_file: Path):
    # make_db appends: the db already holds p1 (3h ago) and p2 (20d ago).
    today = NOW.replace(hour=12, minute=0, second=0, microsecond=0)
    seeded_first_seen = [
        NOW - timedelta(hours=3) + timedelta(minutes=1),  # p1
        today,  # d1
        today - timedelta(hours=2),  # d2
        today - timedelta(days=1),  # d3
        # d4 is 40 days old and falls outside the 30-day chart
    ]
    expected: dict[str, int] = {}
    for dt in [*seeded_first_seen, NOW - timedelta(days=20)]:  # p2
        key = dt.date().isoformat()
        expected[key] = expected.get(key, 0) + 1

    make_db(
        db_file,
        posts=[
            ("d1", PAGE_URL, "today a", None, None, iso(today)),
            ("d2", PAGE_URL, "today b", None, None, iso(today - timedelta(hours=2))),
            ("d3", PAGE_URL, "yesterday", None, None, iso(today - timedelta(days=1))),
            ("d4", PAGE_URL, "too old", None, None, iso(today - timedelta(days=40))),
        ],
    )
    resp = runs_client.get("/")
    assert resp.status_code == 200
    assert "<svg" in resp.text
    svg = resp.text.split("<svg", 1)[1].split("</svg>", 1)[0]
    bars = {}
    for title in re.findall(r"<title>([^<]+)</title>", svg):
        day, count = title.split(": ")
        bars[day] = int(count.split()[0])
    assert bars == expected


def test_chart_empty_state_when_no_recent_posts(tmp_path: Path):
    from crawler_social.server.app import create_app

    make_db(
        tmp_path / "nochart.db",
        posts=[
            ("old", PAGE_URL, "ancient", None, None, iso(NOW - timedelta(days=100))),
        ],
    )
    client = TestClient(create_app(make_config(tmp_path / "nochart.db")))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No posts in the last 30 days." in resp.text
    assert "<rect" not in resp.text
    assert "<svg" not in resp.text


def test_no_route_opens_a_writable_connection(runs_client: TestClient, db_file: Path):
    # Make the database read-only on disk: any write attempt anywhere in
    # these routes would fail loudly instead of silently succeeding.
    os.chmod(db_file, 0o444)
    try:
        assert runs_client.get("/runs").status_code == 200
        assert runs_client.get("/runs/1").status_code == 200
        assert runs_client.get("/state").status_code == 200
        assert runs_client.get("/").status_code == 200
        assert runs_client.get("/posts").status_code == 200
        assert runs_client.get("/snapshots").status_code == 200
    finally:
        os.chmod(db_file, 0o644)
