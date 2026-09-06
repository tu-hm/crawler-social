"""Pure formatting helpers shared by the server-rendered views.

Nothing here touches the database or a request; functions take stored
values and return display-ready strings. `highlight` is the one helper
allowed to return HTML: it escapes first and adds <mark> around the
matches afterwards, so its output is always fully escaped text plus the
wrapper tags -- no database value ever reaches a template unescaped.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from markupsafe import Markup


def format_bytes(n: float) -> str:
    """Byte count as a display string. The ladder tops out at TB.

    The last unit is the catch-all, so a value beyond it reads as e.g.
    "2048.0 TB" rather than falling out of the ladder.
    """
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if abs(n) < 1024 or unit == units[-1]:
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} {units[-1]}"


def excerpt(text: str | None, length: int = 180) -> str:
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= length:
        return collapsed
    return collapsed[: max(length - 1, 0)].rstrip() + "…"


def snippet(text: str | None, term: str | None, length: int = 180) -> str:
    """An excerpt centred on the first match of `term`.

    `excerpt` always takes the head of the text, so a search hit past the
    cut-off produced a row with no visible reason it matched. This keeps
    the window over the match instead, marking each trimmed end with an
    ellipsis. With no term, or no match, it degrades to `excerpt`.
    """
    if not text or not term:
        return excerpt(text, length)
    collapsed = " ".join(text.split())
    if len(collapsed) <= length:
        return collapsed
    found = collapsed.lower().find(term.lower())
    if found == -1:
        return excerpt(collapsed, length)
    # Keep a third of the window as lead-in so the match is not flush left.
    lead = max(length // 3, 0)
    start = max(0, min(found - lead, len(collapsed) - length))
    end = start + length
    return (
        ("…" if start > 0 else "")
        + collapsed[start:end].strip()
        + ("…" if end < len(collapsed) else "")
    )


def relative_age(stored: str | None, *, now: datetime | None = None) -> str:
    """Relative age ("3h ago") of a stored UTC timestamp.

    Used for title attributes so the table keeps the exact stored time as
    its text. Stored timestamps are ISO-8601; a value that fails to parse
    degrades to an empty string instead of breaking the page.
    """
    if not stored:
        return ""
    try:
        dt = datetime.fromisoformat(stored)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    seconds = int((now - dt.astimezone(timezone.utc)).total_seconds())
    future = seconds < 0
    seconds = abs(seconds)
    if seconds < 90:
        text = f"{seconds}s"
    elif seconds < 90 * 60:
        text = f"{round(seconds / 60)}m"
    elif seconds < 36 * 3600:
        text = f"{round(seconds / 3600)}h"
    elif seconds < 18 * 86400:
        text = f"{round(seconds / 86400)}d"
    else:
        text = f"{round(seconds / (7 * 86400))}w"
    return f"in {text}" if future else f"{text} ago"


def duration(
    started: str | None, finished: str | None = None, *, now: datetime | None = None
) -> str:
    """Elapsed time between two stored UTC timestamps ("3m 24s").

    Without a finished time (a run still going) the duration runs up to
    `now`, so the table can show a live elapsed value. Unparseable input
    degrades to an empty string.
    """
    if not started:
        return ""
    try:
        start_dt = datetime.fromisoformat(started)
        end_dt = (
            datetime.fromisoformat(finished)
            if finished
            else (now or datetime.now(timezone.utc))
        )
    except ValueError:
        return ""
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    seconds = max(
        0, int((end_dt.astimezone(timezone.utc) - start_dt.astimezone(timezone.utc)).total_seconds())
    )
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600:02d}h"


def run_is_stale(run: dict, *, now: datetime | None = None) -> bool:
    """True for a `running` row with no finish time, started over an hour ago.

    Such a run almost certainly died without updating its row. Display
    only -- the server never rewrites the row.
    """
    if run.get("status") != "running" or run.get("finished_at"):
        return False
    started = run.get("started_at")
    if not started:
        return False
    try:
        start_dt = datetime.fromisoformat(started)
    except ValueError:
        return False
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - start_dt.astimezone(timezone.utc)).total_seconds() > 3600


def highlight(text: str | None, term: str | None) -> Markup:
    """Escape-first server-side highlighting.

    The raw text is split around case-insensitive matches of `term`, every
    fragment is escaped, and the escaped match is wrapped in <mark>.
    Matching runs on raw text so escape sequences (e.g. ``&amp;``) can
    never produce false matches, and no part of the output is unescaped.
    """
    raw = text or ""
    if not term:
        return Markup(html.escape(raw))
    pattern = re.compile(re.escape(term), re.IGNORECASE)
    parts: list[str] = []
    last = 0
    for match in pattern.finditer(raw):
        parts.append(html.escape(raw[last : match.start()]))
        parts.append(f"<mark>{html.escape(match.group(0))}</mark>")
        last = match.end()
    parts.append(html.escape(raw[last:]))
    return Markup("".join(parts))
