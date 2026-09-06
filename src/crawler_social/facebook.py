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

#: Every expansion click gets its own short timeout. Playwright's default is
#: 30s; a stuck element must cost a second, not half a minute times a budget.
CLICK_TIMEOUT_MS = 1500
#: How many matches of a label pattern are examined for a visible one.
_SCAN_LIMIT = 25

#: Anchored, so "Xem them binh luan" ("view more comments") cannot match the
#: plain "see more" that expands a body. A leading ellipsis is optional
#: because Facebook renders the truncation mark inside the button on some
#: surfaces ("... More").
SEE_MORE_PATTERN = re.compile(
    r"^\s*(?:\u2026|\.{3})?\s*(?:see\s+more|xem\s+th\u00eam|voir\s+plus|"
    r"mehr\s+anzeigen|ver\s+m\u00e1s|more)\s*$",
    re.IGNORECASE,
)
#: Attributes that mark a control as opening a menu, so it is never a text
#: expander. A group page is full of them: the header kebab, the tab-bar
#: overflow and the per-post action menu all carry the accessible name
#: "Xem them" / "More", which is exactly what SEE_MORE_PATTERN matches.
_MENU_ATTRS = ("aria-haspopup", "aria-expanded")
#: Deliberately unanchored: Facebook renders counts inside the label.
MORE_COMMENTS_PATTERN = re.compile(
    r"(?:view|load|see)\s+(?:\d[\d.,]*\s+)?(?:more\s+|previous\s+)?comments?"
    r"|xem\s+th\u00eam\s+b\u00ecnh\s+lu\u1eadn"
    r"|xem\s+(?:c\u00e1c\s+)?b\u00ecnh\s+lu\u1eadn\s+(?:tr\u01b0\u1edbc|kh\u00e1c)",
    re.IGNORECASE,
)
MORE_REPLIES_PATTERN = re.compile(
    r"(?:view|see)\s+(?:all\s+)?(?:\d[\d.,]*\s+)?(?:more\s+)?repl(?:y|ies)"
    r"|xem\s+(?:th\u00eam\s+)?(?:\d[\d.,]*\s+)?ph\u1ea3n\s+h\u1ed3i",
    re.IGNORECASE,
)

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


def check_gui_session(
    env: dict[str, str] | None = None, *, platform: str | None = None
) -> None:
    """Fail clearly when no logged-in graphical session exists.

    `platform` defaults to the host and exists so the Linux branch can be
    exercised from a test on any machine, the same way
    `resolve_browser_binary` takes one.
    """
    env = env if env is not None else dict(__import__("os").environ)
    platform = platform if platform is not None else sys.platform
    if platform == "darwin":
        return
    if platform.startswith("linux"):
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
    #: Click "See more" so a truncated body reaches the DOM before capture.
    expand_text: bool = True
    max_expand_clicks: int = 12


