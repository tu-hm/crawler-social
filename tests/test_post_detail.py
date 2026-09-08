"""Tests for `parser.parse_post_detail` -- the second, authoritative read.

The feed pass finds a post and reads what the feed shows: a body that may be
clipped, and a relative age. The permalink pass re-reads the same post whole.
These tests fix what the second read is allowed to change.
"""

from __future__ import annotations

from datetime import datetime, timezone

from crawler_social import parser

PAGE_URL = "https://www.facebook.com/groups/VNOIForum"
POST_ID = "12345"
CAPTURED_AT = datetime(2026, 2, 4, 5, 0, 0, tzinfo=timezone.utc)


def permalink_page(
    *,
    body: str = "The whole announcement, every word of it.",
    author: str = "Nguyen Hoang",
    extra: str = "",
) -> bytes:
    return (
        '<div role="article" aria-labelledby="s1" id="s1">'
        f"<h3><a href='/nh/'><strong>{author}</strong></a></h3>"
        f'<div data-ad-preview="message"><div dir="auto">{body}</div></div>'
        f'<a href="/groups/VNOIForum/posts/{POST_ID}">2 giờ</a>'
        f"{extra}"
        "</div>"
    ).encode("utf-8")


def test_the_story_is_returned_under_the_id_the_caller_asked_for():
    """A hydrated post updates the feed pass's row, it does not open a new one."""
    post, _ = parser.parse_post_detail(
        permalink_page(), PAGE_URL, "h-derivedhash", CAPTURED_AT
    )
    assert post.post_id == "h-derivedhash"
    assert post.page_url == PAGE_URL
    assert post.text == "The whole announcement, every word of it."
    assert post.author == "Nguyen Hoang"


def test_the_permalink_pages_own_timestamp_is_read():
    post, _ = parser.parse_post_detail(
        permalink_page(), PAGE_URL, POST_ID, CAPTURED_AT
    )
    assert post.published_at == "2026-02-04T03:00:00+00:00"


def test_the_reaction_total_is_read_from_the_summary_bar():
    bar = (
        '<div><div aria-label="Like: 99 people; see who reacted to this"></div>'
        "<span>184</span></div>"
    )
    post, _ = parser.parse_post_detail(
        permalink_page(extra=bar), PAGE_URL, POST_ID, CAPTURED_AT
    )
    assert post.reaction_count == 184


def test_the_story_wins_over_a_suggested_post_beside_it():
    """A permalink page can render neighbours; the longest body is the story."""
    suggested = (
        '<div role="article" aria-labelledby="s2" id="s2">'
        "<h3><a href='/other/'><strong>Someone Else</strong></a></h3>"
        '<div data-ad-preview="message"><div dir="auto">Short.</div></div>'
        "</div>"
    )
    html = permalink_page() + suggested.encode("utf-8")
    post, _ = parser.parse_post_detail(html, PAGE_URL, "h-derivedhash", CAPTURED_AT)
    assert post.author == "Nguyen Hoang"


def test_a_matching_id_beats_the_longest_body():
    long_neighbour = (
        '<div role="article" aria-labelledby="s2" id="s2">'
        "<h3><a href='/other/'><strong>Someone Else</strong></a></h3>"
        '<div data-ad-preview="message"><div dir="auto">'
        + ("padding " * 60)
        + "</div></div>"
        '<a href="/groups/VNOIForum/posts/99999">3 giờ</a>'
        "</div>"
    )
    html = permalink_page() + long_neighbour.encode("utf-8")
    post, _ = parser.parse_post_detail(html, PAGE_URL, POST_ID, CAPTURED_AT)
    assert post.author == "Nguyen Hoang"


def test_comments_on_the_page_are_not_mistaken_for_the_post():
    comment = (
        '<div role="article" aria-label="Comment by Tran Minh 1 giờ">'
        '<div dir="auto">Tran Minh</div>'
        '<div dir="auto">A comment, which is not the post.</div>'
        "</div>"
    )
    post, _ = parser.parse_post_detail(
        permalink_page(extra=comment), PAGE_URL, POST_ID, CAPTURED_AT
    )
    assert "not the post" not in (post.text or "")
    assert post.author == "Nguyen Hoang"


def test_a_page_with_no_story_yields_none_rather_than_raising():
    """A deleted post, or markup that moved: a diagnostic, not a crash."""
    post, diagnostics = parser.parse_post_detail(
        b"<html><body></body></html>", PAGE_URL, POST_ID, CAPTURED_AT
    )
    assert post is None
    assert isinstance(diagnostics, list)


def test_empty_bytes_yield_none():
    post, _ = parser.parse_post_detail(b"", PAGE_URL, POST_ID, CAPTURED_AT)
    assert post is None
