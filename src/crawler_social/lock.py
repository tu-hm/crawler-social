"""Portable single-writer file lock based on POSIX fcntl.flock.

Kept in its own module so the locking behavior is testable without a browser.
flock is available on both supported platforms (macOS and Linux).
"""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path


class LockBusyError(RuntimeError):
    """Raised when another process already holds the lock."""


class FileLock:
    """Exclusive lock on a lock file, held until released or closed."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self, blocking: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise LockBusyError(
                    f"Another crawler process holds the lock at {self.path}."
                ) from exc
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