def _is_text_expander(element) -> bool:
    """True when this button really un-truncates text, not opens a menu.

    `get_by_role(name=...)` matches the *accessible name*, which for an
    icon-only control comes from its `aria-label`. On a group page several
    menu buttons are labelled "Xem them" / "More" and match SEE_MORE_PATTERN
    exactly, so clicking on name alone opens the group options menu instead
    of expanding a post. A genuine expander is different in two ways: it
    carries no menu semantics, and the label is its own visible text.
    """
    try:
        for attr in _MENU_ATTRS:
            if element.get_attribute(attr, timeout=CLICK_TIMEOUT_MS):
                return False
        text = element.inner_text(timeout=CLICK_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 - unreadable element is not clickable
        return False
    return bool(SEE_MORE_PATTERN.search((text or "").strip()))


#: Where readable content lives. Both roles are needed: on a group feed the
#: post bodies sit directly under `role="feed"` and only the *comments* carry
#: `role="article"`, while a permalink page has articles and no feed. Group
#: chrome -- header, tab bar, right rail, chat -- is outside both, and that is
#: where the look-alike menu buttons are.
_CONTENT_ROOTS = '[role="feed"], [role="article"]'


def _in_content(page):
    """Scope a search to the feed/article containers when the page has any."""
    try:
        roots = page.locator(_CONTENT_ROOTS)
        if roots.count():
            return roots
    except Exception:  # noqa: BLE001 - fall back to the whole page
        pass
    return page


def _dismiss_popup(page) -> bool:
    """Close a menu or dialog that a click opened. True when one was closed.

    A belt-and-braces step: if a look-alike button still slips through the
    filter, the menu it opened is closed immediately instead of swallowing
    the clicks that follow.
    """
    try:
        popup = page.locator('[role="menu"], [role="dialog"]').first
        if not popup.is_visible(timeout=CLICK_TIMEOUT_MS):
            return False
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        return True
    except Exception:  # noqa: BLE001 - best effort only
        return False


def _click_repeatedly(
    page,
    pattern: "re.Pattern[str]",
    *,
    max_clicks: int,
    rng: random.Random,
    settle_ms: int = 250,
    on_click: Callable[[], bool] | None = None,
    accept: Callable[[object], bool] | None = None,
    scope: Callable[[object], object] | None = None,
) -> int:
    """Click every visible button whose accessible name matches `pattern`.

    Defensive on purpose: each click mutates the DOM, so the match set is
    re-queried every time; every Playwright call carries an explicit short
    timeout; a failure on one element is swallowed and the next one tried.

    Only `role="button"` is clicked (D2), so an expansion click can never be
    a link that navigates away from the page being captured.

    `scope` narrows where matches are looked for and `accept` vets each
    candidate before it is clicked; both default to no restriction.

    Returns the number of clicks that landed.
    """
    clicks = 0
    while clicks < max_clicks:
        try:
            root = scope(page) if scope is not None else page
            matches = root.get_by_role("button", name=pattern)
            total = min(matches.count(), _SCAN_LIMIT)
        except Exception:  # noqa: BLE001 - a dead locator ends the loop
            break
        landed = False
        for index in range(total):
            element = matches.nth(index)
            try:
                if not element.is_visible(timeout=CLICK_TIMEOUT_MS):
                    continue
                if accept is not None and not accept(element):
                    continue
                element.click(timeout=CLICK_TIMEOUT_MS, no_wait_after=True)
            except Exception:  # noqa: BLE001 - stale or covered; try the next
                continue
            clicks += 1
            landed = True
            page.wait_for_timeout(settle_ms + rng.randint(0, 200))
            if on_click is not None and not on_click():
                return clicks
            break
        if not landed:
            break
    return clicks


def _same_page(current: str | None, expected: str) -> bool:
    """True when `current` is still the page we were capturing."""
    if not current:
        return False
    return current.split("#", 1)[0].rstrip("/") == expected.split("#", 1)[0].rstrip("/")


def expand_post_text(
    page,
    options: CaptureOptions,
    rng: random.Random,
    *,
    url: str | None = None,
) -> int:
    """Click the "See more" buttons on screen so full bodies reach the DOM.

    The text behind "See more" is genuinely absent from the DOM until the
    button is clicked, so this has to happen at capture time -- the parser
    cannot recover it later.
    """
    if not options.expand_text or options.max_expand_clicks <= 0:
        return 0
    expected = url or page.url or ""

    def still_here() -> bool:
        _dismiss_popup(page)
        if not expected:
            return True
        if _same_page(page.url, expected):
            return True
        # A click navigated: undo it and stop expanding rather than keep
        # clicking on whatever page we landed on.
        try:
            page.go_back(timeout=5000, wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001 - best effort; the caller re-inspects
            pass
        return False

    return _click_repeatedly(
        page,
        SEE_MORE_PATTERN,
        max_clicks=options.max_expand_clicks,
        rng=rng,
        on_click=still_here,
        accept=_is_text_expander,
        scope=_in_content,
    )


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
    still committed first, as evidence, and then stops the run. What is
    committed is the capture's metadata; the markup itself is never stored,
    and reaches the caller in memory only, which is what the parser reads.
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
            # Expand before reading the DOM: the hidden tail of a long post
            # is not in `page.content()` until "See more" has been clicked.
            expand_post_text(page, options, rng, url=page_url)
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


DEFAULT_COMMENT_MAX_POSTS = 10


@dataclass(frozen=True)
class CommentOptions:
    #: Comments wanted per post. 0 disables the pass entirely (D7).
    top_n: int = 0
    #: Hard ceiling on permalink navigations in one run.
    max_posts: int = DEFAULT_COMMENT_MAX_POSTS
    max_more_clicks: int = 6
    max_expand_clicks: int = 20
    #: After goto, before touching anything.
    settle_ms: int = 2500
    #: Between posts, jittered.
    pause_ms: int = 2000
    max_seconds: float = 300.0


@dataclass(frozen=True)
class CommentCapture:
    """One permalink visit. `error` set means nothing was captured."""

    post_id: str
    post_url: str
    captured_at: str | None = None
    html: bytes | None = None
    error: str | None = None


def post_permalink(page_url: str, post_id: str) -> str:
    """Fallback permalink for a post whose article carried no usable link."""
    base = page_url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return f"{base}/posts/{post_id}"


def _visible_comment_count(page) -> int:
    """Best-effort count of comment articles currently in the DOM."""
    try:
        return page.locator('div[role="article"][aria-label]').count()
    except Exception:  # noqa: BLE001 - counting must never break the pass
        return 0


def _expand_comments(page, options: CommentOptions, rng: random.Random) -> None:
    """Load more comments, then un-truncate the bodies that are showing."""
    seen = _visible_comment_count(page)
    clicks = 0
    while clicks < options.max_more_clicks and seen < options.top_n + 1:
        landed = _click_repeatedly(
            page,
            MORE_COMMENTS_PATTERN,
            max_clicks=1,
            rng=rng,
            settle_ms=900,
        )
        if not landed:
            break
        clicks += landed
        grown = _visible_comment_count(page)
        if grown <= seen:
            # The click did not add anything; more clicking will not either.
            break
        seen = grown

    # Comment bodies truncate behind the same "See more" as post bodies.
    _click_repeatedly(
        page,
        SEE_MORE_PATTERN,
        max_clicks=options.max_expand_clicks,
        rng=rng,
        accept=_is_text_expander,
        scope=_in_content,
    )


def capture_comments(
    targets,
    conn,
    run_id: int,
    config: Config,
    options: CommentOptions | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Iterator[CommentCapture]:
    """Visit each post's permalink and yield committed comment snapshots.

    `targets` is `[(post_id, permalink_url), ...]`, already capped by the
    caller. One browser session serves the whole pass (D5).

    A per-post failure is *yielded* as a record with `error` set rather than
    raised, because one bad permalink must not cost the whole pass (D6). A
    wall is different: the snapshot commits as evidence and BlockedError
    stops the run, exactly as the feed loop does.
    """
    options = options or CommentOptions()
    should_stop = should_stop or (lambda: False)
    targets = list(targets)
    if options.top_n <= 0 or not targets:
        return
    rng = random.Random()

    from . import db

    with browser_session(config, quiet=True) as page:
        deadline = time.monotonic() + options.max_seconds
        for index, (post_id, post_url) in enumerate(targets):
            if should_stop() or time.monotonic() >= deadline:
                break
            if index:
                page.wait_for_timeout(
                    options.pause_ms + rng.randint(0, options.pause_ms)
                )
            try:
                page.goto(post_url, wait_until="domcontentloaded")
                page.wait_for_timeout(options.settle_ms)
            except BrowserClosedError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad permalink only
                yield CommentCapture(post_id, post_url, error=f"goto failed: {exc}")
                continue

            html, verdict = inspect(page, post_url)
            captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if verdict.blocking:
                db.save_snapshot(conn, run_id, post_url, captured_at, html)
                raise BlockedError(verdict)

            try:
                _expand_comments(page, options, rng)
            except BrowserClosedError:
                raise
            except Exception as exc:  # noqa: BLE001 - expansion is optional
                yield CommentCapture(
                    post_id, post_url, error=f"expand failed: {exc}"
                )
                continue

            html, verdict = inspect(page, post_url)
            captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            # Record before parse: the capture is on the run's books here,
            # and only then does the caller parse the bytes.
            db.save_snapshot(conn, run_id, post_url, captured_at, html)
            if verdict.blocking:
                raise BlockedError(verdict)
            yield CommentCapture(post_id, post_url, captured_at, html)
