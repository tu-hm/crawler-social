"""Tests for the parser."""

from __future__ import annotations

from datetime import datetime, timezone
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
    assert ids == ["777001", "777002", "777003", "777004", "6006"]
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


def test_malformed_candidates_skipped_with_diagnostic(parsed):
    posts, diagnostics = parsed
    assert all(p.post_id != "777005" for p in posts)
    reasons = {d.reason for d in diagnostics}
    assert "no post id found" in reasons
    assert "no text content" in reasons


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
