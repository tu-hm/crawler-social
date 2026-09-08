"""Pure, offline parser for captured Facebook Page HTML.

The parser is independent of the browser, OS, database, secrets, environment,
and wall clock: it only sees bytes, a page URL, and the capture timestamp
(used solely to resolve an explicitly relative timestamp).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag


@dataclass(frozen=True)
class Post:
    post_id: str
    page_url: str
    text: Optional[str]
    author: Optional[str]
    published_at: Optional[str]
    #: Permalink from the article; None means the caller constructs one.
    url: Optional[str] = None
    #: Total reactions across every emotion. None means Facebook showed no
    #: count, which it omits entirely for a story nobody has reacted to.
    reaction_count: Optional[int] = None


@dataclass(frozen=True)
class Comment:
    comment_id: str
    post_id: str
    author: Optional[str]
    text: Optional[str]
    published_at: Optional[str]
    like_count: Optional[int]
    #: 1-based rank in Facebook's "most relevant" order, so 1 is the top.
    #: Top-level comments rank among themselves; a reply ranks among the
    #: replies to its own parent, so rank 1 means "first reply", not "first
    #: comment on the post".
    rank: int
    #: The comment this one replies to; None for a top-level comment.
    parent_comment_id: Optional[str] = None


@dataclass(frozen=True)
class Diagnostic:
    reason: str
    context: str


_POST_ID_PATTERNS = [
    # Group feeds expose the story id only as `set=gm.<id>` on an attachment
    # link; it is the most specific signal, so it is tried first.
    re.compile(r"set=gm\.(\d+)"),
    re.compile(r"story_fbid=(\d+)"),
    # Modern permalinks carry an opaque id ('/posts/pfbid02Mc3…') rather than
    # a numeric one. It is stable across runs, which a content hash is not.
    re.compile(r"/posts/(pfbid[A-Za-z0-9]+)"),
    re.compile(r"story_fbid=(pfbid[A-Za-z0-9]+)"),
    re.compile(r"/posts/(?:\d+_)?(\d+)"),
    re.compile(r"/(?:photos|videos|reel)/(?:[^\"']*/)?(?:[^\"']*/)?(\d{8,})"),
    re.compile(r"fbid=(\d+)"),
    re.compile(r'"top_level_post_id"\s*:\s*"(\d+)"'),
    re.compile(r'"post_id"\s*:\s*"(\d+)"'),
]

#: Modern group and Page feeds wrap every story in a div carrying
#: `aria-posinset` and render its parts with `data-ad-rendering-role`.
#: `role="article"` is left to comments alone, so post detection cannot
#: rely on it any more.
_FEED_UNIT_ATTR = "aria-posinset"
_RENDERING_ROLE = "data-ad-rendering-role"

_RELATIVE_PATTERNS = [
    (re.compile(r"^just now$", re.I), timedelta(0)),
]

#: Vietnamese units included: the target Pages render timestamps in Vietnamese.
_RELATIVE_SECONDS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "giây": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "phút": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "giờ": 3600, "tiếng": 3600,
    "d": 86400, "day": 86400, "days": 86400, "ngày": 86400,
    "w": 604800, "week": 604800, "weeks": 604800, "tuần": 604800,
    "mo": 2592000, "month": 2592000, "months": 2592000, "tháng": 2592000,
    "y": 31536000, "yr": 31536000, "year": 31536000, "years": 31536000,
    "năm": 31536000,
}

_RELATIVE_UNIT = re.compile(
    r"^\s*(\d+)\s*(" + "|".join(sorted(_RELATIVE_SECONDS, key=len, reverse=True))
    + r")\s*(?:ago|trước)?\s*$",
    re.I,
)

_RELATIVE_WORDS = {
    "yesterday": timedelta(days=1),
    "hôm qua": timedelta(days=1),
    "a minute ago": timedelta(minutes=1),
    "an hour ago": timedelta(hours=1),
    "a day ago": timedelta(days=1),
    "a week ago": timedelta(weeks=1),
    "few seconds ago": timedelta(seconds=5),
    "vừa xong": timedelta(0),
}

_FB_DATE_FORMATS = [
    "%A, %B %d, %Y at %I:%M %p",
    "%B %d, %Y at %I:%M %p",
    "%m/%d/%Y %I:%M %p",
    "%Y-%m-%dT%H:%M:%S%z",
]

# --- Hover annotations -----------------------------------------------------
#
# A feed carries neither a story's permalink nor its timestamp: Facebook fills
# both in only once the pointer is over the timestamp link. The capture layer
# hovers each story and writes what appeared onto the story node as the three
# attributes below, so this parser keeps reading nothing but bytes.

#: Permalink, absolute, as Facebook filled the href in on hover.
PERMALINK_ATTR = "data-crawler-permalink"
#: The hover tooltip, verbatim -- "8 Tháng 9, 2025 lúc 14:32".
TIMESTAMP_ATTR = "data-crawler-timestamp"
#: `Date.getTimezoneOffset()` in the capturing browser: minutes *behind* UTC,
#: so Asia/Ho_Chi_Minh (UTC+7) reports -420. The tooltip is rendered in that
#: zone, and without the offset there is no way back to UTC.
TZ_OFFSET_ATTR = "data-crawler-tz-offset"

_MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
    # Facebook renders Vietnamese months numerically ("Tháng 9"), but a
    # profile set to spelled-out months gets these.
    "tháng giêng": 1, "tháng một": 1, "tháng hai": 2, "tháng ba": 3,
    "tháng tư": 4, "tháng năm": 5, "tháng sáu": 6, "tháng bảy": 7,
    "tháng tám": 8, "tháng chín": 9, "tháng mười": 10,
    "tháng mười một": 11, "tháng mười hai": 12, "tháng chạp": 12,
}

#: "14:32", "2:32 PM", "2:32 CH" (Vietnamese PM), "2:32 SA" (AM).
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm|sa|ch)?\b", re.I)
_PM_MARKERS = frozenset({"pm", "ch"})

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
#: "8 Tháng 9, 2025" -- the form the Vietnamese tooltip actually uses.
_DAY_NUMERIC_MONTH = re.compile(
    r"\b(\d{1,2})\s+tháng\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?", re.I
)
#: "8 September 2025"
_DAY_NAMED_MONTH = re.compile(
    r"\b(\d{1,2})\s+([^\W\d_]+(?:\s+[^\W\d_]+){0,2}?)\s*,?\s*(\d{4})?\b",
    re.I,
)
#: "September 8, 2025"
_NAMED_MONTH_DAY = re.compile(
    r"\b([^\W\d_]+(?:\s+[^\W\d_]+){0,2}?)\s+(\d{1,2})\s*,?\s*(\d{4})?\b",
    re.I,
)

_TODAY_WORDS = ("hôm nay", "today")
_YESTERDAY_WORDS = ("hôm qua", "yesterday")


def _clock(text: str) -> tuple[int, int]:
    """Hour and minute from a tooltip, defaulting to midnight."""
    match = _CLOCK.search(text)
    if not match:
        return 0, 0
    hour, minute = int(match.group(1)), int(match.group(2))
    marker = (match.group(3) or "").lower()
    if marker in _PM_MARKERS and hour < 12:
        hour += 12
    elif marker and marker not in _PM_MARKERS and hour == 12:
        hour = 0
    return min(hour, 23), min(minute, 59)


def _month_number(name: str) -> Optional[int]:
    return _MONTH_NAMES.get(" ".join(name.lower().split()))


def _calendar_date(text: str, captured_local: datetime) -> Optional[tuple[int, int, int]]:
    """(year, month, day) from a tooltip, or None when it carries no date.

    A tooltip inside the current year omits the year, so it is taken from the
    capture -- and rolled back one year when that would put the post in the
    future, which is what "31 Tháng 12" means when captured in January.
    """
    match = _ISO_DATE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    lowered = text.lower()
    for word in _TODAY_WORDS:
        if word in lowered:
            return captured_local.year, captured_local.month, captured_local.day
    for word in _YESTERDAY_WORDS:
        if word in lowered:
            day = captured_local - timedelta(days=1)
            return day.year, day.month, day.day

    day = month = None
    year: Optional[int] = None
    match = _DAY_NUMERIC_MONTH.search(text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else None
    else:
        for pattern, day_first in ((_DAY_NAMED_MONTH, True), (_NAMED_MONTH_DAY, False)):
            for candidate in pattern.finditer(text):
                raw_day = candidate.group(1 if day_first else 2)
                raw_month = candidate.group(2 if day_first else 1)
                number = _month_number(raw_month)
                if number is None:
                    continue
                day, month = int(raw_day), number
                year = int(candidate.group(3)) if candidate.group(3) else None
                break
            if month is not None:
                break

    if day is None or month is None or not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    if year is None:
        year = captured_local.year
        if (month, day) > (captured_local.month, captured_local.day):
            year -= 1
    return year, month, day


def _parse_absolute(
    text: str, captured_at: datetime, tz_offset_minutes: int = 0
) -> Optional[datetime]:
    """UTC datetime from a hover tooltip rendered in the browser's own zone.

    `tz_offset_minutes` is `Date.getTimezoneOffset()`: minutes behind UTC, so
    UTC+7 reports -420 and the local wall clock is `utc - offset`.
    """
    text = " ".join((text or "").split())
    if not text:
        return None
    shift = timedelta(minutes=tz_offset_minutes)
    captured_local = captured_at - shift
    date = _calendar_date(text, captured_local)
    if date is None:
        return None
    hour, minute = _clock(text)
    try:
        local = datetime(date[0], date[1], date[2], hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None
    return local + shift


def _tz_offset(node: Tag) -> int:
    """Capture-time `getTimezoneOffset()`, read off the annotated story."""
    raw = node.get(TZ_OFFSET_ATTR)
    if raw is None:
        holder = node.find(attrs={TZ_OFFSET_ATTR: True})
        raw = holder.get(TZ_OFFSET_ATTR) if holder is not None else None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return 0


#: The hover pass's own attributes, stripped before any regex reads the
#: markup as a flat string: an annotation is trusted only after
#: `_clean_permalink` has vetted it, and a blind scan would skip that.
_ANNOTATION_ATTRS = re.compile(
    r'\s(?:'
    + "|".join((PERMALINK_ATTR, TIMESTAMP_ATTR, TZ_OFFSET_ATTR, "data-crawler-hovered"))
    + r')="[^"]*"'
)


def _markup_without_annotations(node: Tag) -> str:
    return _ANNOTATION_ATTRS.sub("", str(node))


def _annotated(node: Tag, attribute: str) -> Optional[str]:
    """An annotation on the story node itself, or on the first descendant."""
    raw = node.get(attribute)
    if raw is None:
        holder = node.find(attrs={attribute: True})
        raw = holder.get(attribute) if holder is not None else None
    raw = str(raw).strip() if raw is not None else ""
    return raw or None


def _extract_post_id(article: Tag) -> Optional[str]:
    # A hovered permalink names the story outright, which is why the hover
    # pass exists: without it most group stories fall back to a content hash
    # and can never be visited for their comments.
    hovered = _annotated(article, PERMALINK_ATTR)
    # Cleaned first: the id is read out of the path, so a href pointing
    # somewhere other than Facebook must not get to name a Facebook post.
    cleaned = _clean_permalink(hovered) if hovered else None
    if cleaned:
        for pattern in _POST_ID_PATTERNS:
            match = pattern.search(cleaned)
            if match:
                return match.group(1)
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
    article_html = _markup_without_annotations(article)
    for pattern in _POST_ID_PATTERNS:
        match = pattern.search(article_html)
        if match:
            return match.group(1)
    aria = article.get("aria-labelledby") or ""
    if aria.startswith("fbfeed_post_"):
        return aria[len("fbfeed_post_"):]
    return None


#: Truncation markers Facebook appends to a story it has clipped. They are
#: cut before hashing so a post keeps one identity whether or not the "See
#: more" click landed on that particular run.
_SEE_MORE_SUFFIX = re.compile(
    r"(?:\s|\u2026|\.{3})*(?:see more|xem th\u00eam|see translation|xem b\u1ea3n d\u1ecbch)\s*$",
    re.I,
)

#: Marks an id this parser derived rather than read out of the markup.
#: URL-safe on purpose: these ids end up in viewer paths like /posts/<id>.
SYNTHETIC_ID_PREFIX = "h-"


def is_synthetic_post_id(post_id: str) -> bool:
    """True for an id derived from content, which no permalink can be built from."""
    return post_id.startswith(SYNTHETIC_ID_PREFIX)


def _synthetic_post_id(page_url: str, author: Optional[str], text: str) -> str:
    """Stable identity for a story whose markup carries no Facebook id.

    Group feeds render most stories with no story id anywhere in the served
    HTML -- Facebook fills the permalink in only on hover -- so without this
    every such post is dropped. The digest deliberately excludes the
    timestamp, which is relative ("4m") and therefore different on every
    capture: hashing it would file one post as a new post per run.
    """
    body = _SEE_MORE_SUFFIX.sub("", " ".join(text.split()))
    payload = "\x1f".join((page_url, author or "", body))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"{SYNTHETIC_ID_PREFIX}{digest}"


def _extract_author(article: Tag) -> Optional[str]:
    name = article.find(attrs={_RENDERING_ROLE: "profile_name"})
    if name is not None:
        text = name.get_text(" ", strip=True)
        if text:
            return text
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
        seconds = _RELATIVE_SECONDS[match.group(2).lower()]
        return captured_at - timedelta(seconds=amount * seconds)
    return None


def _extract_published_at(article: Tag, captured_at: datetime) -> Optional[str]:
    # The hover tooltip is the only exact time a feed ever shows, so it beats
    # every relative age below it: "4m" is a minute-resolution guess, this is
    # the minute Facebook itself records.
    tooltip = _annotated(article, TIMESTAMP_ATTR)
    if tooltip:
        resolved = _parse_absolute(tooltip, captured_at, _tz_offset(article))
        if resolved:
            return resolved.isoformat(timespec="seconds")
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
    return _relative_from_links(article, captured_at)


def _relative_from_links(node: Tag, captured_at: datetime) -> Optional[str]:
    """Age read from a plain link or span ("2 giờ", "2 h").

    Facebook's modern surfaces render a story's age as the text of the
    permalink itself rather than in an <abbr> or <time>, so without this a
    post keeps published_at = None and sorts by last_seen instead.
    """
    for tag in node.find_all(["a", "span"], limit=60):
        text = tag.get_text(" ", strip=True)
        if not text or len(text) > 24:
            continue
        resolved = _parse_relative(text, captured_at)
        if resolved:
            return resolved.isoformat(timespec="seconds")
    return None


def _extract_text(article: Tag) -> Optional[str]:
    for selector in (
        {"data-ad-preview": "message"},
        {"data-ad-comet-preview": "message"},
        {_RENDERING_ROLE: "story_message"},
    ):
        node = article.find(attrs=selector)
        if node:
            text = node.get_text("\n", strip=True)
            if text:
                return text
    # A pure link share carries no story message: the only words the author
    # is quoting are the attachment's own description. On a photo- or
    # video-only story that same slot holds an opaque attachment token
    # ("CcPNOGdt0jm8FuDmyaCB5wmugsb6Q"), which is not text anyone posted --
    # so the fallback is taken only when the description reads as prose.
    shared = article.find(attrs={_RENDERING_ROLE: "description"})
    if shared is not None:
        text = shared.get_text("\n", strip=True)
        if text and any(character.isspace() for character in text):
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


#: Permalink hosts. l.facebook.com is deliberately absent: its `u=`
#: parameter can carry any URL.
_PERMALINK_HOSTS = frozenset(
    {
        "",
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "web.facebook.com",
        "mbasic.facebook.com",
    }
)

#: Tried in order, so a real permalink beats a photo or video link to it.
_PERMALINK_HREF_PATTERNS = [
    re.compile(r"/permalink\.php\?"),
    re.compile(r"/posts/"),
    re.compile(r"story_fbid="),
    re.compile(r"/videos/"),
    re.compile(r"/reel/"),
    re.compile(r"/photos?/"),
]

#: Per-session tracking params; keeping them makes one post look like many.
#: `comment_id` rides along too: a feed story surfaced as "X commented on
#: this" hovers to a permalink anchored on that comment, and visiting it
#: scrolls to one reply instead of opening the post at the top.
_DROPPED_QUERY_KEYS = frozenset(
    {
        "_rdr", "ref", "refsrc", "fref", "hc_location", "rdid", "share_url",
        "notif_t", "comment_id", "reply_comment_id", "comment_tracking",
        "notif_id", "paipv", "eav", "_ft_",
    }
)


def _clean_permalink(href: str) -> Optional[str]:
    """Absolute, de-tracked Facebook URL, or None when href is not one."""
    parts = urlsplit(href.strip())
    if parts.scheme and parts.scheme not in ("http", "https"):
        return None
    if parts.netloc.lower() not in _PERMALINK_HOSTS:
        return None
    if not parts.path.startswith("/"):
        return None
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.startswith("__") and key not in _DROPPED_QUERY_KEYS
        ]
    )
    netloc = parts.netloc or "www.facebook.com"
    return urlunsplit(("https", netloc, parts.path, query, ""))


#: The story id a group feed hides on an attachment link.
_GROUP_STORY_ID = re.compile(r"set=gm\.(\d+)")


def _group_story_permalink(page_url: str, article: Tag) -> Optional[str]:
    """`<group>/posts/<id>` for a story whose only id is on a photo link.

    The photo link itself opens the media viewer, not the story, and its
    comment list is not the post's -- so visiting it for comments returns
    nothing. The canonical story URL is built from the id instead.
    """
    match = _GROUP_STORY_ID.search(_markup_without_annotations(article))
    if not match:
        return None
    base = page_url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if "/groups/" not in base:
        return None
    return f"{base}/posts/{match.group(1)}"


def _extract_post_url(article: Tag) -> Optional[str]:
    # A permalink Facebook filled in on hover is the story's own URL. Every
    # href below it is a guess from an attachment, which may open the media
    # viewer rather than the post.
    hovered = _annotated(article, PERMALINK_ATTR)
    if hovered:
        cleaned = _clean_permalink(hovered)
        if cleaned:
            return cleaned
    hrefs = [
        str(link["href"])
        for link in article.find_all("a", href=True)
        if str(link["href"]).strip()
    ]
    for pattern in _PERMALINK_HREF_PATTERNS:
        for href in hrefs:
            if pattern.search(href):
                cleaned = _clean_permalink(href)
                if cleaned:
                    return cleaned
    return None


def _feed_units(soup: BeautifulSoup) -> list[Tag]:
    """Rendered stories from a modern feed.

    A feed unit that has scrolled out of view is virtualized down to an empty
    placeholder that still carries `aria-posinset`, so presence of the
    attribute is not enough: a unit counts only once it has rendered a
    profile name, which every real story does.
    """
    return [
        node
        for node in soup.find_all(attrs={_FEED_UNIT_ATTR: True})
        if node.find(attrs={_RENDERING_ROLE: "profile_name"}) is not None
    ]


def _candidate_articles(soup: BeautifulSoup) -> list[Tag]:
    """Post-shaped nodes only.

    Two feed generations are read here. On the modern feed a story is a
    `aria-posinset` unit and `role="article"` belongs to comments alone; on
    the older markup the post itself was the article. Comments are excluded
    either way -- by their aria-label, and by sitting inside a feed unit --
    because otherwise every comment would be filed as a post.
    """
    articles = _feed_units(soup)
    unit_ids = {id(node) for node in articles}
    seen = set(unit_ids)

    def inside_unit(node: Tag) -> bool:
        return any(id(parent) in unit_ids for parent in node.parents)

    for node in soup.find_all("div", attrs={"role": "article"}):
        if _COMMENT_LABEL.match(str(node.get("aria-label") or "")):
            continue
        if id(node) in seen or inside_unit(node):
            continue
        seen.add(id(node))
        articles.append(node)
    for node in soup.find_all(attrs={"data-ft": True}):
        if id(node) in seen or inside_unit(node):
            continue
        seen.add(id(node))
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
        # Comments sit inside the story they belong to on a modern feed, so
        # they are dropped before any extractor walks the descendants --
        # otherwise a post absorbs its commenters' names, words and times.
        body = _strip_nested_articles(article)
        if not body.get_text(" ", strip=True):
            continue
        text = _extract_text(body)
        if not text:
            diagnostics.append(
                Diagnostic(
                    reason="no text content",
                    context=(article.get("id") or f"candidate #{index}"),
                )
            )
            continue
        author = _extract_author(body)
        published = _extract_published_at(body, captured_at)
        post_id = _extract_post_id(body) or _synthetic_post_id(
            page_url, author, text
        )
        posts.append(
            Post(
                post_id=post_id,
                page_url=page_url,
                text=text,
                author=author,
                published_at=published,
                url=(
                    _group_story_permalink(page_url, body)
                    or _extract_post_url(body)
                ),
                reaction_count=_extract_reaction_count(body),
            )
        )
    return posts, diagnostics


#: Comments share role="article" with posts; only the aria-label differs.
_COMMENT_LABEL = re.compile(
    r"^\s*(comment|reply|commentaire|réponse|bình luận|phản hồi|trả lời)\b",
    re.I,
)

_COMMENT_AUTHOR_FROM_LABEL = re.compile(
    r"^\s*(?:comment|reply|commentaire|réponse|bình luận|phản hồi|trả lời)\s+"
    r"(?:by|from|de|của)\s+(.+?)\s*(?:,|$)",
    re.I,
)

_TRAILING_AGE = re.compile(
    r"\s+\d+\s*(?:" + "|".join(sorted(_RELATIVE_SECONDS, key=len, reverse=True))
    + r")\s*(?:ago|trước)?\s*$",
    re.I,
)

_COMMENT_ID_PATTERNS = [
    re.compile(r"[?&]comment_id=(\d+)"),
    re.compile(r"[?&]reply_comment_id=(\d+)"),
    re.compile(r'"comment_id"\s*:\s*"([^"]+)"'),
    re.compile(r'id="comment_(\d+)"'),
]

_LIKE_COUNT_PATTERNS = [
    re.compile(r"([\d.,]+)\s*(?:reactions?|likes?)\b", re.I),
    re.compile(r"([\d.,]+)\s*(?:lượt thích|người khác)\b", re.I),
]

_COMMENT_CHROME = frozenset(
    {
        "like", "reply", "share", "edited", "author", "top fan", "follow",
        "hide", "report", "see more", "see translation", "most relevant",
        "view more replies", "translate",
        "thích", "trả lời", "chia sẻ", "đã chỉnh sửa", "tác giả",
        "xem thêm", "xem bản dịch", "phù hợp nhất", "theo dõi", "ẩn",
        "báo cáo", "dịch",
    }
)

_COUNT_VALUE = re.compile(r"^(\d+(?:[.,]\d+)*)\s*([kKmM])?$")


def _parse_count(raw: str) -> Optional[int]:
    """"1,234" -> 1234, "1.5K" -> 1500. None when it is not a count."""
    match = _COUNT_VALUE.match(raw.strip())
    if not match:
        return None
    digits, suffix = match.group(1), (match.group(2) or "").lower()
    if suffix:
        try:
            value = float(digits.replace(",", "."))
        except ValueError:
            return None
        return int(value * (1000 if suffix == "k" else 1_000_000))
    try:
        return int(re.sub(r"[.,]", "", digits))
    except ValueError:
        return None


#: The summary control opening the reaction list. Facebook labels it with an
#: explicit total on a comment ("2 reactions; see who reacted to this") but
#: without one on a story, where the total is rendered beside it instead.
_REACTED_LABEL = re.compile(r"who reacted to this", re.I)


def _extract_reaction_count(node: Tag) -> Optional[int]:
    """Total reactions on a story or a comment, or None when none is shown.

    The per-emotion labels ("Like: 99 people", "Haha: 28 people") are
    deliberately not summed: Facebook lists only the leading emotions, so
    they add up to less than the real total.
    """
    anchor = None
    for child in node.find_all(attrs={"aria-label": True}, limit=400):
        label = str(child.get("aria-label"))
        if not _REACTED_LABEL.search(label):
            continue
        for pattern in _LIKE_COUNT_PATTERNS:
            match = pattern.search(label)
            if match:
                count = _parse_count(match.group(1))
                if count is not None:
                    return count
        if anchor is None:
            anchor = child
    if anchor is None:
        return None
    # No total in the label: it sits next to the control, in a bar that also
    # carries the comment and share counts, reactions first.
    container = anchor
    for _ in range(6):
        container = container.parent
        if container is None:
            return None
        if container.get_text(" ", strip=True):
            break
    else:
        return None
    for child in container.find_all(["span", "div"]):
        count = _parse_count(child.get_text(" ", strip=True))
        if count is not None:
            return count
    return None


def _matched_comment_nodes(soup: BeautifulSoup) -> list[Tag]:
    """Every comment container on the page, replies included, in document order."""
    matched = [
        node
        for node in soup.find_all(attrs={"role": "article"})
        if _COMMENT_LABEL.match(str(node.get("aria-label") or ""))
    ]
    if not matched:
        matched = [
            node
            for node in soup.find_all("div", id=True)
            if str(node.get("id")).startswith("comment_")
        ]
    return matched


def _comment_tree(soup: BeautifulSoup) -> list[tuple[Tag, Optional[Tag]]]:
    """(comment, parent) pairs in document order; parent is None at top level.

    Threading is read from the DOM rather than from the aria-label: a reply
    is nested inside the comment it answers, and that nesting is consistent
    across Facebook's surfaces in a way the "Reply by X" / "Comment by X"
    wording is not. Because a parent's opening tag precedes its replies,
    document order guarantees a parent is always seen before its children.
    """
    matched = _matched_comment_nodes(soup)
    identities = {id(node) for node in matched}
    pairs: list[tuple[Tag, Optional[Tag]]] = []
    for node in matched:
        parent = None
        for ancestor in node.parents:
            if id(ancestor) in identities:
                parent = ancestor
                break
        pairs.append((node, parent))
    return pairs


def _comment_nodes(soup: BeautifulSoup) -> list[Tag]:
    """Top-level comment containers only, replies excluded."""
    return [node for node, parent in _comment_tree(soup) if parent is None]


def _strip_nested_articles(node: Tag) -> Tag:
    """A copy of the comment with its reply threads removed.

    Every field extractor below walks descendants, so without this a
    parent comment would absorb its replies' text, author, likes and id.
    Replies are a non-goal; dropping them once here keeps
    every extractor simple.
    """
    clone = copy.copy(node)
    for nested in clone.find_all(attrs={"role": "article"}):
        nested.decompose()
    for nested in clone.find_all("div", id=True):
        if str(nested.get("id")).startswith("comment_"):
            nested.decompose()
    return clone


def _extract_comment_id(node: Tag, post_id: str, author: Optional[str], text: Optional[str]) -> str:
    node_html = str(node)
    for pattern in _COMMENT_ID_PATTERNS:
        match = pattern.search(node_html)
        if match:
            return match.group(1)
    # No id in the markup: hash the content so a repeat run updates the
    # same row. An edit changes the digest, and so lands as a new row.
    digest = hashlib.blake2s(
        f"{author or ''}\x00{text or ''}".encode("utf-8"), digest_size=8
    ).hexdigest()
    return f"{post_id}:h{digest}"


def _extract_comment_author(node: Tag) -> Optional[str]:
    for tag in node.find_all(["a", "strong", "span"], limit=12):
        text = tag.get_text(" ", strip=True)
        if text and len(text) < 80 and text.lower() not in _COMMENT_CHROME:
            return text
    match = _COMMENT_AUTHOR_FROM_LABEL.match(str(node.get("aria-label") or ""))
    if match:
        # The label runs name and age together: "Comment by Alice 2 hours ago".
        name = _TRAILING_AGE.sub("", match.group(1)).strip(" ,")
        if name:
            return name
    return None


def _extract_comment_time(node: Tag, captured_at: datetime) -> Optional[str]:
    stamped = _extract_published_at(node, captured_at)
    if stamped:
        return stamped
    # Comment ages sit in ordinary link text ("2 h"), not in <abbr> or <time>.
    for tag in node.find_all(["a", "span"], limit=40):
        resolved = _parse_relative(tag.get_text(" ", strip=True), captured_at)
        if resolved:
            return resolved.isoformat(timespec="seconds")
    return None


def _extract_comment_likes(node: Tag) -> Optional[int]:
    labels = [str(node.get("aria-label") or "")]
    labels.extend(
        str(child.get("aria-label"))
        for child in node.find_all(attrs={"aria-label": True}, limit=40)
    )
    for label in labels:
        for pattern in _LIKE_COUNT_PATTERNS:
            match = pattern.search(label)
            if match:
                count = _parse_count(match.group(1))
                if count is not None:
                    return count
    return _extract_reaction_count(node)


def _extract_comment_text(node: Tag, author: Optional[str]) -> Optional[str]:
    parts: list[str] = []
    for child in node.find_all("div", attrs={"dir": "auto"}):
        text = child.get_text(" ", strip=True)
        if not text or text in parts:
            continue
        lowered = text.lower()
        if lowered in _COMMENT_CHROME:
            continue
        if author and text == author:
            continue
        if _parse_count(text) is not None:
            continue
        if _RELATIVE_UNIT.match(text) or lowered in _RELATIVE_WORDS:
            continue
        parts.append(text)
    if not parts:
        return None
    return "\n".join(parts)


def parse_comments(
    html: bytes,
    post_id: str,
    captured_at: datetime,
    limit: Optional[int] = None,
    *,
    include_replies: bool = True,
) -> tuple[list[Comment], list[Diagnostic]]:
    """Parse one captured permalink page into a threaded comment list.

    Comments come back in document order, which on a permalink page is
    Facebook's own "most relevant" ordering, so the first top-level comment
    is the top comment. A reply carries its parent's id and ranks among its
    siblings, so `rank` is read together with `parent_comment_id`: rank 1
    means "first top-level comment" or "first reply to that comment"
    depending on which is set.

    `limit` caps **top-level** comments; the replies to a comment that made
    the cut come back with it, and a reply never uses up the budget. Replies
    to a comment past the limit are dropped with their parent.
    """
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    comments: list[Comment] = []
    diagnostics: list[Diagnostic] = []
    # Facebook renders the comment list twice on a permalink page -- once
    # live and once in a second copy -- so the same comment is matched twice.
    # Keeping the first occurrence preserves Facebook's ordering, and stops
    # rank and the run's comment tally from counting every comment double.
    seen: set[str] = set()
    #: id(node) -> stored comment_id, so a reply can name its parent. Keyed by
    #: the *original* node: the clone handed to the extractors is a different
    #: object and has no place in the tree.
    stored: dict[int, str] = {}
    #: How many children each parent has taken, plus top level under None.
    ranks: dict[Optional[str], int] = {}

    for index, (node, parent_node) in enumerate(_comment_tree(soup)):
        parent_id: Optional[str] = None
        if parent_node is not None:
            if not include_replies:
                continue
            parent_id = stored.get(id(parent_node))
            if parent_id is None:
                # The parent was dropped -- past the limit, unparseable, or a
                # duplicate already seen. Its replies go with it rather than
                # being re-parented to the post.
                continue
        elif limit is not None and ranks.get(None, 0) >= limit:
            continue

        body = _strip_nested_articles(node)
        author = _extract_comment_author(body)
        text = _extract_comment_text(body, author)
        if not text:
            diagnostics.append(
                Diagnostic(reason="no comment text", context=f"{post_id} #{index + 1}")
            )
            continue
        comment_id = _extract_comment_id(body, post_id, author, text)
        if comment_id in seen:
            continue
        seen.add(comment_id)
        stored[id(node)] = comment_id
        ranks[parent_id] = ranks.get(parent_id, 0) + 1
        comments.append(
            Comment(
                comment_id=comment_id,
                post_id=post_id,
                author=author,
                text=text,
                published_at=_extract_comment_time(body, captured_at),
                like_count=_extract_comment_likes(body),
                rank=ranks[parent_id],
                parent_comment_id=parent_id,
            )
        )
    return comments, diagnostics


def parse_post_detail(
    html: bytes,
    page_url: str,
    post_id: str,
    captured_at: datetime,
) -> tuple[Optional[Post], list[Diagnostic]]:
    """Parse the story out of one captured permalink page.

    A permalink page shows the post whole: the body is not clipped to a
    preview, the reaction bar carries the settled total, and the timestamp is
    the story's own rather than the feed's relative age. That makes this the
    authoritative read of a post, and the feed merely how it was found.

    The returned Post keeps the caller's `post_id` and `page_url` -- the feed
    the post belongs to -- so a hydrated post updates the row the feed pass
    created instead of opening a second one.
    """
    posts, diagnostics = parse(html, page_url, captured_at)
    if not posts:
        return None, diagnostics
    # A permalink page can render neighbouring stories ("suggested for you")
    # below the one asked for, so the matching id wins outright and the
    # longest body is the fallback: the story being viewed is the one shown
    # in full.
    chosen = next(
        (post for post in posts if post.post_id == post_id),
        max(posts, key=lambda post: len(post.text or "")),
    )
    return (
        replace(chosen, post_id=post_id, page_url=page_url),
        diagnostics,
    )
