"""Tests for `crawler crawl` with several targets and `--full`.

`run_crawl` is stubbed throughout: what is under test is which URLs the
command decides to crawl and with what options, not the crawling itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from crawler_social import cli, config as config_module
from crawler_social.pipeline import RunSummary

runner = CliRunner()


def summary(run_id: int = 1, blocked: str | None = None) -> RunSummary:
    return RunSummary(
        run_id=run_id,
        status="failed" if blocked else "completed",
        snapshots_captured=1,
        snapshots_total=1,
        new_posts=2,
        existing_posts=0,
        errors=0,
        diagnostics=[],
        blocked=blocked,
        blocked_message="login wall" if blocked else None,
        comments_captured=5,
        posts_with_comments=1,
        posts_hydrated=2,
    )


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """Record every run_crawl call the command makes, and crawl nothing."""
    recorded: list[dict] = []

    def fake_run_crawl(page_url, **kwargs):
        recorded.append({"page_url": page_url, **kwargs})
        return summary(run_id=len(recorded))

    monkeypatch.setattr("crawler_social.pipeline.run_crawl", fake_run_crawl)
    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda env=None: config_module.Config(
            browser_binary=None,
            profile_dir=tmp_path / "profile",
            db_path=tmp_path / "social.db",
            page_url=None,
        ),
    )
    monkeypatch.setattr(cli, "load_config", config_module.load_config)
    return recorded


def test_several_urls_are_crawled_in_the_order_given(calls):
    result = runner.invoke(
        cli.app,
        [
            "crawl",
            "https://www.facebook.com/groups/VNOIForum",
            "https://www.facebook.com/vnoi.wiki",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [c["page_url"] for c in calls] == [
        "https://www.facebook.com/groups/VNOIForum",
        "https://www.facebook.com/vnoi.wiki",
    ]


def test_a_targets_file_supplies_the_urls(calls, tmp_path):
    path = tmp_path / "targets.txt"
    path.write_text(
        "Groups:\n\nhttps://www.facebook.com/groups/VNOIForum\n"
        "Page:\nhttps://www.facebook.com/vnoi.wiki\n",
        encoding="utf-8",
    )
    result = runner.invoke(cli.app, ["crawl", "--targets", str(path)])
    assert result.exit_code == 0, result.output
    assert [c["page_url"] for c in calls] == [
        "https://www.facebook.com/groups/VNOIForum",
        "https://www.facebook.com/vnoi.wiki",
    ]


def test_the_projects_example_file_crawls_its_four_targets(calls):
    example = Path(__file__).resolve().parents[1] / "example.txt"
    result = runner.invoke(cli.app, ["crawl", "--targets", str(example), "--full"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 4
    assert calls[0]["page_url"] == "https://www.facebook.com/groups/VNOIForum"


def test_full_turns_on_every_collection_option(calls):
    result = runner.invoke(
        cli.app,
        ["crawl", "https://www.facebook.com/vnoi.wiki", "--full", "--limit", "7"],
    )
    assert result.exit_code == 0, result.output
    call = calls[0]
    assert call["top_comments"] == cli.FULL_TOP_COMMENTS
    # Every post the feed yielded gets a permalink visit, and --limit is the
    # ceiling on how many that is.
    assert call["comments_max_posts"] == 7
    assert call["expand_text"] is True
    assert call["hover_timestamps"] is True
    assert call["include_replies"] is True


def test_an_explicit_flag_still_beats_full(calls):
    result = runner.invoke(
        cli.app,
        [
            "crawl",
            "https://www.facebook.com/vnoi.wiki",
            "--full",
            "--comments",
            "5",
            "--no-replies",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["top_comments"] == 5
    assert calls[0]["include_replies"] is False


def test_duplicates_across_the_two_sources_are_crawled_once(calls, tmp_path):
    path = tmp_path / "targets.txt"
    path.write_text("https://www.facebook.com/vnoi.wiki\n", encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["crawl", "https://www.facebook.com/vnoi.wiki/", "--targets", str(path)],
    )
    assert result.exit_code == 0, result.output
    assert [c["page_url"] for c in calls] == ["https://www.facebook.com/vnoi.wiki"]


def test_a_non_facebook_url_is_skipped_with_a_warning(calls):
    result = runner.invoke(
        cli.app,
        ["crawl", "https://example.com/feed", "https://www.facebook.com/vnoi.wiki"],
    )
    assert result.exit_code == 0, result.output
    assert "skipping" in result.output
    assert [c["page_url"] for c in calls] == ["https://www.facebook.com/vnoi.wiki"]


def test_no_target_at_all_is_a_usage_error(calls):
    result = runner.invoke(cli.app, ["crawl"])
    assert result.exit_code == 2
    assert "--targets" in result.output
    assert calls == []


def test_a_wall_stops_the_remaining_targets(monkeypatch, calls):
    """The next target would meet the same wall; hammering it makes it worse."""

    def blocked_run(page_url, **kwargs):
        calls.append({"page_url": page_url, **kwargs})
        return summary(run_id=len(calls), blocked="login_wall")

    monkeypatch.setattr("crawler_social.pipeline.run_crawl", blocked_run)
    result = runner.invoke(
        cli.app,
        [
            "crawl",
            "https://www.facebook.com/groups/VNOIForum",
            "https://www.facebook.com/vnoi.wiki",
        ],
    )
    assert result.exit_code == 3
    assert len(calls) == 1
    assert "1 target(s) not crawled" in result.output


def test_one_failing_target_does_not_stop_the_others(monkeypatch, calls):
    def sometimes_explodes(page_url, **kwargs):
        calls.append({"page_url": page_url, **kwargs})
        if "VNOIForum" in page_url:
            raise RuntimeError("playwright exploded")
        return summary(run_id=len(calls))

    monkeypatch.setattr("crawler_social.pipeline.run_crawl", sometimes_explodes)
    result = runner.invoke(
        cli.app,
        [
            "crawl",
            "https://www.facebook.com/groups/VNOIForum",
            "https://www.facebook.com/vnoi.wiki",
        ],
    )
    assert [c["page_url"] for c in calls] == [
        "https://www.facebook.com/groups/VNOIForum",
        "https://www.facebook.com/vnoi.wiki",
    ]
    # Reported, not silently swallowed.
    assert result.exit_code == 1
    assert "playwright exploded" in result.output


def test_the_run_reports_full_posts_and_a_total(calls):
    result = runner.invoke(
        cli.app,
        [
            "crawl",
            "https://www.facebook.com/groups/VNOIForum",
            "https://www.facebook.com/vnoi.wiki",
        ],
    )
    assert "full posts: 2" in result.output
    assert "total: 4 new posts | 4 full posts | 10 comments" in result.output
