"""Facebook Page capture: browser startup, navigation, scrolling, snapshots.

All browser interaction lives in this module. The browser is visible; on
login the user completes it manually in the window. Checkpoints and CAPTCHAs
stop the run instead of being bypassed.

Two ways to reach a logged-in session, chosen by ``CRAWLER_ATTACH_MODE``:

``launch`` (default)
    Playwright starts Chrome against the dedicated profile in
    ``CRAWLER_PROFILE_DIR``. Warm that profile with ``crawler login`` first.

``cdp``
    Playwright attaches to a Chrome the *user* already started with
    ``--remote-debugging-port``. Nothing about that browser was launched by
    automation, so it carries a normal fingerprint and a real session. This is
    the mode to use when a login inside the automated browser gets challenged.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from . import wall
from .config import Config
from .lock import FileLock

MAX_CANDIDATE_POSTS = 20
SCROLL_PAUSE_MS = 1500
DEFAULT_MAX_SECONDS = 180.0
HOME_URL = "https://www.facebook.com/"

#: Strip Chrome's automation banner switches. `--enable-automation` sets
#: `navigator.webdriver` and advertises the session as automated; a session the
#: user logged into by hand should not be announcing that on every request.
_IGNORE_DEFAULT_ARGS = ["--enable-automation"]

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-service-autorun",
    "--password-store=basic",
]

_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""


class CaptureError(RuntimeError):
    """Capture failed with a clear, user-facing reason."""


class BlockedError(CaptureError):
    """Facebook served a wall instead of content. Carries the verdict."""

    def __init__(self, verdict: wall.Verdict) -> None:
        super().__init__(verdict.message)
        self.verdict = verdict


class BrowserClosedError(CaptureError):
    """The browser window went away mid-run."""


def check_gui_session(env: dict[str, str] | None = None) -> None:
    """Fail clearly when no logged-in graphical session exists."""
    env = env if env is not None else dict(__import__("os").environ)
    if sys.platform == "darwin":
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


def _is_closed_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "closed" in text or "disconnected" in text or "crashed" in text


@contextmanager
def _wrap_browser_errors() -> Iterator[None]:
    """Turn Playwright teardown noise into one clear message."""
    from playwright.sync_api import Error as PlaywrightError

    try:
        yield
    except PlaywrightError as exc:
        if _is_closed_error(exc):
            raise BrowserClosedError(
                "The browser window was closed before the run finished. "
                "Leave it open while the crawl runs."
            ) from exc
        raise CaptureError(f"browser error: {exc}") from exc


@contextmanager
def browser_session(config: Config, *, quiet: bool = False) -> Iterator:
    """Yield a Playwright page backed by a logged-in browser.

    Teardown differs by mode on purpose: ``launch`` owns the browser and closes
    it, ``cdp`` is a guest in the user's own Chrome and only closes its own tab.
    """
    check_gui_session()
    profile = ensure_profile_dir(config.profile_dir)

    from playwright.sync_api import sync_playwright

    lock = acquire_profile_lock(profile)
    try:
        with sync_playwright() as pw:
            if config.attach_mode == "cdp":
                if not quiet:
                    print(f"Attaching over CDP: {config.cdp_url}")
                try:
                    browser = pw.chromium.connect_over_cdp(config.cdp_url)
                except Exception as exc:  # noqa: BLE001
                    raise CaptureError(
                        f"Could not attach to Chrome at {config.cdp_url}. "
                        "Start your own Chrome first, for example:\n"
                        "  google-chrome --remote-debugging-port=9222 "
                        f"--user-data-dir={profile}\n"
                        "then log in to Facebook in that window."
                    ) from exc
                context = (
                    browser.contexts[0]
                    if browser.contexts
                    else browser.new_context()
                )
                page = context.new_page()
                try:
                    with _wrap_browser_errors():
                        yield page
                finally:
                    try:
                        page.close()
                    except Exception:  # noqa: BLE001 - teardown is best effort
                        pass
            else:
                binary = resolve_browser_binary(config.browser_binary)
                if not quiet:
                    print(f"Browser binary: {binary}")
                    print(f"Profile path:   {profile}")
                context = pw.chromium.launch_persistent_context(
                    str(profile),
                    executable_path=binary,
                    headless=False,
                    viewport={"width": 1280, "height": 900},
                    locale=config.locale,
                    timezone_id=config.timezone_id,
                    args=_LAUNCH_ARGS,
                    ignore_default_args=_IGNORE_DEFAULT_ARGS,
                )
                context.add_init_script(_INIT_SCRIPT)
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    with _wrap_browser_errors():
                        yield page
                finally:
                    try:
                        context.close()
                    except Exception:  # noqa: BLE001 - teardown is best effort
                        pass
    finally:
        lock.release()


def inspect(page, url: str) -> tuple[bytes, wall.Verdict]:
    """Read the live DOM and classify what Facebook actually served."""
    html = page.content().encode("utf-8")
    return html, wall.classify(html, page.url or url)


def check_session(config: Config) -> wall.Verdict:
    """Open Facebook and report whether the profile holds a live session."""
    with browser_session(config) as page:
        page.goto(HOME_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        _, verdict = inspect(page, HOME_URL)
        return verdict


def login(config: Config, deadline_seconds: float = 600.0) -> wall.Verdict:
    """Open a window and wait for the user to sign in by hand.

    The credentials are typed by a human into a visible window; this function
    never submits them. It only polls until the login wall is gone.
    """
    with browser_session(config) as page:
        page.goto(HOME_URL, wait_until="domcontentloaded")
        print(
            "Log in to Facebook in the browser window, including any "
            "two-factor step. This window will detect it automatically."
        )
        deadline = time.monotonic() + deadline_seconds
        verdict = wall.Verdict(wall.EMPTY, "not checked yet")
        while time.monotonic() < deadline:
            page.wait_for_timeout(2000)
            _, verdict = inspect(page, HOME_URL)
            if verdict.kind == wall.CHECKPOINT:
                raise BlockedError(verdict)
            if verdict.kind != wall.LOGIN_WALL:
                print("Session established. Saving profile and closing.")
                page.wait_for_timeout(2000)
                return verdict
        raise CaptureError(
            "Login was not completed in time. Run `crawler login` again."
        )


@dataclass(frozen=True)
class CaptureOptions:
    max_seconds: float = DEFAULT_MAX_SECONDS
    max_candidates: int = MAX_CANDIDATE_POSTS
    scroll_pause_ms: int = SCROLL_PAUSE_MS
    jitter_ms: int = 900


def human_scroll(page, options: CaptureOptions, rng: random.Random) -> None:
    """Scroll in irregular steps, then pause for an irregular time.

    A fixed 2400px jump every 1500ms is a machine signature. Real reading is
    uneven, occasionally backtracks, and never lands on the same interval
    twice.
    """
    for _ in range(rng.randint(2, 4)):
        page.mouse.wheel(0, rng.randint(500, 1100))
        page.wait_for_timeout(rng.randint(120, 380))
    if rng.random() < 0.2:
        page.mouse.wheel(0, -rng.randint(120, 400))
        page.wait_for_timeout(rng.randint(200, 500))
    page.wait_for_timeout(
        options.scroll_pause_ms + rng.randint(0, max(1, options.jitter_ms))
    )


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
    may remove earlier DOM nodes. A snapshot that turns out to be a wall is
    still committed first, as evidence, and then stops the run.
    """
    options = options or CaptureOptions()
    should_stop = should_stop or (lambda: False)
    rng = random.Random()

    from . import db
    from .parser import parse

    with browser_session(config) as page:
        page.goto(page_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        deadline = time.monotonic() + options.max_seconds
        candidates_seen: set[str] = set()
        first = True

        while time.monotonic() < deadline and not should_stop():
            html, verdict = inspect(page, page_url)
            captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            db.save_snapshot(conn, run_id, page_url, captured_at, html)

            if verdict.blocking:
                # Committed above so the wall is on record, then stop. Never
                # keep scrolling a wall: it stores nothing and looks like a bot.
                raise BlockedError(verdict)
            if first and verdict.kind == wall.EMPTY:
                print(f"warning: {verdict.message}")
            first = False

            yield captured_at, html
            posts, _ = parse(html, page_url, datetime.now(timezone.utc))
            candidates_seen.update(post.post_id for post in posts)
            if len(candidates_seen) >= options.max_candidates:
                break
            human_scroll(page, options, rng)


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
