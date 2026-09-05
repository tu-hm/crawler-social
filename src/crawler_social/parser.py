"""Pure, offline parser for captured Facebook Page HTML.

The parser is independent of the browser, OS, database, secrets, environment,
and wall clock: it only sees bytes, a page URL, and the capture timestamp
(used solely to resolve an explicitly relative timestamp).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from bs4 import BeautifulSoup, Tag


@dataclass(frozen=True)
class Post:
    post_id: str
    page_url: str
    text: Optional[str]
    author: Optional[str]
    published_at: Optional[str]


@dataclass(frozen=True)
class Diagnostic:
    reason: str
    context: str


_POST_ID_PATTERNS = [
    re.compile(r"story_fbid=(\d+)"),
    re.compile(r"/posts/(?:\d+_)?(\d+)"),
    re.compile(r"/(?:photos|videos|reel)/(?:[^\"']*/)?(?:[^\"']*/)?(\d{8,})"),
    re.compile(r"fbid=(\d+)"),
    re.compile(r'"top_level_post_id"\s*:\s*"(\d+)"'),
    re.compile(r'"post_id"\s*:\s*"(\d+)"'),
]

_RELATIVE_PATTERNS = [
    (re.compile(r"^just now$", re.I), timedelta(0)),
]

_RELATIVE_UNIT = re.compile(
    r"^\s*(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)\s*(?:ago)?\s*$",
    re.I,
)

_RELATIVE_WORDS = {
    "yesterday": timedelta(days=1),
    "a minute ago": timedelta(minutes=1),
    "an hour ago": timedelta(hours=1),
    "a day ago": timedelta(days=1),
    "a week ago": timedelta(weeks=1),
    "few seconds ago": timedelta(seconds=5),
}

_FB_DATE_FORMATS = [
    "%A, %B %d, %Y at %I:%M %p",
    "%B %d, %Y at %I:%M %p",
    "%m/%d/%Y %I:%M %p",
    "%Y-%m-%dT%H:%M:%S%z",
]


def _extract_post_id(article: Tag, html: str) -> Optional[str]:
    for attr in ("data-ft",):
        raw = article.get(attr)
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                pid = data.get("top_level_post_id") or data.get("post_id")
                if pid:
                    return str(pid)
            except (ValueError, AttributeError):
                pass
    article_html = str(article)
    for pattern in _POST_ID_PATTERNS:
        match = pattern.search(article_html)
        if match:
            return match.group(1)
    aria = article.get("aria-labelledby") or ""
    if aria.startswith("fbfeed_post_"):
        return aria[len("fbfeed_post_"):]
    return None


def _extract_author(article: Tag) -> Optional[str]:
    header = article.find(["h2", "h3", "h4"])
    if header:
        link = header.find("a")
        if link:
            text = link.get_text(" ", strip=True)
            if text:
                return text
        text = header.get_text(" ", strip=True)
        if text:
            return text
    strong = article.find("strong")
    if strong:
        text = strong.get_text(" ", strip=True)
        if text:
            return text
    return None


def _parse_relative(text: str, captured_at: datetime) -> Optional[datetime]:
    text = text.strip().lower()
    for pattern, delta in _RELATIVE_PATTERNS:
        if pattern.match(text):
            return captured_at + delta
    if text in _RELATIVE_WORDS:
        return captured_at - _RELATIVE_WORDS[text]
    match = _RELATIVE_UNIT.match(text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        seconds = {
            "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
            "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
            "d": 86400, "day": 86400, "days": 86400,
            "w": 604800, "week": 604800, "weeks": 604800,
        }[unit]
        return captured_at - timedelta(seconds=amount * seconds)
    return None


def _extract_published_at(article: Tag, captured_at: datetime) -> Optional[str]:
    stamp = article.find(attrs={"data-utime": True})
    if stamp is not None:
        try:
            epoch = int(stamp["data-utime"])
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
        except (ValueError, KeyError, TypeError, OSError):
            pass
    for abbr in article.find_all(["abbr", "time"]):
        title = abbr.get("title")
        if title:
            title = str(title)
            for fmt in _FB_DATE_FORMATS:
                try:
                    parsed = datetime.strptime(title, fmt)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed.isoformat(timespec="seconds")
                except ValueError:
                    continue
            resolved = _parse_relative(title, captured_at)
            if resolved:
                return resolved.isoformat(timespec="seconds")
        text = abbr.get_text(" ", strip=True)
        if text:
            resolved = _parse_relative(text, captured_at)
            if resolved:
                return resolved.isoformat(timespec="seconds")
    return None


def _extract_text(article: Tag) -> Optional[str]:
    for selector in (
        {"data-ad-preview": "message"},
        {"data-ad-comet-preview": "message"},
    ):
        node = article.find("div", attrs=selector)
        if node:
            text = node.get_text("\n", strip=True)
            if text:
                return text
    seen: list[str] = []
    for node in article.find_all("div", attrs={"dir": "auto"}):
        if node.find_parent(attrs={"data-ad-preview": "message"}):
            continue
        text = node.get_text(" ", strip=True)
        if text and len(text) > 1 and text not in seen:
            seen.append(text)
    if not seen:
        return None
    return "\n".join(seen)


def _candidate_articles(soup: BeautifulSoup) -> list[Tag]:
    articles = soup.find_all("div", attrs={"role": "article"})
    seen = {id(node) for node in articles}
    for node in soup.find_all(attrs={"data-ft": True}):
        if id(node) not in seen:
            articles.append(node)
    return articles


def parse(
    html: bytes,
    page_url: str,
    captured_at: datetime,
) -> tuple[list[Post], list[Diagnostic]]:
    """Parse one captured snapshot into candidate posts.

    Returns (posts, diagnostics). Malformed candidate nodes are skipped with a
    diagnostic; a missing author or time becomes None without failing the
    snapshot.
    """
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    posts: list[Post] = []
    diagnostics: list[Diagnostic] = []
    for index, article in enumerate(_candidate_articles(soup)):
        post_id = _extract_post_id(article, str(article))
        if not post_id:
            diagnostics.append(
                Diagnostic(
                    reason="no post id found",
                    context=(article.get("id") or f"candidate #{index}"),
                )
            )
            continue
        text = _extract_text(article)
        if not text:
            diagnostics.append(
                Diagnostic(reason="no text content", context=post_id)
            )
            continue
        author = _extract_author(article)
        published = _extract_published_at(article, captured_at)
        posts.append(
            Post(
                post_id=post_id,
                page_url=page_url,
                text=text,
                author=author,
                published_at=published,
            )
        )
    return posts, diagnostics
