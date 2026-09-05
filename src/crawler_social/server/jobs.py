"""The single crawl job slot for the viewer.

Design constraints (plans/v2/08):
- `run_crawl` installs SIGINT/SIGTERM handlers with `signal.signal`, which
  only works in the main thread of a process -- so the viewer never runs
  the pipeline in-process. It spawns `crawler crawl` as a subprocess with
  a list argv and shell=False.
- One crawl at a time: this flag guards the common case; the FileLock
  profile lock still guards against a crawl started from a terminal.
- Output is tailed by a reader thread into a bounded ring buffer so a
  runaway crawl cannot exhaust memory.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

from ..facebook import check_gui_session

#: Only these hosts may be crawled: the URL becomes a subprocess argument
#: and a browser navigation, so arbitrary input is refused.
ALLOWED_HOSTS = frozenset({"facebook.com", "www.facebook.com", "m.facebook.com"})
#: Ring buffer ceiling in lines.
MAX_LINES = 500
#: Grace period after SIGTERM before a stop escalates to SIGKILL.
STOP_GRACE_SECONDS = 10


class JobRefused(Exception):
    """start() refused; the message is fit to show the user."""


def validate_page_url(page_url: str) -> str:
    """Accept only https URLs on the Facebook host allowlist."""
    candidate = page_url.strip()
    parts = urlsplit(candidate)
    if parts.scheme != "https":
        raise JobRefused(
            f"Only https:// URLs are accepted; got {parts.scheme or 'no'} scheme."
        )
    if parts.netloc.lower() not in ALLOWED_HOSTS:
        raise JobRefused(
            "Only facebook.com, www.facebook.com, or m.facebook.com pages "
            f"are accepted; got {parts.netloc!r}."
        )
    return candidate


class CrawlJob:
    """State for the at-most-one crawl started from the UI."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._page_url: Optional[str] = None
        self._limit: int = 0
        self._started_at: Optional[float] = None
        self._lines: list[str] = []
        self._lines_dropped: int = 0
        self._exit_code: Optional[int] = None
        self._stop_requested: bool = False
        self._stop_sent_at: Optional[float] = None

    # -- lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        """Clear all state. For tests; production keeps one long-lived job."""
        with self._lock:
            self._process = None
            self._page_url = None
            self._limit = 0
            self._started_at = None
            self._lines = []
            self._lines_dropped = 0
            self._exit_code = None
            self._stop_requested = False
            self._stop_sent_at = None

    def start(
        self,
        page_url: str,
        limit: int,
        *,
        comments: int = 0,
        comments_max_posts: int | None = None,
        env: dict | None = None,
    ) -> None:
        """Spawn `crawler crawl` for page_url, or raise JobRefused."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise JobRefused(
                    "A crawl is already running. Wait for it to finish or stop it first."
                )
            self.reset_locked()

        page_url = validate_page_url(page_url)
        try:
            check_gui_session(env)
        except Exception as exc:  # CaptureError -- message names DISPLAY etc.
            raise JobRefused(str(exc)) from exc

        argv = [
            sys.executable,
            "-m",
            "crawler_social.cli",
            "crawl",
            page_url,
            "--limit",
            str(int(limit)),
        ]
        if comments:
            argv += ["--comments", str(int(comments))]
            if comments_max_posts:
                argv += ["--comments-max-posts", str(int(comments_max_posts))]
        # A list argv with shell=False: the URL is one argument, never a
        # shell string, so metacharacters cannot be interpreted.
        proc = subprocess.Popen(  # noqa: S603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
        )
        with self._lock:
            self._process = proc
            self._page_url = page_url
            self._limit = int(limit)
            self._started_at = time.monotonic()
            self._stop_requested = False
            self._stop_sent_at = None
        threading.Thread(target=self._pump, args=(proc,), daemon=True).start()

    def stop(self) -> bool:
        """SIGTERM first; a later call after the grace period SIGKILLs."""
        with self._lock:
            proc = self._process
            if proc is None or proc.poll() is not None:
                return False
            now = time.monotonic()
            if self._stop_sent_at is None:
                self._stop_requested = True
                self._stop_sent_at = now
                proc.send_signal(signal.SIGTERM)
            elif now - self._stop_sent_at > STOP_GRACE_SECONDS:
                proc.kill()
        return True

    # -- reporting ---------------------------------------------------------

    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def status(self) -> dict:
        with self._lock:
            proc = self._process
            is_running = proc is not None and proc.poll() is None
            if proc is not None and not is_running and self._exit_code is None:
                self._exit_code = proc.returncode
            elapsed = (
                time.monotonic() - self._started_at
                if self._started_at is not None
                else None
            )
            return {
                "running": is_running,
                "page_url": self._page_url,
                "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
                "lines": list(self._lines[-MAX_LINES:]),
                "lines_dropped": self._lines_dropped,
                "exit_code": self._exit_code,
                "stop_requested": self._stop_requested,
                # A terminal crawl that hit the FileLock prints this; shown
                # to the user as a readable note instead of a raw log.
                "lock_busy": any("holds the lock" in line for line in self._lines),
            }

    # -- internals ---------------------------------------------------------

    def reset_locked(self) -> None:
        """Reset everything except the process slot; caller holds the lock."""
        self._page_url = None
        self._limit = 0
        self._started_at = None
        self._lines = []
        self._lines_dropped = 0
        self._exit_code = None
        self._stop_requested = False
        self._stop_sent_at = None

    def _pump(self, proc) -> None:
        """Reader thread body: stdout (+merged stderr) into the ring buffer."""
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            with self._lock:
                self._lines.append(line)
                if len(self._lines) > MAX_LINES:
                    drop = len(self._lines) - MAX_LINES
                    del self._lines[:drop]
                    self._lines_dropped += drop


#: The one job slot. The server process has exactly one at any time.
crawl_job = CrawlJob()
