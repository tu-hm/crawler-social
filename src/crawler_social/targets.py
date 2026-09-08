"""Read a list of Facebook URLs to crawl out of a plain text file.

The file is written by hand, so it is read forgivingly: headings ("Groups:",
"Page:"), blank lines and `#` comments are ignored, and what is left has to
look like a Facebook URL to survive. Order is kept and duplicates are dropped,
because the order is the order the crawl visits them in.

    Groups:

    https://www.facebook.com/groups/VNOIForum
    https://www.facebook.com/groups/IffIndianFootballFans

    Page:
    https://www.facebook.com/vnoi.wiki
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

#: Hosts a target may name. Deliberately narrow: this file is fed straight to
#: a browser, and a crawl of some other site is never what was meant.
FACEBOOK_HOSTS = frozenset(
    {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "web.facebook.com",
        "mbasic.facebook.com",
        "fb.com",
        "www.fb.com",
    }
)


class TargetsError(ValueError):
    """The targets file could not be read, or held no usable URL."""


def normalize_target(raw: str) -> str | None:
    """One tidy https Facebook URL, or None when `raw` is not one.

    The fragment and query string go: a target is a feed to open, and the
    tracking parameters a copied URL carries would file the same group under
    two different `page_url` values in the database.
    """
    candidate = raw.strip().strip("<>\"'").rstrip(",;")
    if not candidate or candidate.startswith("#"):
        return None
    if "//" not in candidate:
        # A bare "facebook.com/vnoi.wiki" is still a target; anything with no
        # host at all ("Groups:") is a heading and drops out below.
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https"):
        return None
    if parts.netloc.lower() not in FACEBOOK_HOSTS:
        return None
    path = parts.path.rstrip("/")
    if not path or path == "/":
        # The bare home feed is never a crawl target: it is personalised,
        # unbounded, and nothing about it is reproducible.
        return None
    return urlunsplit(("https", "www.facebook.com", path, "", ""))


def parse_targets(text: str) -> list[str]:
    """Every Facebook URL in `text`, in order, without duplicates."""
    seen: set[str] = set()
    targets: list[str] = []
    for line in text.splitlines():
        url = normalize_target(line)
        if url is None or url in seen:
            continue
        seen.add(url)
        targets.append(url)
    return targets


def load_targets(path: Path | str) -> list[str]:
    """Read a targets file, or fail with a message naming the file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TargetsError(f"Could not read targets file {path}: {exc}") from exc
    targets = parse_targets(text)
    if not targets:
        raise TargetsError(
            f"No Facebook URLs found in {path}. Put one URL per line, for "
            "example https://www.facebook.com/groups/VNOIForum."
        )
    return targets
