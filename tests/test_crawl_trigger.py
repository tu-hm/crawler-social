"""Tests for the crawl trigger.

Every test patches subprocess.Popen -- no test launches a real browser
or a real crawl.
"""

from __future__ import annotations

import io
import re
import signal
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crawler_social.facebook import CaptureError
from crawler_social.server import jobs, pages as pages_module
from crawler_social.server.jobs import JobRefused
from tests.conftest import make_config, make_db

HTTPS_PAGE = "https://www.facebook.com/ExamplePublicPage"
#: Satisfies the real check_gui_session without a desktop.
DESKTOP_ENV = {"DISPLAY": ":0"}


class FakeProc:
    """Enough of Popen for the job runner: poll/send_signal/kill/stdout."""

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self.returncode = None
        self._poll_result: int | None = None
        self.signals: list[signal.Signals] = []
        self.killed = False
        self.stdout = io.BytesIO(b"")
        Recorder.calls.append((argv, kwargs))

    def poll(self):
        return self._poll_result

    def wait(self, timeout=None):
        # The stop watchdog needs wait() to really block, or escalation
        # would look instant.
        if self._poll_result is None and timeout is not None:
            time.sleep(timeout)
        if self._poll_result is None:
            if timeout is not None:
                raise subprocess.TimeoutExpired(self.argv, timeout)
            return None
        return self._poll_result

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.killed = True

    def finish(self, code: int = 0) -> None:
        self._poll_result = code
        self.returncode = code


class Recorder:
    calls: list = []


@pytest.fixture(autouse=True)
def fresh_job(monkeypatch):
    """Isolate the module-level job; record Popen calls; no real GUI need."""
    jobs.crawl_job.reset()
    Recorder.calls = []
    monkeypatch.setattr(jobs.subprocess, "Popen", FakeProc)
    # jobs.check_gui_session stays real so the pass-through test below
    # exercises it.
    monkeypatch.setattr(pages_module, "check_gui_session", lambda env=None: None)
    yield
    jobs.crawl_job.reset()
    Recorder.calls = []


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    make_db(tmp_path / "social.db", posts=[], runs=[])
    from crawler_social.server.app import create_app

    return TestClient(create_app(make_config(tmp_path / "social.db")))


def _csrf_token(client: TestClient) -> str:
    page = client.get("/crawl")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match, "csrf token missing from the crawl form"
    return match.group(1)


def test_start_refuses_when_a_job_is_already_running():
    jobs.crawl_job.start(HTTPS_PAGE, 5, env=DESKTOP_ENV)
    assert len(Recorder.calls) == 1
    with pytest.raises(JobRefused, match="already running"):
        jobs.crawl_job.start(HTTPS_PAGE, 5, env=DESKTOP_ENV)
    assert len(Recorder.calls) == 1


def test_start_refuses_non_https_and_foreign_hosts():
    with pytest.raises(JobRefused, match="https"):
        jobs.crawl_job.start("http://facebook.com/x", 5, env=DESKTOP_ENV)
    with pytest.raises(JobRefused, match="facebook.com"):
        jobs.crawl_job.start("https://evil.example/x", 5, env=DESKTOP_ENV)
    assert Recorder.calls == []


def test_start_accepts_https_facebook_url():
    jobs.crawl_job.start(HTTPS_PAGE, 7, env=DESKTOP_ENV)
    argv, kwargs = Recorder.calls[0]
    assert argv == [
        sys.executable, "-m", "crawler_social.cli", "crawl",
        HTTPS_PAGE, "--limit", "7",
    ]
    assert kwargs["shell"] is False


def test_shell_metacharacters_stay_one_argument():
    tricky = "https://facebook.com/x?q=; rm -rf ~"
    jobs.crawl_job.start(tricky, 5, env=DESKTOP_ENV)
    argv, kwargs = Recorder.calls[0]
    assert tricky in argv
    assert kwargs["shell"] is False


