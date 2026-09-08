"""Tests for what the hover pass leaves behind in the markup.

A feed carries neither a story's permalink nor its publication time --
Facebook fills both in only when the pointer is over the timestamp. The
capture layer provokes that and writes the result onto the story node; these
tests cover the other half, where the parser reads it back. Offline like the
rest of the parser tests: bytes in, dataclasses out.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from crawler_social import parser

PAGE_URL = "https://www.facebook.com/groups/VNOIForum"
#: 12:00 in Asia/Ho_Chi_Minh, the zone the target pages render in.
CAPTURED_AT = datetime(2026, 2, 4, 5, 0, 0, tzinfo=timezone.utc)
HCMC_OFFSET = -420


def story(*, permalink: str = "", timestamp: str = "", offset: int = HCMC_OFFSET) -> bytes:
    """One annotated feed unit, as the hover pass would leave it."""
    attributes = f'aria-posinset="1" {parser.TZ_OFFSET_ATTR}="{offset}"'
    if permalink:
        attributes += f' {parser.PERMALINK_ATTR}="{permalink}"'
    if timestamp:
        attributes += f' {parser.TIMESTAMP_ATTR}="{timestamp}"'
    return (
        f"<div {attributes}>"
        '<div data-ad-rendering-role="profile_name">Nguyen Hoang</div>'
        '<div data-ad-rendering-role="story_message">'
        '<div dir="auto">Bai viet day du.</div></div>'
        '<a href="#">4 giờ</a>'
        "</div>"
    ).encode("utf-8")


def only(html: bytes) -> parser.Post:
    posts, _ = parser.parse(html, PAGE_URL, CAPTURED_AT)
    assert len(posts) == 1
    return posts[0]


def test_a_hovered_timestamp_becomes_an_exact_publication_time():
    post = only(story(timestamp="8 Tháng 9, 2025 lúc 14:32"))
    # 14:32 in UTC+7 is 07:32 UTC.
    assert post.published_at == "2025-09-08T07:32:00+00:00"


def test_the_tooltip_beats_the_relative_age_beside_it():
    """"4 giờ" is a minute-resolution guess; the tooltip is the real minute."""
    post = only(story(timestamp="3 Tháng 2, 2026 lúc 09:05"))
    assert post.published_at == "2026-02-03T02:05:00+00:00"


def test_without_a_hover_the_relative_age_is_still_used():
    post = only(story())
    assert post.published_at == "2026-02-04T01:00:00+00:00"


def test_the_browsers_timezone_is_what_converts_the_tooltip_to_utc():
    """The same wall clock in London and Ho Chi Minh City is not the same instant."""
    hcmc = only(story(timestamp="8 Tháng 9, 2025 lúc 14:32"))
    utc = only(story(timestamp="8 Tháng 9, 2025 lúc 14:32", offset=0))
    assert hcmc.published_at == "2025-09-08T07:32:00+00:00"
    assert utc.published_at == "2025-09-08T14:32:00+00:00"


def test_a_hovered_permalink_becomes_the_posts_url_and_its_id():
    post = only(
        story(permalink="https://www.facebook.com/groups/VNOIForum/posts/12345/")
    )
    assert post.url == "https://www.facebook.com/groups/VNOIForum/posts/12345/"
    assert post.post_id == "12345"


def test_a_hovered_permalink_rescues_a_story_from_a_content_hash():
    """The whole point: a hashed id has no permalink, so it gets no comments."""
    without = only(story())
    assert parser.is_synthetic_post_id(without.post_id)
    with_hover = only(
        story(permalink="https://www.facebook.com/groups/VNOIForum/posts/12345/")
    )
    assert not parser.is_synthetic_post_id(with_hover.post_id)


def test_tracking_parameters_are_stripped_from_a_hovered_permalink():
    post = only(
        story(
            permalink=(
                "https://www.facebook.com/groups/VNOIForum/posts/12345/"
                "?__cft__[0]=abc&amp;ref=notif"
            )
        )
    )
    assert "__cft__" not in post.url and "ref=notif" not in post.url


@pytest.mark.parametrize(
    "permalink",
    [
        "https://evil.test/groups/VNOIForum/posts/12345/",
        "javascript:alert(1)",
        "https://l.facebook.com/l.php?u=https%3A%2F%2Fevil.test",
    ],
)
def test_a_permalink_pointing_off_facebook_is_refused(permalink):
    """A hovered href is still untrusted: the next pass navigates to it."""
    post = only(story(permalink=permalink))
    assert post.url is None or "evil.test" not in post.url
    # Nor may it name the post: the id is read out of the permalink's path,
    # and a story keyed by someone else's id is a story filed as the wrong one.
    assert parser.is_synthetic_post_id(post.post_id)


def test_an_unreadable_tooltip_leaves_the_post_dated_by_its_age():
    post = only(story(timestamp="Xem thêm về trang này"))
    assert post.published_at == "2026-02-04T01:00:00+00:00"


def test_a_missing_offset_is_read_as_utc_rather_than_dropping_the_time():
    html = (
        f'<div aria-posinset="1" {parser.TIMESTAMP_ATTR}="8 Tháng 9, 2025 lúc 14:32">'
        '<div data-ad-rendering-role="profile_name">Nguyen Hoang</div>'
        '<div data-ad-rendering-role="story_message"><div dir="auto">Bai viet.</div></div>'
        "</div>"
    ).encode("utf-8")
    assert only(html).published_at == "2025-09-08T14:32:00+00:00"


def test_a_comment_anchor_is_stripped_from_a_post_permalink():
    """A story surfaced as "X commented on this" hovers to a comment anchor.

    Visiting that opens the post scrolled to one reply, which is not the post,
    and files the same story under two URLs across runs.
    """
    post = only(
        story(
            permalink=(
                "https://www.facebook.com/groups/VNOIForum/posts/28531754426418687/"
                "?comment_id=28532564316337698"
            )
        )
    )
    assert post.url == (
        "https://www.facebook.com/groups/VNOIForum/posts/28531754426418687/"
    )
    assert post.post_id == "28531754426418687"
