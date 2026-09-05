"""Tests for `parser.parse_comments` (plans/v3/04).

Offline like the rest of the parser tests: bytes in, dataclasses out.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from crawler_social import parser

CAPTURED_AT = datetime(2026, 2, 4, 12, 0, 0, tzinfo=timezone.utc)
POST_ID = "777001"
FIXTURE = Path(__file__).parent / "fixtures" / "facebook_post_comments.html"


def load():
    return parser.parse_comments(FIXTURE.read_bytes(), POST_ID, CAPTURED_AT)


def test_comments_are_returned_in_document_order():
    comments, _ = load()
    assert [c.rank for c in comments] == [1, 2, 3, 4, 5]
    assert [c.author for c in comments] == [
        "Alice Nguyen",
        "Trần Minh",
        "Chris Doe",
        "Dana Lee",
        "Eve Pham",
    ]


def test_nested_replies_are_excluded_and_do_not_leak_into_the_parent():
    comments, _ = load()
    assert all("Bob Tran" != c.author for c in comments)
    top = comments[0]
    assert top.text == "This is the top comment and it is quite long."
    assert "reply" not in (top.text or "").lower()


def test_author_does_not_absorb_the_relative_age():
    """The aria-label reads "Comment by Alice Nguyen 2 hours ago"."""
    comments, _ = load()
    assert comments[0].author == "Alice Nguyen"


def test_ids_likes_and_times_are_extracted():
    comments, _ = load()
    assert comments[0].comment_id == "1001"
    assert comments[0].like_count == 128
    assert comments[0].published_at is not None
    # A Vietnamese label parses the same way as the English one.
    assert comments[1].comment_id == "1002"
    assert comments[1].like_count == 3


def test_a_comment_without_an_id_gets_a_stable_synthetic_one():
    first, _ = load()
    again, _ = load()
    synthetic = first[2].comment_id
    assert synthetic.startswith(f"{POST_ID}:h")
    assert synthetic == again[2].comment_id


def test_a_textless_comment_is_a_diagnostic_not_a_row():
    comments, diagnostics = load()
    assert all(c.text for c in comments)
    assert any("comment text" in d.reason for d in diagnostics)


def test_limit_truncates_to_the_top_n():
    comments, _ = parser.parse_comments(
        FIXTURE.read_bytes(), POST_ID, CAPTURED_AT, limit=2
    )
    assert [c.rank for c in comments] == [1, 2]


def test_comments_are_not_parsed_as_posts():
    """A permalink snapshot must yield one post, not one post per comment."""
    posts, _ = parser.parse(
        FIXTURE.read_bytes(), "https://www.facebook.com/ExamplePage", CAPTURED_AT
    )
    assert [p.post_id for p in posts] == [POST_ID]


def test_the_post_permalink_is_extracted_without_tracking_parameters():
    posts, _ = parser.parse(
        FIXTURE.read_bytes(), "https://www.facebook.com/ExamplePage", CAPTURED_AT
    )
    url = posts[0].url
    assert url == "https://www.facebook.com/ExamplePage/posts/777001"
    assert "__cft__" not in url and "__tn__" not in url


def test_empty_html_yields_nothing_and_does_not_raise():
    comments, diagnostics = parser.parse_comments(b"", POST_ID, CAPTURED_AT)
    assert comments == []
    assert diagnostics == [] or all(d.reason for d in diagnostics)
