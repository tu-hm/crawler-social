"""Tests for `targets`: turning a hand-written URL list into crawl targets."""

from __future__ import annotations

from pathlib import Path

import pytest

from crawler_social import targets

EXAMPLE = Path(__file__).resolve().parents[1] / "example.txt"


def test_the_projects_own_example_file_yields_its_four_targets():
    """The headings and blank lines in example.txt are not URLs."""
    assert targets.load_targets(EXAMPLE) == [
        "https://www.facebook.com/groups/VNOIForum",
        "https://www.facebook.com/groups/IffIndianFootballFans",
        "https://www.facebook.com/vnoi.wiki",
        "https://www.facebook.com/olaclass.edu",
    ]


def test_headings_comments_and_blank_lines_are_ignored():
    text = "\n".join(
        [
            "Groups:",
            "",
            "# a note to self",
            "https://www.facebook.com/groups/VNOIForum",
            "Page: ",
            "https://www.facebook.com/vnoi.wiki",
        ]
    )
    assert targets.parse_targets(text) == [
        "https://www.facebook.com/groups/VNOIForum",
        "https://www.facebook.com/vnoi.wiki",
    ]


def test_order_is_kept_and_duplicates_are_dropped():
    """The order is the order the crawl visits them in."""
    text = "\n".join(
        [
            "https://www.facebook.com/b",
            "https://www.facebook.com/a",
            "https://m.facebook.com/b/",
            "https://www.facebook.com/b?ref=bookmarks",
        ]
    )
    assert targets.parse_targets(text) == [
        "https://www.facebook.com/b",
        "https://www.facebook.com/a",
    ]


def test_tracking_parameters_are_stripped():
    """Otherwise one group is filed under two different page_url values."""
    url = targets.normalize_target(
        "https://www.facebook.com/groups/VNOIForum/?ref=bookmarks#top"
    )
    assert url == "https://www.facebook.com/groups/VNOIForum"


def test_a_bare_host_is_accepted_and_made_absolute():
    assert (
        targets.normalize_target("facebook.com/vnoi.wiki")
        == "https://www.facebook.com/vnoi.wiki"
    )


@pytest.mark.parametrize(
    "line",
    [
        "Groups:",
        "",
        "   ",
        "# https://www.facebook.com/vnoi.wiki",
        "https://example.com/vnoi.wiki",
        "https://facebook.com.evil.test/vnoi.wiki",
        "javascript:alert(1)",
        "file:///etc/passwd",
    ],
)
def test_anything_that_is_not_a_facebook_url_is_rejected(line):
    assert targets.normalize_target(line) is None


def test_the_bare_home_feed_is_not_a_target():
    """It is personalised, unbounded, and not reproducible."""
    assert targets.normalize_target("https://www.facebook.com/") is None
    assert targets.normalize_target("https://www.facebook.com") is None


def test_a_missing_file_names_the_file_it_could_not_read(tmp_path):
    with pytest.raises(targets.TargetsError) as excinfo:
        targets.load_targets(tmp_path / "nope.txt")
    assert "nope.txt" in str(excinfo.value)


def test_a_file_with_no_urls_says_what_it_wanted(tmp_path):
    path = tmp_path / "targets.txt"
    path.write_text("Groups:\n\nPage:\n", encoding="utf-8")
    with pytest.raises(targets.TargetsError) as excinfo:
        targets.load_targets(path)
    assert "one URL per line" in str(excinfo.value)
