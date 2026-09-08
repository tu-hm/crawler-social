"""Tests for the parser."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crawler_social.parser import parse

FIXTURE = Path(__file__).parent / "fixtures" / "facebook_page_sample.html"
PAGE_URL = "https://www.facebook.com/ExamplePublicPage"
CAPTURED_AT = datetime(2026, 2, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def parsed():
    posts, diagnostics = parse(FIXTURE.read_bytes(), PAGE_URL, CAPTURED_AT)
    return posts, diagnostics


def test_fixture_produces_stable_post_ids(parsed):
    posts, _ = parsed
    ids = [p.post_id for p in posts]
    # The fifth is the malformed candidate: it carries text but no id of any
    # kind, so the parser derives one rather than dropping the post.
    assert ids[:4] == ["777001", "777002", "777003", "777004"]
    assert ids[4].startswith("h-")
    assert ids[5:] == ["6006"]
    assert ids == [p.post_id for p in parse(FIXTURE.read_bytes(), PAGE_URL, CAPTURED_AT)[0]]


def test_parse_is_pure_and_deterministic(parsed):
    posts, _ = parsed
    again, _ = parse(FIXTURE.read_bytes(), PAGE_URL, CAPTURED_AT)
    assert again == posts


def test_missing_optional_field_becomes_none_without_failing(parsed):
    posts, _ = parsed
    by_id = {p.post_id: p for p in posts}
    assert by_id["777003"].author is None
    assert by_id["777004"].published_at is None


def test_relative_timestamp_resolved_against_captured_at(parsed):
    posts, _ = parsed
    by_id = {p.post_id: p for p in posts}
    # "3 hrs" before 2026-02-04T12:00:00Z.
    assert by_id["777002"].published_at == "2026-02-04T09:00:00+00:00"


def test_absolute_data_utime_timestamp(parsed):
    posts, _ = parsed
    by_id = {p.post_id: p for p in posts}
    assert by_id["777001"].published_at == "2026-02-02T02:40:00+00:00"


def test_text_and_author_extracted(parsed):
    posts, _ = parsed
    by_id = {p.post_id: p for p in posts}
    assert "Artemis II" in by_id["777001"].text
    assert by_id["777001"].author == "NASA"


def test_candidate_without_text_is_skipped_with_diagnostic(parsed):
    """A candidate with a permalink but no words is not a post worth storing."""
    posts, diagnostics = parsed
    assert all(p.post_id != "777005" for p in posts)
    assert "no text content" in {d.reason for d in diagnostics}


def test_candidate_without_any_id_is_kept_under_a_derived_id(parsed):
    """Facebook serves most feed stories with no id, so dropping them empties the crawl."""
    posts, _ = parsed
    derived = [p for p in posts if p.post_id.startswith("h-")]
    assert len(derived) == 1
    assert "no identifiers at all" in derived[0].text
    assert derived[0].url is None


def test_parser_works_without_browser_packages(monkeypatch):
    import sys

    saved = {name: sys.modules.pop(name) for name in ("playwright",) if name in sys.modules}
    try:
        monkeypatch.setitem(sys.modules, "playwright", None)
        posts, _ = parse(FIXTURE.read_bytes(), PAGE_URL, CAPTURED_AT)
        assert posts
    finally:
        sys.modules.update(saved)


def test_empty_and_garbage_html_do_not_raise():
    for blob in (b"", b"<html><body></body></html>", b"\xff\xfe not html"):
        posts, _ = parse(blob, PAGE_URL, CAPTURED_AT)
        assert posts == []


GROUP_FIXTURE = Path(__file__).parent / "fixtures" / "facebook_group_feed.html"
GROUP_URL = "https://www.facebook.com/groups/900"


@pytest.fixture(scope="module")
def group_feed():
    return parse(GROUP_FIXTURE.read_bytes(), GROUP_URL, CAPTURED_AT)


def test_modern_feed_units_are_parsed_as_posts(group_feed):
    """Stories live in aria-posinset units; role="article" is comments only."""
    posts, diagnostics = group_feed
    assert [p.author for p in posts] == ["Tin Tuc Moi", "Nguyen Hoang", "Khuyen Vo"]
    assert diagnostics == []


def test_group_post_id_read_from_the_gm_parameter(group_feed):
    posts, _ = group_feed
    assert posts[0].post_id == "2136830243711210"


def test_story_without_an_id_is_keyed_by_content(group_feed):
    posts, _ = group_feed
    assert posts[1].post_id.startswith("h-")
    assert posts[1].post_id == parse(
        GROUP_FIXTURE.read_bytes(), GROUP_URL, CAPTURED_AT + timedelta(hours=9)
    )[0][1].post_id


def test_comments_do_not_leak_into_the_post_that_holds_them(group_feed):
    posts, _ = group_feed
    assert "Qua kinh khung" not in posts[0].text
    assert posts[0].author == "Tin Tuc Moi"


def test_link_share_falls_back_to_the_attachment_description(group_feed):
    """A pure share has no story message; its title is the attachment's branding."""
    posts, _ = group_feed
    assert posts[2].text == "Va cham giua o to va xe may."


def test_opaque_attachment_token_is_not_stored_as_post_text():
    """A photo-only story's description slot holds an id, not the author's words."""
    html = b"""
    <div aria-posinset="1">
      <div data-ad-rendering-role="profile_name">An Mar</div>
      <div data-ad-rendering-role="description">CcPNOGdt0jm8FuDmyaCB5wmugsb6Q</div>
    </div>
    """
    posts, diagnostics = parse(html, GROUP_URL, CAPTURED_AT)
    assert posts == []
    assert [d.reason for d in diagnostics] == ["no text content"]


def test_post_reaction_total_read_from_the_summary_bar():
    """The bar's first number is the total; per-emotion labels list only the leaders."""
    html = b"""
    <div aria-posinset="1">
      <div data-ad-rendering-role="profile_name">Tin Tuc Moi</div>
      <div data-ad-comet-preview="message"><div dir="auto">Mot bai viet.</div></div>
      <div>
        <div aria-label="Like: 99 people"></div>
        <div aria-label="Haha: 28 people"></div>
        <div><span aria-label="See who reacted to this"></span></div>
        <div><span>129</span><span>3</span><span>1</span></div>
      </div>
    </div>
    """
    posts, _ = parse(html, GROUP_URL, CAPTURED_AT)
    assert posts[0].reaction_count == 129


def test_post_without_a_reaction_bar_has_no_count():
    """Facebook omits the summary entirely at zero reactions, so the count is unknown."""
    html = b"""
    <div aria-posinset="1">
      <div data-ad-rendering-role="profile_name">Nguyen Hoang</div>
      <div data-ad-comet-preview="message"><div dir="auto">Khong ai tha tim.</div></div>
    </div>
    """
    posts, _ = parse(html, GROUP_URL, CAPTURED_AT)
    assert posts[0].reaction_count is None


def test_group_story_permalink_preferred_over_the_photo_viewer():
    """The photo link opens the media viewer, whose comments are not the post's."""
    posts, _ = parse(GROUP_FIXTURE.read_bytes(), GROUP_URL, CAPTURED_AT)
    assert posts[0].url == "https://www.facebook.com/groups/900/posts/2136830243711210"
