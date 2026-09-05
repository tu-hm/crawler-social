"""Configuration loading for crawler-social.

Reads optional local settings from environment variables first, then from a
gitignored `.env` file next to the project root. Supports:

- CRAWLER_BROWSER_BINARY
- CRAWLER_PROFILE_DIR
- CRAWLER_DB_PATH
- CRAWLER_PAGE_URL (convenience default for the chosen public Page)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import paths


@dataclass(frozen=True)
class Config:
    browser_binary: str | None
    profile_dir: Path
    db_path: Path
    page_url: str | None


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_config(env: dict[str, str] | None = None) -> Config:
    env = dict(env) if env is not None else dict(os.environ)
    env_file = paths.config_file()
    for key, value in _load_dotenv(env_file).items():
        env.setdefault(key, value)

    profile_dir = Path(
        env.get("CRAWLER_PROFILE_DIR") or paths.default_profile_dir(env)
    )
    db_path = Path(env.get("CRAWLER_DB_PATH") or paths.default_db_path(env))
    return Config(
        browser_binary=env.get("CRAWLER_BROWSER_BINARY") or None,
        profile_dir=profile_dir,
        db_path=db_path,
        page_url=env.get("CRAWLER_PAGE_URL") or None,
    )
