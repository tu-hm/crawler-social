"""OS-appropriate application paths for crawler-social.

All operating-system specific behavior lives here. The rest of the
application must use these helpers instead of scattering platform checks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

APP_NAME = "crawler-social"


def state_dir(env: dict[str, str] | None = None) -> Path:
    """Application state directory (browser profiles live under here)."""
    env = env if env is not None else os.environ
    xdg = env.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path(platformdirs.user_state_dir(appname=APP_NAME, appauthor=False))


def default_profile_dir(env: dict[str, str] | None = None) -> Path:
    """Default Chrome profile directory, outside the repository."""
    return state_dir(env) / "chrome" / "default"


def default_db_path(env: dict[str, str] | None = None) -> Path:
    """Default database location: data/social.db relative to the project."""
    env = env if env is not None else os.environ
    root = env.get("CRAWLER_PROJECT_ROOT")
    if root:
        return Path(root) / "data" / "social.db"
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        current = start
        while current != current.parent:
            if (current / "pyproject.toml").is_file():
                return current / "data" / "social.db"
            current = current.parent
    return Path.cwd() / "data" / "social.db"


def config_file(env: dict[str, str] | None = None) -> Path:
    """Local, gitignored configuration file path."""
    return default_db_path(env).parent.parent / ".env"


def is_windows_like() -> bool:
    """True when running on an unsupported platform name (kept for tests)."""
    return sys.platform.startswith("win")
