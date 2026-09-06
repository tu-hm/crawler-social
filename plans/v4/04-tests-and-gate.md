# 04 — Tests and the verification gate

## Outcome

`tests/test_htmx_contract.py` exists and holds every assertion that keeps
Steps 01–03 honest. The plans/v2/10 gate still holds with **no new routes
registered**, which is itself asserted. `make test-server` and
`uv run pytest` are green.

## Depends on

- Steps 01–03, or whichever of them were taken. The "Required tests"
  sections in those steps list *what* to assert; this step is where the
  assertions live and how they are written.

## Why a dedicated module

The `INLINE_SCRIPT` / `INLINE_STYLE` greps in
`tests/test_server_security.py` already established the pattern this step
extends: some properties of the UI are only provable by looking at the
markup, and a property nobody greps for is a property that decays. v4 adds
five such properties — no eval-requiring attributes, no unregistered
components, no broken swap targets, no htmx element without a no-JS
fallback, no unpinned vendored byte. One module keeps them together and
makes the failure message say which promise broke.

Put the Step 01 and Step 03 assertions here too, not in
`test_server_security.py`. That module is about the hardening layer;
this one is about the client contract.

## Work, in order

### 1. Fixtures

Reuse `tests/conftest.py`'s `make_db` / `make_config` and seed one
database that hits every branch these tests need: a post with text (for
the copy button), a run with a >120-character error (for §2 of Step 03), a
snapshot, and a watermark row. `tests/test_smoke_routes.py` already builds
almost exactly this — copy its `seeded_app` fixture rather than inventing
a second shape.

Two module-level constants:

```python
TEMPLATES = Path(crawler_social.server.templating.TEMPLATES_DIR)
HTML_ROUTES = ["/", "/posts", "/posts/p1", "/runs", "/runs/1",
               "/snapshots", "/snapshots/1", "/state", "/crawl"]
```

Derive `TEMPLATES` from `templating.TEMPLATES_DIR`, never from a path
relative to the test file — the same reason `templating.py` derives it
from `__file__`.

### 2. The banned-attribute grep, over template *sources*

These must be caught in the templates, not in rendered output, because a
banned attribute on a rarely-rendered branch would slip through a
route walk.

```python
BANNED = {
    "hx-on:":        r"\bhx-on:",
    "js: prefix":    r"\b(hx-vals|hx-headers)\s*=\s*[\"']\s*js:",
    "trigger filter": r"hx-trigger\s*=\s*[\"'][^\"']*\w\[",
    "alpine object":  r"x-data\s*=\s*[\"']\s*\{",
    "alpine negation": r"x-(show|if|bind:[\w-]+)\s*=\s*[\"']\s*!",
    "alpine expression": r"x-(text|show|if)\s*=\s*[\"'][^\"']*[+()]",
    "inline style":   r"\bstyle\s*=",
    "alpine style":   r"\bx-bind:style|:\bstyle=",
}
```

Walk every `*.html` under `TEMPLATES` and assert no pattern matches,
naming the file, the line, and *why* in the failure message — "requires
`unsafe-eval`, which `script-src 'self'` forbids" is the message that
saves the next person twenty minutes.

The `inline style` entry is not new to v4: today's 14 templates already
contain zero `style=` attributes, and with `style-src 'self'` and no
`style-src-attr` a single one would be blocked by the browser. Locking it
in here costs one line.

### 3. Swap targets resolve — over *rendered* pages

The highest-value test in the module, because a wrong id is the failure
mode htmx has no error for: the request succeeds, the swap finds nothing,
and the page just sits there.

For each path in `HTML_ROUTES`, render it and for every
`hx-target`, `hx-select` and `hx-select-oob` value:

- strip a leading `#`, and for `hx-select-oob` split on `,` then on `:` to
  drop the strategy suffix;
- skip the literal `this`;
- assert an element with that id exists **in the same response**.

`hx-select` is the one that must be checked against the response of
`hx-get`'s own URL, not the current page. For every URL in this plan those
are the same route, which is exactly D1's claim — so assert that too:
`hx-get`'s path, with parameters filled, equals the current path for every
element in `#results` and `#crawl-panel`. If that assertion ever fails,
someone introduced a partial endpoint and D1 no longer holds.

### 4. Every `hx-get` / `hx-post` URL is a registered route

Reuse the route-walking machinery in `tests/test_smoke_routes.py` rather
than re-deriving it. Collect the `hx-get` and `hx-post` values from all
rendered pages, strip query strings, and match each against
`app.routes` using Starlette's own matching so `{post_id:path}` resolves
properly.

