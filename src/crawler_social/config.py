"""Configuration loading for crawler-social.

Reads optional local settings from environment variables first, then from a
gitignored `.env` file next to the project root. Supports:

- CRAWLER_BROWSER_BINARY
- CRAWLER_PROFILE_DIR
- CRAWLER_DB_PATH
- CRAWLER_PAGE_URL (convenience default for the chosen public Page)
- CRAWLER_ATTACH_MODE ("launch" or "cdp")
- CRAWLER_CDP_URL (DevTools endpoint used when the mode is "cdp")
- CRAWLER_LOCALE, CRAWLER_TIMEZONE (keep the automated browser consistent
  with the machine it runs on)
- CRAWLER_SERVE_HOST (default "127.0.0.1"; the local web viewer binds
  loopback only unless told otherwise)
- CRAWLER_SERVE_PORT (default 8765)
- CRAWLER_SERVE_TOKEN (unset by default; required for non-loopback binds)
- CRAWLER_TOP_COMMENTS (0 = off; comments wanted per post)
- CRAWLER_COMMENTS_MAX_POSTS (ceiling on permalink visits per run)
- CRAWLER_EXPAND_TEXT ("1"/"0"; click "See more" before capture)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import paths


ATTACH_MODES = ("launch", "cdp")
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 8765
DEFAULT_TOP_COMMENTS = 0
DEFAULT_COMMENTS_MAX_POSTS = 10


@dataclass(frozen=True)
class Config:
    browser_binary: str | None
    profile_dir: Path
    db_path: Path
    page_url: str | None
    attach_mode: str = "launch"
    cdp_url: str = DEFAULT_CDP_URL
    locale: str = "en-US"
    timezone_id: str | None = None
    serve_host: str = DEFAULT_SERVE_HOST
    serve_port: int = DEFAULT_SERVE_PORT
    serve_token: str | None = None
    top_comments: int = DEFAULT_TOP_COMMENTS
    comments_max_posts: int = DEFAULT_COMMENTS_MAX_POSTS
    expand_text: bool = True


def _bool_env(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _int_env(value: str | None, default: int, name: str, *, minimum: int = 0) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer; got {value!r}.") from None
    if parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}; got {parsed}.")
    return parsed


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
    attach_mode = (env.get("CRAWLER_ATTACH_MODE") or "launch").strip().lower()
    if attach_mode not in ATTACH_MODES:
        raise ValueError(
            f"CRAWLER_ATTACH_MODE must be one of {', '.join(ATTACH_MODES)}; "
            f"got {attach_mode!r}."
        )
    serve_port_raw = env.get("CRAWLER_SERVE_PORT") or str(DEFAULT_SERVE_PORT)
    try:
        serve_port = int(serve_port_raw)
    except ValueError:
        raise ValueError(
            f"CRAWLER_SERVE_PORT must be an integer between 1 and 65535; "
            f"got {serve_port_raw!r}."
        ) from None
    if not 1 <= serve_port <= 65535:
        raise ValueError(
            f"CRAWLER_SERVE_PORT must be an integer between 1 and 65535; "
            f"got {serve_port_raw!r}."
        )
    return Config(
        browser_binary=env.get("CRAWLER_BROWSER_BINARY") or None,
        profile_dir=profile_dir,
        db_path=db_path,
        page_url=env.get("CRAWLER_PAGE_URL") or None,
        attach_mode=attach_mode,
        cdp_url=env.get("CRAWLER_CDP_URL") or DEFAULT_CDP_URL,
        locale=env.get("CRAWLER_LOCALE") or "en-US",
        timezone_id=env.get("CRAWLER_TIMEZONE") or None,
        serve_host=env.get("CRAWLER_SERVE_HOST") or DEFAULT_SERVE_HOST,
        serve_port=serve_port,
        serve_token=env.get("CRAWLER_SERVE_TOKEN") or None,
        top_comments=_int_env(
            env.get("CRAWLER_TOP_COMMENTS"),
            DEFAULT_TOP_COMMENTS,
            "CRAWLER_TOP_COMMENTS",
        ),
        comments_max_posts=_int_env(
            env.get("CRAWLER_COMMENTS_MAX_POSTS"),
            DEFAULT_COMMENTS_MAX_POSTS,
            "CRAWLER_COMMENTS_MAX_POSTS",
            minimum=1,
        ),
        expand_text=_bool_env(env.get("CRAWLER_EXPAND_TEXT"), True),
    )
