"""Tests for `parser.parse_comments`.

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


def top_level(comments):
    return [c for c in comments if c.parent_comment_id is None]


def test_comments_are_returned_in_document_order():
    comments, _ = load()
    tops = top_level(comments)
    assert [c.rank for c in tops] == [1, 2, 3, 4, 5]
    assert [c.author for c in tops] == [
        "Alice Nguyen",
        "Trần Minh",
        "Chris Doe",
        "Dana Lee",
        "Eve Pham",
    ]


def test_a_reply_is_threaded_under_its_parent_and_ranks_among_siblings():
    comments, _ = load()
    reply = next(c for c in comments if c.author == "Bob Tran")
    assert reply.parent_comment_id == "1001"
    # Rank 1 among the replies to 1001, not rank 1 on the post.
    assert reply.rank == 1
    assert reply.comment_id == "2001"
    # A reply follows its parent, so a viewer rendering in order nests it.
    assert comments.index(reply) == comments.index(comments[0]) + 1


def test_a_reply_does_not_leak_into_its_parent():
    comments, _ = load()
    top = comments[0]
    assert top.author == "Alice Nguyen"
    assert top.text == "This is the top comment and it is quite long."
    assert "reply" not in (top.text or "").lower()


def test_replies_can_be_excluded():
    comments, _ = parser.parse_comments(
        FIXTURE.read_bytes(), POST_ID, CAPTURED_AT, include_replies=False
    )
    assert all(c.parent_comment_id is None for c in comments)
    assert all(c.author != "Bob Tran" for c in comments)
    assert [c.rank for c in comments] == [1, 2, 3, 4, 5]


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
    assert top_level(comments)[1].comment_id == "1002"
    assert top_level(comments)[1].like_count == 3


def test_a_comment_without_an_id_gets_a_stable_synthetic_one():
    first, _ = load()
    again, _ = load()
    synthetic = top_level(first)[2].comment_id
    assert synthetic.startswith(f"{POST_ID}:h")
    assert synthetic == top_level(again)[2].comment_id


def test_a_textless_comment_is_a_diagnostic_not_a_row():
    comments, diagnostics = load()
    assert all(c.text for c in comments)
    assert any("comment text" in d.reason for d in diagnostics)


def test_limit_counts_top_level_comments_and_keeps_their_replies():
    """A reply rides along with the parent that made the cut, free of charge."""
    comments, _ = parser.parse_comments(
        FIXTURE.read_bytes(), POST_ID, CAPTURED_AT, limit=2
    )
    assert [c.rank for c in top_level(comments)] == [1, 2]
    assert [c.author for c in top_level(comments)] == ["Alice Nguyen", "Trần Minh"]
    assert any(c.author == "Bob Tran" for c in comments)


def test_a_reply_to_a_dropped_parent_is_dropped_too():
    """Never re-parented to the post: it would read as a top-level comment."""
    comments, _ = parser.parse_comments(
        FIXTURE.read_bytes(), POST_ID, CAPTURED_AT, limit=0
    )
    assert comments == []


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


def test_duplicate_comment_render_is_collapsed():
    """A permalink page renders the comment list twice; ranks must not count double."""
    one = (
        '<div role="article" aria-label="Comment by Ann 2 hours ago">'
        '<div dir="auto">Ann</div><div dir="auto">first comment</div>'
        '<a href="?comment_id=111">2 h</a>'
        '<div aria-label="2 reactions; see who reacted to this"></div>'
        "</div>"
    )
    two = (
        '<div role="article" aria-label="Comment by Bo 1 hour ago">'
        '<div dir="auto">Bo</div><div dir="auto">second comment</div>'
        '<a href="?comment_id=222">1 h</a>'
        "</div>"
    )
    html = (one + two + one + two).encode("utf-8")
    comments, _ = parser.parse_comments(html, "900", CAPTURED_AT)
    assert [c.comment_id for c in comments] == ["111", "222"]
    assert [c.rank for c in comments] == [1, 2]
    assert comments[0].like_count == 2
    assert comments[1].like_count is None
