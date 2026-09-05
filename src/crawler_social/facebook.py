"""Facebook Page capture: browser startup, navigation, scrolling, snapshots.

All browser interaction lives in this module. The browser is visible; on
login the user completes it manually in the window. Checkpoints and CAPTCHAs
stop the run instead of being bypassed.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from .config import Config
from .lock import FileLock

MAX_CANDIDATE_POSTS = 20
SCROLL_PAUSE_MS = 1500
DEFAULT_MAX_SECONDS = 180.0

_LOGIN_MARKERS = ("login",)
_CHECKPOINT_MARKERS = (
    "checkpoint",
    "captcha",
    "captcha_entered_point",
    "two_factor",
    "two-step verification",
)


class CaptureError(RuntimeError):
    """Capture failed with a clear, user-facing reason."""


def check_gui_session(env: dict[str, str] | None = None) -> None:
    """Fail clearly when no logged-in graphical session exists."""
    env = env if env is not None else dict(__import__("os").environ)
    if sys.platform == "darwin":
        if not Path("/Applications/Google Chrome.app").exists() and not Path(
            "/Applications/Chromium.app"
        ).exists():
            # The binary resolver gives detailed guidance; nothing more here.
            pass
        return
    if sys.platform.startswith("linux"):
        if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
            raise CaptureError(
                "No graphical session found: DISPLAY and WAYLAND_DISPLAY are "
                "both empty. Facebook capture needs a logged-in desktop "
                "session (X11 or Wayland)."
            )


_CHROME_CANDIDATES = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/google-chrome",
    ],
    "linux": ["google-chrome", "google-chrome-stable", "google-chrome-beta"],
}

_CHROMIUM_CANDIDATES = {
    "darwin": ["/Applications/Chromium.app/Contents/MacOS/Chromium"],
    "linux": ["chromium", "chromium-browser", "chromium-freeworld"],
}


def resolve_browser_binary(
    override: str | None = None,
    platform: str | None = None,
) -> str:
    """Resolve the browser binary in the documented order."""
    platform = platform or sys.platform
    if override:
        found = shutil.which(override) or override
        if Path(found).exists():
            return str(Path(found))
        raise CaptureError(
            f"CRAWLER_BROWSER_BINARY is set to {override!r} but that file "
            "does not exist. Install Google Chrome or Chromium, or unset it."
        )
    for candidates in (_CHROME_CANDIDATES, _CHROMIUM_CANDIDATES):
        for candidate in candidates.get(platform, []):
            if "/" in candidate:
                if Path(candidate).exists():
                    return candidate
            else:
                found = shutil.which(candidate)
                if found:
                    return found
    raise CaptureError(
        "No Google Chrome or Chromium browser found. Install Google Chrome "
        "or Chromium, or set CRAWLER_BROWSER_BINARY to the browser path."
    )


def ensure_profile_dir(path: Path) -> Path:
    """Create the dedicated profile directory with user-only permissions."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def profile_lock_path(profile_dir: Path) -> Path:
    return Path(profile_dir).parent / "browser.lock"


def acquire_profile_lock(profile_dir: Path) -> FileLock:
    lock = FileLock(profile_lock_path(profile_dir))
    lock.acquire(blocking=False)
    return lock


def detect_checkpoint(url: str) -> None:
    lowered = url.lower()
    if any(marker in lowered for marker in _CHECKPOINT_MARKERS):
        raise CaptureError(
            "Facebook is showing a checkpoint, CAPTCHA, or access restriction. "
            "Stop: do not bypass it. Resolve it manually in the browser first."
        )


def wait_for_login(page, page_url: str, deadline_seconds: float = 300.0) -> None:
    """Let the user complete login manually in the visible window."""
    print("Facebook requests login. Please complete it in the browser window.")
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        detect_checkpoint(page.url)
        if not any(marker in page.url.lower() for marker in _LOGIN_MARKERS):
            return
        page.wait_for_timeout(SCROLL_PAUSE_MS)
    raise CaptureError(
        "Login was not completed within 5 minutes. Run the command again."
    )


@dataclass(frozen=True)
class CaptureOptions:
    max_seconds: float = DEFAULT_MAX_SECONDS
    max_candidates: int = MAX_CANDIDATE_POSTS
    scroll_pause_ms: int = SCROLL_PAUSE_MS


def capture_snapshots(
    page_url: str,
    conn,
    run_id: int,
    config: Config,
    options: CaptureOptions | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[tuple[str, bytes]]:
    """Open the Page, scroll, and yield committed (captured_at, html) pairs.

    Each snapshot is committed through save_snapshot() immediately after
    capture -- never deferred to the final scroll, because virtualized feeds
    may remove earlier DOM nodes.
    """
    options = options or CaptureOptions()
    should_stop = should_stop or (lambda: False)
    check_gui_session()
    binary = resolve_browser_binary(config.browser_binary)
    profile = ensure_profile_dir(config.profile_dir)
    print(f"Browser binary: {binary}")
    print(f"Profile path:   {profile}")

    from playwright.sync_api import sync_playwright

    from . import db
    from .parser import parse

    lock = acquire_profile_lock(profile)
    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                str(profile),
                executable_path=binary,
                headless=False,
                viewport={"width": 1280, "height": 900},
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(page_url, wait_until="domcontentloaded")
                detect_checkpoint(page.url)
                if any(marker in page.url.lower() for marker in _LOGIN_MARKERS):
                    wait_for_login(page, page_url)
                    page.goto(page_url, wait_until="domcontentloaded")

                deadline = time.monotonic() + options.max_seconds
                candidates_seen: set[str] = set()

                while time.monotonic() < deadline and not should_stop():
                    html = page.content().encode("utf-8")
                    captured_at = datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    )
                    db.save_snapshot(conn, run_id, page_url, captured_at, html)
                    yield captured_at, html
                    posts, _ = parse(html, page_url, datetime.now(timezone.utc))
                    candidates_seen.update(post.post_id for post in posts)
                    if len(candidates_seen) >= options.max_candidates:
                        break
                    page.mouse.wheel(0, 2400)
                    page.wait_for_timeout(options.scroll_pause_ms)
                    detect_checkpoint(page.url)
            finally:
                context.close()
    finally:
        lock.release()


def save_fixture(
    html: bytes,
    page_url: str,
    captured_at: str,
    browser: str,
    fixtures_dir: Path,
) -> Path:
    """Save a small non-private HTML fixture and metadata sidecar."""
    fixtures_dir = Path(fixtures_dir)
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", page_url.lower()).strip("-")[:60]
    stem = f"{slug}_{captured_at.replace(':', '').replace('+', 'p')}"
    html_path = fixtures_dir / f"{stem}.html"
    meta_path = fixtures_dir / f"{stem}.meta.json"
    html_path.write_bytes(html)
    meta_path.write_text(
        json.dumps(
            {
                "page_url": page_url,
                "captured_at": captured_at,
                "browser": browser,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return html_path
