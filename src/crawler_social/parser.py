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
from dataclasses import dataclass
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
    #: The post's own permalink when the article carried one. The comment
    #: pass navigates to it; None means the caller falls back to a
    #: constructed `<page>/posts/<id>` URL.
    url: Optional[str] = None


@dataclass(frozen=True)
class Comment:
    comment_id: str
    post_id: str
    author: Optional[str]
    text: Optional[str]
    published_at: Optional[str]
    like_count: Optional[int]
    #: 1-based position in Facebook's own ordering on the permalink page,
    #: which defaults to "most relevant" -- so rank 1 is the top comment.
    rank: int


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

#: Seconds per relative-time unit. Vietnamese units are here because the
#: Pages this crawler is pointed at render their timestamps in Vietnamese;
#: an unrecognized unit silently costs the post its published_at.
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
        seconds = _RELATIVE_SECONDS[match.group(2).lower()]
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


#: Hosts whose links may be treated as a permalink. `l.facebook.com` is the
#: outbound redirector and is deliberately absent: its `u=` parameter can
#: carry any URL at all.
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

#: Tried in order, so a real permalink wins over a photo or video link that
#: happens to point at the same story.
_PERMALINK_HREF_PATTERNS = [
    re.compile(r"/permalink\.php\?"),
    re.compile(r"/posts/"),
    re.compile(r"story_fbid="),
    re.compile(r"/videos/"),
    re.compile(r"/reel/"),
    re.compile(r"/photos?/"),
]

#: Facebook decorates every feed link with tracking parameters. They are
#: long, they are per-session, and keeping them would make the same post
#: look like a different URL on every capture.
_DROPPED_QUERY_KEYS = frozenset(
    {"_rdr", "ref", "refsrc", "fref", "hc_location", "rdid", "share_url", "notif_t"}
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


def _extract_post_url(article: Tag) -> Optional[str]:
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


def _candidate_articles(soup: BeautifulSoup) -> list[Tag]:
    """Post-shaped nodes only.

    Comments use `role="article"` too, and a permalink snapshot is full of
    them. Their aria-label is the only thing that separates the two, so it
    is checked here -- otherwise re-parsing a permalink snapshot would file
    every comment as a post.
    """
    articles = [
        node
        for node in soup.find_all("div", attrs={"role": "article"})
        if not _COMMENT_LABEL.match(str(node.get("aria-label") or ""))
    ]
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
                url=_extract_post_url(article),
            )
        )
    return posts, diagnostics


# --- comments -------------------------------------------------------------
#
# Comment markup is the most obfuscated part of a Facebook page and the part
# most likely to change. Everything below is deliberately best-effort: a
# node that yields nothing becomes a Diagnostic rather than an exception,
# so a comment this parser cannot read costs one diagnostic and not the
# post it hangs under. Captures are not stored, so an improved parser
# applies to the next crawl, never to an old one.

#: A comment's container is `role="article"` like a post's; only the
#: aria-label tells them apart.
_COMMENT_LABEL = re.compile(
    r"^\s*(comment|reply|commentaire|réponse|bình luận|phản hồi|trả lời)\b",
    re.I,
)

_COMMENT_AUTHOR_FROM_LABEL = re.compile(
    r"^\s*(?:comment|reply|commentaire|réponse|bình luận|phản hồi|trả lời)\s+"
    r"(?:by|from|de|của)\s+(.+?)\s*(?:,|$)",
    re.I,
)

#: A relative age tacked onto the end of an aria-label.
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

#: Button and label text that lives inside a comment node but is chrome,
#: not content.
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


def _comment_nodes(soup: BeautifulSoup) -> list[Tag]:
    """Top-level comment containers, replies excluded.

    A reply is nested inside its parent comment, so dropping every node
    that descends from another matched node removes reply threads
    structurally -- without relying on the aria-label wording to
    distinguish "Reply by X" from "Comment by X", which it does not do
    consistently across Facebook's surfaces.
    """
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
    identities = {id(node) for node in matched}
    return [
        node
        for node in matched
        if not any(id(parent) in identities for parent in node.parents)
    ]


def _strip_nested_articles(node: Tag) -> Tag:
    """A copy of the comment with its reply threads removed.

    Every field extractor below walks descendants, so without this a
    parent comment would absorb its replies' text, author, likes and id.
    Replies are a non-goal (plans/v3/00, D4); dropping them once here keeps
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
    # No id in the markup: derive a stable one from the content so a repeat
    # run updates the same row instead of inserting a duplicate. Editing the
    # comment changes the digest, and therefore creates a new row.
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
        # "Comment by Alice Nguyen 2 hours ago" -- the label runs the name
        # and the age together, so the age has to come back off.
        name = _TRAILING_AGE.sub("", match.group(1)).strip(" ,")
        if name:
            return name
    return None


def _extract_comment_time(node: Tag, captured_at: datetime) -> Optional[str]:
    stamped = _extract_published_at(node, captured_at)
    if stamped:
        return stamped
    # Comments carry their age as the text of an ordinary link ("2 h",
    # "1 ngày"), not in an <abbr> or <time>.
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
    return None


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
) -> tuple[list[Comment], list[Diagnostic]]:
    """Parse the top-level comments out of one captured permalink page.

    Ranks are document order, which on a permalink page is Facebook's own
    "most relevant" ordering -- so rank 1 is the top comment. `limit` caps
    how many are returned, and is applied after replies are excluded.
    """
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    comments: list[Comment] = []
    diagnostics: list[Diagnostic] = []
    for index, node in enumerate(_comment_nodes(soup)):
        if limit is not None and len(comments) >= limit:
            break
        node = _strip_nested_articles(node)
        author = _extract_comment_author(node)
        text = _extract_comment_text(node, author)
        if not text:
            diagnostics.append(
                Diagnostic(reason="no comment text", context=f"{post_id} #{index + 1}")
            )
            continue
        comments.append(
            Comment(
                comment_id=_extract_comment_id(node, post_id, author, text),
                post_id=post_id,
                author=author,
                text=text,
                published_at=_extract_comment_time(node, captured_at),
                like_count=_extract_comment_likes(node),
                rank=len(comments) + 1,
            )
        )
    return comments, diagnostics
