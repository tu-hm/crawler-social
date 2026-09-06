"""The client-side contract for the vendored libraries (plans/v4/04).

Five properties of the markup that nothing else can prove, because each one
fails silently in a browser rather than in a request:

1. No attribute needs `unsafe-eval`, which `script-src 'self'` forbids.
2. Every `x-data` names a component that is actually registered.
3. Every htmx swap target id exists in the response it is selected from.
4. Every element carrying `hx-get` also works with JavaScript disabled.
5. The vendored bytes are the pinned ones, and are the CSP build of Alpine.

This module exists for the same reason `INLINE_SCRIPT` does in
`test_server_security.py`: a property nobody greps for is a property that
decays. The failure messages say which promise broke, not just which
pattern matched.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crawler_social.server.templating import STATIC_DIR, TEMPLATES_DIR

from tests.conftest import make_config, make_db

PAGE_URL = "https://a.example"
VENDOR_DIR = STATIC_DIR / "vendor"

# A long error so the /runs disclosure component renders; a post with text so
# the copy button does.
LONG_ERROR = "boom: " + "captcha wall detail " * 12

#: `?limit=1&offset=1` is not decoration: with a short result set both
#: pagination links render as disabled <span>s carrying no attributes, so a
#: page-1 render never exercises partials/pagination.html at all.
HTML_ROUTES = [
    "/",
    "/posts",
    "/posts?limit=1&offset=1",
    "/posts/p1",
    "/runs",
    "/runs/1",
    "/snapshots",
    "/snapshots?limit=1&offset=1",
    "/snapshots/1",
    "/state",
    "/crawl",
]


RUNNING_STATUS = {
    "running": True,
    "page_url": PAGE_URL,
    "elapsed_seconds": 12.0,
    "lines": ["scrolling", "captured 3 posts"],
    "lines_dropped": 4,
    "exit_code": None,
    "stop_requested": False,
    "lock_busy": False,
}


@pytest.fixture()
def client(db_file: Path) -> TestClient:
    make_db(
        db_file,
        posts=[
            ("p1", PAGE_URL, "a post with text to copy", "Ann",
             "2026-01-02T10:00:00+00:00", "2026-01-02T09:00:00+00:00"),
            ("p2", PAGE_URL, "another post", None, None,
             "2026-01-01T09:00:00+00:00"),
            ("p3", PAGE_URL, "a third post", None, None,
             "2026-01-01T08:00:00+00:00"),
        ],
        runs=[("2026-01-01T00:00:00+00:00", "failed",
               "2026-01-01T00:05:00+00:00", LONG_ERROR)],
        snapshots=[
            (0, PAGE_URL, "2026-01-01T00:01:00+00:00", b"<html></html>"),
            (0, PAGE_URL, "2026-01-01T00:02:00+00:00", b"<html>b</html>"),
        ],
        states=[(PAGE_URL, "p1", "2026-01-02T10:00:00+00:00",
                 "2026-01-02T10:00:00+00:00")],
    )
    from crawler_social.server.app import create_app

    return TestClient(create_app(make_config(db_file)))


@pytest.fixture()
def running_client(client: TestClient, monkeypatch) -> TestClient:
    """A client whose /crawl renders the *running* panel.

    Without this the poll attributes are never rendered -- they sit behind
    `{% if status.running %}` -- and every assertion about them passes
    vacuously.
    """
    from crawler_social.server import pages as pages_module

    class StubJob:
        def status(self):
            return dict(RUNNING_STATUS)

    monkeypatch.setattr(pages_module, "crawl_job", StubJob())
    return client


def _templates() -> list[Path]:
    return sorted(Path(TEMPLATES_DIR).rglob("*.html"))


def _rendered(client: TestClient) -> dict[str, str]:
    pages = {}
    for path in HTML_ROUTES:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        pages[path] = resp.text
    return pages


# --- 1. Nothing may require unsafe-eval, and nothing may be inline style ---

#: Each pattern maps to the reason it is banned, which becomes the failure
#: message. Anything in the right-hand column of the table in
#: plans/v4/03-alpine-components.md belongs here.
BANNED = {
    r"\bhx-on:": "hx-on: is evaluated with the Function constructor, which "
                 "script-src 'self' forbids and allowEval:false disables",
    r"\b(?:hx-vals|hx-headers)\s*=\s*[\"']\s*js:":
        "the js: prefix is evaluated at runtime; pass plain values instead",
    r"hx-trigger\s*=\s*[\"'][^\"']*\w\[":
        "an expression trigger filter such as click[ctrlKey] needs eval",
    r"x-data\s*=\s*[\"']\s*\{":
        "the CSP build cannot parse an object literal; register the "
        "component with Alpine.data() and name it here",
    r"x-(?:show|if|bind:[\w-]+)\s*=\s*[\"']\s*!":
        "the CSP build cannot negate; expose a second boolean property",
    r"x-(?:text|show|if)\s*=\s*[\"'][^\"']*[+()]":
        "the CSP build cannot evaluate an expression; move it into a "
        "method or a getter",
    r"\sstyle\s*=\s*[\"']":
        "style-src 'self' with no style-src-attr blocks inline style "
        "attributes; put the rule in app.css",
    r"x-bind:style\b":
        "same as a literal style attribute once Alpine applies it",
}


@pytest.mark.parametrize("pattern,reason", list(BANNED.items()))
def test_no_banned_attribute_in_any_template(pattern: str, reason: str) -> None:
    compiled = re.compile(pattern, re.IGNORECASE)
    hits = []
    for template in _templates():
        for number, line in enumerate(template.read_text().splitlines(), 1):
            if compiled.search(line):
                hits.append(f"{template.name}:{number}: {line.strip()}")
    assert not hits, reason + "\n" + "\n".join(hits)


# --- 2. Alpine components are registered ------------------------------------


def test_every_x_data_names_a_registered_component() -> None:
    app_js = (STATIC_DIR / "app.js").read_text()
    registered = set(re.findall(r"Alpine\.data\(\s*[\"']([\w$]+)[\"']", app_js))
    used = set()
    for template in _templates():
        used.update(re.findall(r"x-data\s*=\s*[\"']([\w$.]+)[\"']",
                               template.read_text()))
    missing = sorted(used - registered)
    assert not missing, (
        "x-data names no registered component -- the attribute will warn to "
        f"the console and do nothing: {missing}. Registered: {sorted(registered)}"
    )
    assert used, "no x-data found at all; did the templates change?"


def test_x_cloak_has_a_css_rule() -> None:
    # Without it, every element that exists only to show an Alpine state is
    # visible as a dead control to a reader with JavaScript off.
    assert "[x-cloak]" in (STATIC_DIR / "app.css").read_text(), (
        "app.css needs an [x-cloak] { display: none !important } rule"
    )


def test_htmx_indicator_rules_are_hand_written() -> None:
    # includeIndicatorStyles:false means htmx no longer injects these, so
    # nothing else guards their existence.
    assert ".htmx-indicator" in (STATIC_DIR / "app.css").read_text()


# --- 3. Swap targets resolve -------------------------------------------------

ID_ATTRS = ("hx-target", "hx-select")


def _ids_in(html: str) -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', html))


def test_every_swap_target_exists_in_its_own_response(client: TestClient) -> None:
    for path, html in _rendered(client).items():
        present = _ids_in(html)
        for attr in ID_ATTRS:
            for value in re.findall(rf'{attr}="([^"]+)"', html):
                if value == "this":
                    continue
                assert value.startswith("#"), f"{path}: {attr}={value!r}"
                target = value[1:]
                assert target in present, (
                    f"{path}: {attr}=\"{value}\" but no element has that id. "
                    "htmx reports nothing for this -- the request succeeds "
                    "and the page silently does not update."
                )


def test_out_of_band_targets_exist_and_name_a_strategy(
    running_client: TestClient,
) -> None:
    seen = 0
    for path, html in _rendered(running_client).items():
        present = _ids_in(html)
        for value in re.findall(r'hx-select-oob="([^"]+)"', html):
            seen += 1
            for entry in value.split(","):
                selector, _, strategy = entry.strip().partition(":")
                target = selector.lstrip("#")
                assert target in present, (
                    f"{path}: hx-select-oob names #{target}, which this "
                    "response does not contain"
                )
                if target == "crawl-output":
                    assert strategy == "innerHTML", (
                        "the log must be swapped with :innerHTML or the <pre> "
                        "is recreated and its scroll position resets"
                    )
                if target == "crawl-announce":
                    assert strategy == "innerHTML", (
                        "the live region element must survive the swap or the "
                        "announcement never fires"
                    )
    assert seen, (
        "no hx-select-oob rendered anywhere -- this test would pass "
        "vacuously. The /crawl poll attributes sit behind "
        "{% if status.running %}; is the running_client fixture in use?"
    )


def test_swaps_re_request_their_own_route(client: TestClient) -> None:
    """The D1 claim: no element fetches a partial-only endpoint."""
    for path, html in _rendered(client).items():
        for url in re.findall(r'hx-get="([^"]+)"', html):
            route = url.split("?")[0]
            assert route == path.split("?")[0] or route in HTML_ROUTES, (
                f"{path}: hx-get points at {route!r}, which is neither this "
                "route nor another page route. If that is deliberate, a "
                "partial endpoint was introduced and plans/v4/00 D1 no "
                "longer holds."
            )


def test_crawl_output_and_announce_sit_outside_the_polled_panel(
    running_client: TestClient,
) -> None:
    html = running_client.get("/crawl").text
    panel_start = html.index('id="crawl-panel"')
    panel_end = html.index("</div>", panel_start)
    panel = html[panel_start:panel_end]
    for element in ("crawl-output", "crawl-announce"):
        assert f'id="{element}"' in html, (
            f"#{element} is not rendered while a crawl runs, so the "
            "out-of-band swap has nothing to update"
        )
        assert f'id="{element}"' not in panel, (
            f"#{element} is inside #crawl-panel; the main swap and the "
            "out-of-band swap would fight over it"
        )


# --- 4. Progressive enhancement ---------------------------------------------

ELEMENT_WITH_HX_GET = re.compile(r"<(\w+)\b([^>]*\bhx-get=\"[^\"]+\"[^>]*)>", re.S)


def test_every_hx_get_element_works_without_javascript(client: TestClient) -> None:
    seen = 0
    for path, html in _rendered(client).items():
        for tag, attrs in ELEMENT_WITH_HX_GET.findall(html):
            seen += 1
            hx_get = re.search(r'hx-get="([^"]+)"', attrs).group(1)
            if tag == "a":
                fallback = re.search(r'href="([^"]+)"', attrs)
                assert fallback, f"{path}: <a hx-get> with no href"
            elif tag == "form":
                fallback = re.search(r'action="([^"]+)"', attrs)
                assert fallback, f"{path}: <form hx-get> with no action"
                assert re.search(r'method="[^"]+"', attrs), (
                    f"{path}: <form hx-get> with no method"
                )
            else:
                pytest.fail(
                    f"{path}: <{tag}> carries hx-get but has no no-JS "
                    "equivalent; only <a> and <form> may"
                )
            assert fallback.group(1) == hx_get, (
                f"{path}: hx-get={hx_get!r} disagrees with the no-JS URL "
                f"{fallback.group(1)!r}. The two paths have diverged."
            )
    assert seen, "no hx-get found at all; did the templates change?"


def test_page_size_form_keeps_its_noscript_button(client: TestClient) -> None:
    # The only control on /posts with no other no-JS path.
    assert "<noscript>" in client.get("/posts").text


def test_filter_forms_still_submit_natively(client: TestClient) -> None:
    for path, action in (("/posts", "/posts"), ("/snapshots", "/snapshots")):
        html = client.get(path).text
        assert f'method="get" action="{action}"' in html


# --- 5. Configuration and vendored bytes ------------------------------------

REQUIRED_CONFIG = {
    "includeIndicatorStyles": False,
    "historyCacheSize": 0,
    "allowEval": False,
    "allowScriptTags": False,
    "selfRequestsOnly": True,
}


def test_htmx_config_meta_is_present_and_correct(client: TestClient) -> None:
    html = client.get("/").text
    match = re.search(r"<meta name=\"htmx-config\" content='([^']+)'>", html)
    assert match, "no htmx-config meta tag; htmx would run at its defaults"
    config = json.loads(match.group(1))
    for key, expected in REQUIRED_CONFIG.items():
        assert config[key] is expected or config[key] == expected, (
            f"htmx-config {key}={config[key]!r}, expected {expected!r}"
        )


def test_config_meta_on_every_page(client: TestClient) -> None:
    for path, html in _rendered(client).items():
        assert 'name="htmx-config"' in html, path


def test_vendored_files_match_their_checksums() -> None:
    lines = (VENDOR_DIR / "SHASUMS256").read_text().split("\n")
    checked = 0
    for line in lines:
        if not line.strip():
            continue
        digest, name = line.split()
        name = name.lstrip("*")
        actual = hashlib.sha256((VENDOR_DIR / name).read_bytes()).hexdigest()
        assert actual == digest, (
            f"{name} does not match SHASUMS256. Re-run `make vendor` if the "
            "version was changed on purpose; otherwise a vendored library "
            "was edited in place."
        )
        checked += 1
    assert checked == 2, f"expected two vendored files, checksummed {checked}"


def test_vendored_alpine_is_the_csp_build() -> None:
    # NOT a `Function(` assertion: that string appears identically in the
    # stock build, so such a test passes while every attribute is broken.
    source = (VENDOR_DIR / "alpine-csp.min.js").read_text()
    assert "CSP-friendly build" in source, (
        "alpine-csp.min.js is stock Alpine, not @alpinejs/csp. Stock Alpine "
        "reaches its evaluator through the async-function constructor, which "
        "script-src 'self' refuses, so no Alpine attribute will work."
    )


def test_vendored_files_are_served(client: TestClient) -> None:
    for name in ("htmx.min.js", "alpine-csp.min.js"):
        resp = client.get(f"/static/vendor/{name}")
        assert resp.status_code == 200, name
        assert "javascript" in resp.headers["content-type"], name


def test_no_external_url_in_any_rendered_page(client: TestClient) -> None:
    # The viewer must work with no network. The two vendored <script src>
    # values are root-relative, so this keeps holding.
    for path, html in _rendered(client).items():
        for attr in re.findall(r'(?:src|href)="([^"]+)"', html):
            assert not attr.startswith(("http://", "https://")), f"{path}: {attr}"


# --- 6. The /crawl poll terminates by swapping itself away ------------------


def test_crawl_panel_polls_while_running(running_client: TestClient) -> None:
    html = running_client.get("/crawl").text
    assert 'id="crawl-panel"' in html
    assert 'hx-trigger="every 2s"' in html
    assert 'hx-select="#crawl-panel"' in html


def test_crawl_panel_stops_polling_when_idle(client: TestClient) -> None:
    """The termination guarantee (plans/v4/00 D4).

    The idle panel must carry no trigger: htmx stops polling because the
    element that was polling no longer exists after the swap. Without this
    the poll would run forever against a finished crawl.
    """
    html = client.get("/crawl").text
    assert 'id="crawl-panel"' in html, "the swap target must always exist"
    assert "hx-trigger" not in html, (
        "the idle /crawl panel carries a trigger, so polling would never "
        "stop once a crawl finishes"
    )
    assert "every 2s" not in html


def test_announce_region_carries_no_ticking_value(running_client: TestClient) -> None:
    """A live region whose text changes every 2s is read out every 2s."""
    html = running_client.get("/crawl").text
    match = re.search(
        r'<p id="crawl-announce"[^>]*>(.*?)</p>', html, re.S
    )
    assert match, "no #crawl-announce region"
    text = match.group(1).strip()
    assert text == "Crawl running.", repr(text)
    # RUNNING_STATUS sets elapsed_seconds to 12.0; it must not appear here.
    assert "12" not in text, (
        "the announce region contains the elapsed time, so a screen reader "
        "would re-announce it on every poll"
    )
    assert 'aria-live="polite"' in match.group(0)


def test_running_panel_has_no_second_live_region(running_client: TestClient) -> None:
    html = running_client.get("/crawl").text
    assert html.count('aria-live="polite"') == 1, (
        "more than one live region on /crawl: the visible elapsed counter "
        "must not be announced, only #crawl-announce"
    )


# --- 7. There is exactly one rendering path (D1) -----------------------------


def test_hx_request_header_changes_nothing(client: TestClient) -> None:
    """No handler may branch on HX-Request.

    The whole reason the pre-v4 HTML assertions still pass is that an htmx
    request gets the same complete page as a navigation.
    """
    for path in ("/posts?q=post", "/runs", "/snapshots", "/crawl"):
        plain = client.get(path)
        boosted = client.get(path, headers={"HX-Request": "true"})
        assert boosted.status_code == plain.status_code, path
        assert boosted.text == plain.text, (
            f"{path} renders differently for an htmx request -- a partial "
            "path was introduced and plans/v4/00 D1 no longer holds"
        )


def test_results_region_contains_the_rows(client: TestClient) -> None:
    html = client.get("/posts").text
    start = html.index('id="results"')
    end = html.index('class="page-size-form"')
    region = html[start:end]
    assert "a post with text to copy" in region, (
        "the rows are outside #results, so a filter swap would not replace them"
    )
    assert "result-count" in region


def test_csv_link_carries_the_current_filter(client: TestClient) -> None:
    html = client.get("/posts?q=post&order=oldest").text
    match = re.search(r'<a class="button" id="csv-link" href="([^"]+)"', html)
    assert match, "no #csv-link; hx-select-oob has nothing to keep in sync"
    href = match.group(1)
    assert "q=post" in href and "order=oldest" in href, href


def test_filter_form_is_outside_the_swap_region(client: TestClient) -> None:
    """Swapping a form mid-keystroke would destroy the caret."""
    html = client.get("/posts").text
    assert html.index('class="filter-bar"') < html.index('id="results"')
