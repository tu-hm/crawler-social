"""Tests for the posts viewer.

The posts table link test (every /posts/{id} URL returns 200) only goes
green once the post detail route exists; everything else stands on its
own.
"""

from __future__ import annotations

import html as html_mod
import re
from pathlib import Path
from urllib.parse import parse_qsl, quote

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_config, make_db

N_POSTS = 30


def _seed_posts() -> list[tuple]:
    posts = []
    for i in range(N_POSTS):
        page_url = "https://a.example" if i % 2 == 0 else "https://b.example"
        if i == 0:
            text = "fish & chips friday"
        elif i % 3 == 0:
            text = f"kubernetes tips {i}"
        else:
            text = f"plain update {i}"
        published = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}T10:00:00+00:00"
        posts.append(
            (
                f"post-{i:03d}", page_url, text, f"Author {i}",
                published, "2026-03-01T08:00:00+00:00",
            )
        )
    return posts


@pytest.fixture()
def client(db_file: Path) -> TestClient:
    make_db(db_file, posts=_seed_posts())
    from crawler_social.server.app import create_app

    return TestClient(create_app(make_config(db_file)))


def _post_hrefs(html: str) -> list[str]:
    return [
        html_mod.unescape(h) for h in re.findall(r'href="(/posts/[^"?]+)"', html)
    ]


def test_posts_lists_seeded_posts_newest_first(client: TestClient):
    resp = client.get("/posts")
    assert resp.status_code == 200
    assert "30 posts" in resp.text
    # post-029 holds the newest published_at (2026-02-02).
    newest = resp.text.index("2026-02-02T10:00:00+00:00")
    middle = resp.text.index("2026-01-28T10:00:00+00:00")
    oldest = resp.text.index("2026-01-01T10:00:00+00:00")
    assert newest < middle < oldest
    assert re.search(r'title="[^"]*\d[smhdw] ago"', resp.text)


def test_q_filter_narrows_rows_and_reports_count(client: TestClient):
    resp = client.get("/posts", params={"q": "kubernetes"})
    assert resp.status_code == 200
    assert "9 posts" in resp.text
    assert "<mark>kubernetes</mark> tips 27" in resp.text
    assert "plain update 2" not in resp.text


def test_q_term_is_wrapped_in_mark(client: TestClient):
    resp = client.get("/posts", params={"q": "kubernetes"})
    assert "<mark>kubernetes</mark>" in resp.text


def test_q_injection_is_escaped_and_mark_stays_escaped(client: TestClient):
    resp = client.get("/posts", params={"q": "<img src=x onerror=1>"})
    assert resp.status_code == 200
    assert "&lt;img src=x onerror=1&gt;" in resp.text
    assert "<img src=x" not in resp.text
    assert "<mark>" not in resp.text

    # An escaped separator inside a match must stay escaped around <mark>.
    resp = client.get("/posts", params={"q": "fish"})
    assert "<mark>fish</mark> &amp; chips friday" in resp.text
    assert "&<mark>" not in resp.text


def test_page_url_filter_narrows_to_one_page(client: TestClient):
    resp = client.get("/posts", params={"page_url": "https://a.example"})
    assert resp.status_code == 200
    assert "15 posts" in resp.text
    assert "plain update 1</a>" not in resp.text  # i=1 lives on b.example
    assert "kubernetes tips 6" in resp.text  # i=6 lives on a.example


def test_second_window_with_limit_and_offset(client: TestClient):
    resp = client.get("/posts", params={"limit": 25, "offset": 25})
    assert resp.status_code == 200
    assert "showing 26–30 of 30" in resp.text
    assert resp.text.count("View →") == 5


def test_pagination_links_carry_q_and_page_url_forward(client: TestClient):
    page_url = quote("https://a.example", safe="")
    resp = client.get(f"/posts?q=plain&page_url={page_url}&limit=5")
    assert resp.status_code == 200
    next_hrefs = [
        html_mod.unescape(href)
        for href in re.findall(r'href="(/posts\?[^"]+)"', resp.text)
        if "offset=5" in href
    ]
    assert next_hrefs, "expected a next-page link"
    carried = dict(parse_qsl(next_hrefs[0].split("?", 1)[1]))
    assert carried["q"] == "plain"
    assert carried["page_url"] == "https://a.example"
    assert carried["limit"] == "5"


def test_no_matches_state_differs_from_empty_database(
    client: TestClient, tmp_path: Path
):
    resp = client.get("/posts", params={"q": "zzzz"})
    assert resp.status_code == 200
    assert "No posts match these filters." in resp.text
    assert 'href="/posts">Clear filters</a>' in resp.text
    assert "No posts stored yet" not in resp.text

    make_db(tmp_path / "empty.db")
    from crawler_social.server.app import create_app

    empty_client = TestClient(create_app(make_config(tmp_path / "empty.db")))
    resp = empty_client.get("/posts")
    assert resp.status_code == 200
    assert "No posts stored yet." in resp.text
    assert "uv run crawler crawl" in resp.text
    assert "No posts match these filters." not in resp.text