This ties the client contract to the plans/v2/10 gate: an htmx attribute
pointing at a route that does not exist fails here, and a route that does
exist is already covered by `test_every_registered_route_is_smoke_tested`.

### 5. v4 adds no routes — assert it

`test_smoke_routes.py` ends in a count assertion whose stated purpose is
that "a new route cannot be added without passing through this smoke
walk." v4 deliberately adds none: every interaction re-requests an
existing route. Leave that count untouched and let it prove the claim. If
a step in this plan makes it fail, the step introduced a partial endpoint
and [Step 00](./00-scope-and-decisions.md) D1 was abandoned somewhere —
that is a decision to make on purpose, in writing, not a number to bump.

### 6. Progressive enhancement

For each rendered page, every element carrying `hx-get` must also carry a
usable no-JS fallback:

- an `<a>` needs a non-empty `href`;
- a `<form>` needs a non-empty `action` and a `method`;
- nothing else may carry `hx-get` at all.

And the two URLs must agree: the `hx-get` value must equal the `href`, or
for a form, the `action` — because `hx-get` on a form serializes the same
fields the native submit would.

Then assert the `<noscript>` apply button still exists on
`/posts`' page-size form. It is the only control that has no other no-JS
path, and it is one line that is easy to lose in an edit.

### 7. Configuration and vendored bytes

- Parse the `htmx-config` meta `content` as JSON from a rendered page and
  assert `includeIndicatorStyles is False`, `historyCacheSize == 0`,
  `allowEval is False`, `allowScriptTags is False`,
  `selfRequestsOnly is True`. Assert the parsed values, so reformatting
  the attribute does not fail and changing a value does.
- Hash `static/vendor/*.js` and compare against `SHASUMS256`.
- Assert `alpine-csp.min.js` contains `CSP-friendly build` — the
  wrong-package check from Step 01 §5, as a test. Not a `Function(`
  assertion: that string appears identically in the stock build, so such a
  test passes while the page is broken.
- Assert `app.css` contains `.htmx-indicator` and, if Step 03 was taken,
  `[x-cloak]`. Both exist only because a library's default was disabled;
  neither has any other guard.
- Extend the existing "no external URL in rendered HTML" assertion to
  cover the two new `<script src>` values.

### 8. Alpine registrations match

Collect every `x-data` value from the templates, collect every
`Alpine.data("…")` name from `static/app.js`, and assert the first set is a
subset of the second. A typo in either place is otherwise completely
silent.

## Required tests

The list above *is* the required tests. What matters is the acceptance
criterion for the whole plan:

- **Every pre-v4 test passes unmodified.** All ~105 assertions that read
  `resp.text` across `test_posts_view.py`, `test_runs_view.py`,
  `test_snapshot_view.py`, `test_ui_shell.py`, `test_viewer_fixes.py`,
  `test_crawl_trigger.py`, `test_server_security.py`,
  `test_server_app.py`, `test_text_only_storage.py` and
  `test_smoke_routes.py`. Not one of them should need an edit, because the
  server still renders the same complete pages. **If one does, stop and
  find out why before editing it** — that test is reporting a real
  behaviour change, and the whole reason to prefer htmx over a client-side
  framework here was to keep them.
- `test_no_inline_script_or_style_on_any_page` passes with no exemption
  added.
- `test_every_registered_route_is_smoke_tested` passes with its count
  unchanged.

## Verification

```console
$ make vendor-verify
$ make test-server
$ uv run pytest -q
$ uv run pytest tests/test_htmx_contract.py -q
```

Then the manual pass that no test covers, because it is about what the
browser refuses rather than what the server sends — run `uv run crawler
serve`, open the console, and exercise `/posts` filtering, `/posts/{id}`
copy, `/runs` disclosure, `/crawl` start-to-finish. **A clean console is
the assertion.** Specifically:

| Console message | What it means |
| --- | --- |
| `Refused to evaluate a string as JavaScript` | **stock** Alpine was vendored (Step 01 §5), or an `hx-on:` / `js:` attribute got in |
| `Alpine Error: … CSP-friendly build` | correct build, but an attribute holds an expression — §2's grep missed a template |
| `Refused to apply inline style` | Step 01 §7 was skipped, or a `style=` attribute got in |
| `htmx:targetError` | a swap target id is wrong — §3 missed a page |
| nothing, but the page does not update | `hx-select` found no match in the response |

Note what is *not* in that table: an Alpine attribute that the CSP build
cannot interpret produces a warning, not an error, and no CSP violation at
all. That is the whole reason §2 exists.

Finally, disable JavaScript and walk all nine `HTML_ROUTES`. Every page
must render and every control must work, which is the pre-v4 behaviour and
the promise the whole plan rests on.
