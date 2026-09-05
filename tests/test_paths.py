"""Tests for paths.py covering mocked macOS and Linux platform values."""

from __future__ import annotations

import platformdirs
import pytest

from crawler_social import paths


@pytest.mark.parametrize(
    ("platform", "expected_name"),
    [
        ("Darwin", "Library/Application Support/crawler-social"),
        ("Linux", ".local/state/crawler-social"),
    ],
)
def test_state_dir_per_platform(platform, expected_name, monkeypatch):
    monkeypatch.setattr(platformdirs, "user_state_dir", lambda appname, appauthor=False: {
        "Darwin": "/Users/test/Library/Application Support/crawler-social",
        "Linux": "/home/test/.local/state/crawler-social",
    }[platform])
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    state = paths.state_dir(env={})
    assert expected_name in str(state)


def test_default_profile_dir_is_under_state_dir(monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("CRAWLER_TEST_PLATFORM", "Linux")
    profile = paths.default_profile_dir(env={})
    assert profile == paths.state_dir({}) / "chrome" / "default"


def test_xdg_state_home_respected_on_linux():
    env = {"XDG_STATE_HOME": "/custom/state"}
    assert paths.state_dir(env) == paths.state_dir(env)
    assert paths.default_profile_dir(env) == paths.state_dir(env) / "chrome" / "default"


def test_default_db_path_is_data_social_db(tmp_path, monkeypatch):
    env = {"CRAWLER_PROJECT_ROOT": str(tmp_path)}
    assert paths.default_db_path(env) == tmp_path / "data" / "social.db"