def test_gui_check_on_linux_names_display():
    # Pinned to the linux branch: macOS has no DISPLAY and returns early.
    from crawler_social.facebook import check_gui_session

    with pytest.raises(CaptureError, match="DISPLAY"):
        check_gui_session({}, platform="linux")
    # ...and the darwin branch is deliberately permissive.
    check_gui_session({}, platform="darwin")


def test_gui_failure_is_passed_through_as_job_refused(monkeypatch):
    def refuse(env=None, **kwargs):
        raise CaptureError("No graphical session found: DISPLAY is empty.")

    monkeypatch.setattr(jobs, "check_gui_session", refuse)
    with pytest.raises(JobRefused, match="DISPLAY"):
        jobs.crawl_job.start(HTTPS_PAGE, 5, env={})
    assert Recorder.calls == []


def test_post_without_csrf_token_is_403_and_starts_nothing(client: TestClient):
    resp = client.post("/crawl", data={"page_url": HTTPS_PAGE, "limit": "5"})
    assert resp.status_code == 403
    assert Recorder.calls == []


def test_post_with_foreign_origin_is_403(client: TestClient):
    token = _csrf_token(client)
    resp = client.post(
        "/crawl",
        data={"page_url": HTTPS_PAGE, "limit": "5", "csrf_token": token},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert Recorder.calls == []


def test_post_with_matching_origin_starts_the_crawl(client: TestClient):
    token = _csrf_token(client)
    resp = client.post(
        "/crawl",
        data={"page_url": HTTPS_PAGE, "limit": "5", "csrf_token": token},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert len(Recorder.calls) == 1
    assert Recorder.calls[0][0][4] == HTTPS_PAGE


def test_post_refusal_renders_the_reason(client: TestClient, monkeypatch):
    class StubJob:
        def start(self, url, limit, **kwargs):
            raise JobRefused("No graphical session: DISPLAY is empty.")

        def status(self):
            return {
                "running": False, "page_url": None, "elapsed_seconds": None,
                "lines": [], "lines_dropped": 0, "exit_code": None,
                "stop_requested": False, "lock_busy": False,
            }

    monkeypatch.setattr(pages_module, "crawl_job", StubJob())
    token = _csrf_token(client)
    resp = client.post(
        "/crawl",
        data={"page_url": HTTPS_PAGE, "limit": "5", "csrf_token": token},
        headers={"Origin": "http://testserver"},
    )
    # 409, not 200: nothing was started, so this is not a page view.
    assert resp.status_code == 409
    assert "DISPLAY is empty" in resp.text
    assert Recorder.calls == []


def test_get_crawl_never_starts_a_job(client: TestClient):
    resp = client.get("/crawl")
    assert resp.status_code == 200
    assert Recorder.calls == []


def test_status_reports_running_then_exit_code():
    jobs.crawl_job.start(HTTPS_PAGE, 5, env=DESKTOP_ENV)
    fake = jobs.crawl_job._process
    assert isinstance(fake, FakeProc)
    status = jobs.crawl_job.status()
    assert status["running"] is True
    assert status["elapsed_seconds"] >= 0
    assert status["exit_code"] is None

    fake.finish(3)
    status = jobs.crawl_job.status()
    assert status["running"] is False
    assert status["exit_code"] == 3


def test_api_crawl_status_endpoint(client: TestClient):
    jobs.crawl_job.start(HTTPS_PAGE, 5, env=DESKTOP_ENV)
    fake = jobs.crawl_job._process
    data = client.get("/api/crawl/status").json()
    assert data["running"] is True
    assert data["page_url"] == HTTPS_PAGE
    fake.finish(0)
    data = client.get("/api/crawl/status").json()
    assert data["running"] is False
    assert data["exit_code"] == 0


def test_stop_sends_sigterm_first_not_sigkill():
    jobs.crawl_job.start(HTTPS_PAGE, 5, env=DESKTOP_ENV)
    fake = jobs.crawl_job._process
    assert jobs.crawl_job.stop() is True
    assert fake.signals == [signal.SIGTERM]
    assert fake.killed is False
    jobs.crawl_job.stop()
    assert fake.signals == [signal.SIGTERM]
    assert fake.killed is False
    # 11 > the 10-second grace, so the next stop escalates to SIGKILL.
    jobs.crawl_job._stop_sent_at = time.monotonic() - 11
    jobs.crawl_job.stop()
    assert fake.killed is True


def test_stop_escalates_on_its_own_after_the_grace_period(monkeypatch):
    # Regression: escalation needed a second stop() the UI never made.
    monkeypatch.setattr(jobs, "STOP_GRACE_SECONDS", 0.05)
    jobs.crawl_job.start(HTTPS_PAGE, 5, env=DESKTOP_ENV)
    fake = jobs.crawl_job._process
    assert jobs.crawl_job.stop() is True
    assert fake.signals == [signal.SIGTERM]
    deadline = time.monotonic() + 3
    while not fake.killed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fake.killed is True, "the watchdog never escalated to SIGKILL"


def test_stop_does_not_kill_a_crawl_that_exits_within_the_grace_period(monkeypatch):
    monkeypatch.setattr(jobs, "STOP_GRACE_SECONDS", 0.5)
    jobs.crawl_job.start(HTTPS_PAGE, 5, env=DESKTOP_ENV)
    fake = jobs.crawl_job._process
    jobs.crawl_job.stop()
    fake.finish(0)
    time.sleep(0.7)
    assert fake.killed is False


def test_stop_is_a_noop_when_nothing_runs():
    assert jobs.crawl_job.stop() is False


def test_ring_buffer_caps_at_500_lines():
    job = jobs.CrawlJob()
    job._pump(types.SimpleNamespace(stdout=io.BytesIO(b"line\n" * 5000)))
    status = job.status()
    assert len(status["lines"]) == jobs.MAX_LINES == 500
    assert status["lines_dropped"] == 4500


def test_lock_busy_from_output_is_surfaced(client: TestClient):
    jobs.crawl_job.start(HTTPS_PAGE, 5, env=DESKTOP_ENV)
    job = jobs.crawl_job
    fake = job._process
    with job._lock:
        job._lines = ["Another crawler process holds the lock at /tmp/x.lock."]
    fake.finish(1)
    page = client.get("/crawl")
    assert "holds the profile lock" in page.text


def test_comments_are_appended_to_the_argv_only_when_asked_for():
    jobs.crawl_job.start(HTTPS_PAGE, 7, env=DESKTOP_ENV)
    argv, _ = Recorder.calls[0]
    assert "--comments" not in argv

    jobs.crawl_job.reset()
    Recorder.calls = []
    jobs.crawl_job.start(
        HTTPS_PAGE, 7, comments=5, comments_max_posts=3, env=DESKTOP_ENV
    )
    argv, kwargs = Recorder.calls[0]
    assert argv[-4:] == ["--comments", "5", "--comments-max-posts", "3"]
    assert kwargs["shell"] is False


def test_the_form_clamps_the_comment_count(client: TestClient):
    """It becomes an argv entry and a permalink visit per post."""
    started = {}

    class StubJob:
        def start(self, url, limit, **kwargs):
            started.update(kwargs)

        def stop(self):
            return False

        def status(self):
            return {
                "running": False, "page_url": None, "elapsed_seconds": None,
                "lines": [], "lines_dropped": 0, "exit_code": None,
                "stop_requested": False, "lock_busy": False,
            }

    monkeypatched = StubJob()
    original = pages_module.crawl_job
    pages_module.crawl_job = monkeypatched
    try:
        token = _csrf_token(client)
        client.post(
            "/crawl",
            data={
                "page_url": HTTPS_PAGE,
                "limit": "5",
                "comments": "9999",
                "csrf_token": token,
            },
            headers={"Origin": "http://testserver"},
        )
        assert started["comments"] == 100

        started.clear()
        client.post(
            "/crawl",
            data={
                "page_url": HTTPS_PAGE,
                "limit": "5",
                "comments": "not a number",
                "csrf_token": token,
            },
            headers={"Origin": "http://testserver"},
        )
        assert started["comments"] == 0
    finally:
        pages_module.crawl_job = original

