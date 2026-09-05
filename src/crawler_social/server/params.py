"""Shared query-parameter helpers for the HTML pages and the JSON API.

Kept in its own module so `pages.py` and `api.py` can both import it
without a circular import (app -> pages -> api -> app).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_when(value: str, *, end_of_day: bool) -> str:
    """ISO-8601 date or datetime -> the stored UTC string format.

    A bare date means the start of that day for `since` and the end of it
    for `until`, so a date range is inclusive the way a user expects.
    Lexicographic comparison in SQL stays correct because every stored
    timestamp uses this exact format.
    """
    text = value.strip()
    try:
        if _DATE_ONLY.fullmatch(text):
            dt = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end_of_day:
                dt = dt + timedelta(days=1, seconds=-1)
        else:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
    except ValueError:
        raise ValueError(
            "must be an ISO-8601 date or datetime, e.g. 2026-01-01 or "
            "2026-01-01T09:30:00"
        ) from None
    return dt.isoformat(timespec="seconds")
