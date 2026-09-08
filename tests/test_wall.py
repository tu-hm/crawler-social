"""Tests for content-based page classification.

The real captures in data/fixtures are login walls that the old URL-only check
missed entirely; they are the regression this module exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crawler_social import wall

FIXTURES = Path(__file__).parent / "fixtures"

GOOD_PAGE = (FIXTURES / "facebook_page_sample.html").read_bytes()

#: Real captures from a run that reported success while storing nothing.
REAL_WALLS = sorted(FIXTURES.glob("facebook_login_wall_*.html"))

LOGIN_HTML = b"""
<html><body>
  <h2>Log in to Facebook</h2>
  <form id="login_form">
    <input type="text" name="email">
    <input type="password" name="pass">
    <button>Log in</button>
  </form>
  <a href="/recover">Forgotten password?</a>
  <a href="/reg">Create new account</a>
</body></html>
"""


def test_good_page_classifies_ok():
    verdict = wall.classify(GOOD_PAGE, "https://www.facebook.com/ExamplePublicPage")
    assert verdict.kind == wall.OK
    assert not verdict.blocking
    assert verdict.article_count >= 1


def test_login_wall_detected_without_url_hint():
    verdict = wall.classify(LOGIN_HTML, "https://www.facebook.com/NASA")
    assert verdict.kind == wall.LOGIN_WALL
    assert verdict.blocking


def test_login_wall_detected_from_url_redirect():
    verdict = wall.classify(b"<html></html>", "https://www.facebook.com/login/?next=x")
    assert verdict.kind == wall.LOGIN_WALL


@pytest.mark.parametrize("path", REAL_WALLS, ids=lambda p: p.stem)
def test_real_captures_are_recognised_as_walls_not_content(path: Path):
    verdict = wall.classify(path.read_bytes(), "")
    assert verdict.kind == wall.LOGIN_WALL, f"{path.name} -> {verdict}"


def test_real_wall_fixtures_are_present():
    """Guard the guard: a glob that matches nothing would pass silently."""
    assert len(REAL_WALLS) == 2


def test_checkpoint_beats_login_when_both_present():
    html = LOGIN_HTML.replace(b"<h2>", b"<h2>Please confirm your identity </h2><h2>")
    assert wall.classify(html, "").kind == wall.CHECKPOINT


def test_checkpoint_detected_from_url():
    assert wall.classify(GOOD_PAGE, "https://www.facebook.com/checkpoint/123").kind == (
        wall.CHECKPOINT
    )


def test_rate_limit_detected():
    html = b"<html><body><p>You're temporarily blocked from doing this.</p></body></html>"
    assert wall.classify(html, "").kind == wall.RATE_LIMITED


def test_marker_inside_script_bundle_does_not_trip_detection():
    # Every logged-in Facebook page ships these strings inside its JS bundles.
    html = (
        b'<html><head><script>var s="Log in to Facebook";'
        b'var t="create new account";</script></head><body>'
        + GOOD_PAGE
        + b"</body></html>"
    )
    assert wall.classify(html, "").kind == wall.OK


def test_empty_page_is_not_blocking():
    verdict = wall.classify(b"<html><body></body></html>", "")
    assert verdict.kind == wall.EMPTY
    assert not verdict.blocking


def test_garbage_bytes_do_not_raise():
    assert wall.classify(b"\x00\xff not html", "").kind in {wall.EMPTY, wall.OK}


def test_modern_feed_without_article_roles_is_not_read_as_empty():
    """Stories moved to aria-posinset; counting role="article" alone saw a rendered feed as empty."""
    html = (
        b'<div aria-posinset="1">'
        b'<div data-ad-rendering-role="profile_name">Tin Tuc Moi</div>'
        b'<div data-ad-comet-preview="message">Tau ca cho sieu me hang cam hom nay.</div>'
        b"</div>"
    )
    verdict = wall.classify(html, "https://www.facebook.com/groups/900")
    assert verdict.kind == wall.OK
    assert verdict.article_count == 1


def test_feed_unit_is_not_counted_twice_when_it_is_also_an_article():
    html = (
        b'<div role="article" aria-posinset="1">'
        b"A story long enough to clear the minimum text threshold."
        b"</div>"
    )
    assert wall.classify(html).article_count == 1
