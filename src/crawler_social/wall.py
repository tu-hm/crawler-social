"""Content-based classification of a captured Facebook page.

The old check looked only at `page.url`. Facebook serves its login gate for a
Page at the *same* URL with no redirect, so a URL check never fires and the
crawler scrolls a login wall for the full time budget, storing snapshots that
contain no posts.

This module looks at the bytes instead. It is pure and offline -- same inputs,
same verdict -- so it can be tested against saved fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

OK = "ok"
LOGIN_WALL = "login_wall"
CHECKPOINT = "checkpoint"
RATE_LIMITED = "rate_limited"
UNAVAILABLE = "unavailable"
EMPTY = "empty"

BLOCKING = frozenset({LOGIN_WALL, CHECKPOINT, RATE_LIMITED, UNAVAILABLE})

MIN_ARTICLE_TEXT = 30


@dataclass(frozen=True)
class Verdict:
    """What the captured page actually is, and why we think so."""

    kind: str
    reason: str
    article_count: int = 0

    @property
    def blocking(self) -> bool:
        return self.kind in BLOCKING

    @property
    def message(self) -> str:
        return _MESSAGES.get(self.kind, self.reason)


_MESSAGES = {
    LOGIN_WALL: (
        "Facebook served its login wall instead of the page. The browser "
        "profile has no valid session. Run `crawler login` to sign in by "
        "hand, then crawl again."
    ),
    CHECKPOINT: (
        "Facebook is showing a checkpoint or identity confirmation. Stop: do "
        "not bypass it. Open the profile with `crawler login` and clear it "
        "manually, then wait before crawling again."
    ),
    RATE_LIMITED: (
        "Facebook is rate limiting or temporarily blocking this session. Stop "
        "and back off; crawling harder makes this worse."
    ),
    UNAVAILABLE: (
        "Facebook says this content is not available. Check the Page URL, and "
        "whether the session is entitled to see it."
    ),
    EMPTY: (
        "The page rendered no post-shaped content. It may still be loading, "
        "or the markup may have changed and the parser needs updating."
    ),
}

# URL substrings: corroboration only, never the sole check.
_CHECKPOINT_URL = ("/checkpoint", "captcha", "two_factor", "confirmemail")
_LOGIN_URL = ("/login.php", "/login/", "login.facebook.com", "/recover/")

# Visible-text signals, matched after <script>/<style> are stripped so
# Facebook's JS bundles cannot trip them.
_CHECKPOINT_TEXT = (
    "confirm your identity",
    "we need to confirm",
    "your account has been locked",
    "we've temporarily locked",
    "suspicious activity",
    "unusual activity",
    "please verify your identity",
    "security check required",
    "enter the code we sent",
)

_RATE_LIMIT_TEXT = (
    "you're temporarily blocked",
    "you are temporarily blocked",
    "temporarily restricted",
    "misusing this feature",
    "going too fast",
    "please try again later",
    "you have been blocked from",
)

_UNAVAILABLE_TEXT = (
    "this content isn't available",
    "this content is no longer available",
    "this page isn't available",
    "content not found",
    "page not found",
    "sorry, this content isn't available",
)

_LOGIN_TEXT = (
    "log in to facebook",
    "log into facebook",
    "forgotten password",
    "forgot password",
    "forgotten account",
    "create new account",
    "email address or mobile number",
    "email address or phone number",
    "you must log in to continue",
)


def visible_text(soup: BeautifulSoup) -> str:
    """Page text with script and style content removed."""
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    return soup.get_text(" ", strip=True)


def _has_password_input(soup: BeautifulSoup) -> bool:
    """A rendered password field. Logged-in feeds do not have one."""
    if soup.find("input", attrs={"type": "password"}):
        return True
    return bool(soup.find("input", attrs={"name": re.compile(r"^pass$", re.I)}))


def count_articles(soup: BeautifulSoup) -> int:
    """Article nodes carrying enough text to be a real post."""
    total = 0
    for node in soup.find_all(attrs={"role": "article"}):
        if len(node.get_text(" ", strip=True)) >= MIN_ARTICLE_TEXT:
            total += 1
    return total


def _match(haystack: str, needles: tuple[str, ...]) -> Optional[str]:
    for needle in needles:
        if needle in haystack:
            return needle
    return None


def classify(html: bytes, url: str = "") -> Verdict:
    """Decide what the captured bytes actually are.

    Blocking states are checked before content, because a checkpoint page can
    still carry leftover article markup underneath the interstitial.
    """
    soup = BeautifulSoup(html or b"", "html.parser")
    articles = count_articles(soup)
    text = visible_text(soup).lower()
    lowered_url = (url or "").lower()

    hit = _match(lowered_url, _CHECKPOINT_URL) or _match(text, _CHECKPOINT_TEXT)
    if hit:
        return Verdict(CHECKPOINT, f"matched {hit!r}", articles)

    hit = _match(text, _RATE_LIMIT_TEXT)
    if hit:
        return Verdict(RATE_LIMITED, f"matched {hit!r}", articles)

    login_url_hit = _match(lowered_url, _LOGIN_URL)
    login_text_hit = _match(text, _LOGIN_TEXT)
    if login_url_hit or (login_text_hit and _has_password_input(soup)):
        reason = f"matched {login_url_hit or login_text_hit!r}"
        return Verdict(LOGIN_WALL, reason, articles)

    hit = _match(text, _UNAVAILABLE_TEXT)
    if hit and articles == 0:
        return Verdict(UNAVAILABLE, f"matched {hit!r}", articles)

    if articles:
        return Verdict(OK, f"{articles} article node(s) with text", articles)
    return Verdict(EMPTY, "no article node carried text", articles)