def test_missing_database_renders_no_posts_stored_state(tmp_path: Path):
    from crawler_social.server.app import create_app

    client = TestClient(create_app(make_config(tmp_path / "missing.db")))
    resp = client.get("/posts")
    assert resp.status_code == 200
    assert "No posts stored yet." in resp.text
    assert "uv run crawler crawl" in resp.text


def test_csv_link_query_equals_page_query(client: TestClient):
    query = "q=plain&page_url=https%3A%2F%2Fa.example&limit=5"
    resp = client.get(f"/posts?{query}")
    matches = re.findall(
        r'href="(/api/export/posts\.csv\?[^"]+)"', resp.text
    )
    assert matches, "CSV download link missing"
    csv_query = html_mod.unescape(matches[0]).split("?", 1)[1]
    assert sorted(parse_qsl(csv_query)) == sorted(parse_qsl(query))


def test_every_post_links_to_a_200_detail_page(client: TestClient):
    resp = client.get("/posts", params={"limit": 200})
    hrefs = set(_post_hrefs(resp.text))
    assert len(hrefs) == N_POSTS  # excerpt link + view link dedupe to one each
    for href in hrefs:
        detail = client.get(href)
        assert detail.status_code == 200, href


def test_active_filter_chips_offer_per_filter_removal(client: TestClient):
    resp = client.get(
        "/posts", params={"q": "plain", "page_url": "https://a.example"}
    )
    chip_urls = re.findall(
        r'filter-chip">[^<]*<a href="([^"]+)"', resp.text
    )
    assert len(chip_urls) == 2
    without_q = dict(parse_qsl(chip_urls[0].split("?", 1)[1])) if "?" in chip_urls[0] else {}
    without_page = dict(parse_qsl(chip_urls[1].split("?", 1)[1])) if "?" in chip_urls[1] else {}
    assert "q" not in without_q and without_q.get("page_url") == "https://a.example"
    assert "page_url" not in without_page and without_page.get("q") == "plain"


def test_order_oldest_reverses_rows(client: TestClient):
    resp = client.get("/posts", params={"order": "oldest"})
    assert resp.text.index("2026-01-01T10:00:00+00:00") < resp.text.index(
        "2026-02-02T10:00:00+00:00"
    )


def test_date_filters_apply_on_html_route(client: TestClient):
    resp = client.get("/posts", params={"since": "2026-02-01"})
    assert "2 posts" in resp.text
    assert "2026-02-02T10:00:00+00:00" in resp.text

    resp = client.get(
        "/posts", params={"since": "2026-02-01", "until": "2026-02-28"}
    )
    assert "2 posts" in resp.text


def test_invalid_html_params_fall_back_instead_of_422(client: TestClient):
    resp = client.get("/posts", params={"since": "not-a-date"})
    assert resp.status_code == 200
    assert "30 posts" in resp.text

    resp = client.get("/posts", params={"limit": "banana"})
    assert resp.status_code == 200
    assert "30 posts" in resp.text


def test_post_detail_shows_stored_comments_in_rank_order(db_file: Path):
    from crawler_social import db
    from crawler_social.server.app import create_app

    make_db(db_file, posts=_seed_posts())
    conn = db.connect(db_file)
    try:
        with db.transaction(conn):
            db.upsert_comment(
                conn, "c2", "post-000", "u", "Bea", "later reply", None, 2, 2
            )
            db.upsert_comment(
                conn, "c1", "post-000", "u", "Ann", "top reply", None, 9, 1
            )
    finally:
        conn.close()

    client = TestClient(create_app(make_config(db_file)))
    page = client.get("/posts/post-000").text
    assert "Comments" in page
    assert page.index("top reply") < page.index("later reply")
    assert "Ann" in page and "9 reactions" in page


def test_post_detail_hides_the_panel_when_there_are_no_comments(client: TestClient):
    page = client.get("/posts/post-001").text
    assert "comment-list" not in page


def test_post_detail_escapes_comment_text(db_file: Path):
    from crawler_social import db
    from crawler_social.server.app import create_app

    make_db(db_file, posts=_seed_posts())
    conn = db.connect(db_file)
    try:
        with db.transaction(conn):
            db.upsert_comment(
                conn, "c1", "post-000", "u", "<script>x</script>",
                "<img src=x onerror=alert(1)>", None, None, 1,
            )
    finally:
        conn.close()

    client = TestClient(create_app(make_config(db_file)))
    page = client.get("/posts/post-000").text
    assert "<script>x</script>" not in page
    assert "onerror=alert(1)&gt;" in page or "&lt;img src=x" in page

