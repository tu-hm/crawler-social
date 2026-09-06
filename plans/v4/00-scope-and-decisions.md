# 00 — Scope and decisions

## The problem

Three interactions in the viewer are worse than they need to be, and one
file is where the cost shows up.

**Filtering `/posts` reloads the page.** `app.js` submits the filter form
on `change`, and debounces text input by 500 ms before submitting. Every
keystroke pause throws away the whole document — header, footer, nav,
scroll position, and the focus ring on the input being typed into. On a
long results table the scroll jump is the most annoying part.

**`/crawl` runs a bespoke polling client.** Sixty of `app.js`'s 109 lines
are one `fetch` loop with its own failure counter, backoff, log-scroll
anchoring, and a `setTimeout` + `location.reload()` to get the form back
when the crawl ends. Every one of those is a thing that can be subtly
wrong, and two of them already were (`elapsed_seconds` null rendering
`NaNs`; the page URL thrown away by the status rewrite — see the comments
in `crawl.html`).

**Client state is hand-wired.** The clipboard button is a
`querySelectorAll` loop that reaches across the document by attribute
selector, restores its own label on a timer, and has no way to express
"disabled while copying."

## Decisions

### D1 — htmx re-requests the same route and selects a fragment

The single most important decision. Rather than adding partial-only
endpoints, an htmx request goes to the **same URL the form would have
submitted to**, gets the complete rendered page back, and swaps only the
matching region out of it with `hx-select`.

```
hx-get="/posts" hx-select="#results" hx-target="#results" hx-push-url="true"
```

Consequences, and why this is worth the wasted bytes:

- **No new templates and no new routes** for `/posts`. `posts.html` gains
  an `id` on a wrapper `<div>` and four attributes on the form. That is the
  entire server-side diff.
- **No second rendering path.** ADR-0066 rejected "fetch-and-patch
  rendering — two rendering paths to keep in sync for no capability the
  tool actually needs." A fragment cut from the page's own response is not
  a second path; a partial template rendering the same rows would be.
- **The no-JS path is the same code.** The form still has
  `method="get" action="/posts"`. With htmx absent it submits. There is no
  branch to keep in sync because there is no branch.
- **Every existing HTML assertion keeps passing.** ~105 assertions across
  11 test modules read `resp.text` from a full page render. None of them
  move.

The cost is that the server renders a header and footer nobody swaps in,
which means one extra `queries.summary()` per interaction against a local
SQLite file over WAL. That is not a cost worth a second template
hierarchy. If it ever becomes one, the escape hatch is a single
`if request.headers.get("HX-Request")` branch inside `render()` that
picks a `base_fragment.html` with an empty chrome — one place, not one
per page.

### D2 — Alpine is the CSP build, and it earns a small, named scope

`script-src 'self'` forbids `unsafe-eval`, so `@alpinejs/csp` is the only
build that runs. In it, attribute values name registered properties and
methods — `x-on:click="copy"`, or a dotted path like
`x-on:click="panel.close"`, which the build resolves with
`value.split(".").reduce(...)` — and all logic lives in `Alpine.data()`
registrations in `app.js`.

The two failure modes are different and both are quiet, which is worth
knowing before debugging one:

- **Stock Alpine under this CSP** builds its evaluator from
  `Object.getPrototypeOf(async function(){}).constructor`, so the browser
  refuses it and the console shows a CSP violation.
- **The CSP build given an expression it cannot interpret** never
  evaluates anything, so there is no violation at all. It emits
  `console.warn("Alpine Error: Alpine is unable to interpret the following
  expression using the CSP-friendly build: …")` and the attribute silently
  does nothing.

The second is why Step 04 greps the templates: a warning in a console
nobody has open is not a test.

That constraint removes most of Alpine's appeal — inline expressions are
the whole selling point — so its scope here is small and listed
exhaustively in [Step 03](./03-alpine-components.md).

It is also smaller than it first looks. The obvious second candidate, the
crawl log's "only scroll if the reader is already at the bottom" logic,
**cannot** be an Alpine component: htmx replaces that element on every
poll, and Alpine's per-element state dies with the element it was attached
to. That one has to be an `htmx:beforeSwap` / `htmx:afterSwap` listener in
`app.js`, so it belongs to [Step 02](./02-htmx-interactions.md).

What is left for Alpine is the clipboard button — a genuine
like-for-like replacement of existing imperative code — plus two optional
components that are new behaviour and marked as such. **Alpine is
therefore optional.** Steps 01, 02 and 04 stand alone and carry the entire
performance win. Alpine pays for itself only if client-side state keeps
accumulating past those three components; the case for adopting it now is
that `app.js` is the file that degrades worst as that happens, and
`Alpine.data()` gives it a shape.

### D3 — `hx-boost` is not used

Boosting every link and form globally would give SPA-style navigation for
free, and it is the wrong trade here. The pages are small, served from
loopback, and already `Cache-Control: no-store`; the win rounds to nothing.
Against it: boosting changes how *every* page loads at once, including the
404, the 500 with its request id, and the 401 from inside
`TokenAuthMiddleware` — pages whose whole job is to render correctly when
something is already broken. Three targeted regions are reviewable; a
global mode change is not.

### D4 — The `/crawl` poll terminates by swapping itself away

`crawl.html` already renders two mutually exclusive panels: a "Crawl
running" panel when `status.running`, and the start form when it is not.
Wrap both in one `#crawl-panel` element and poll the route itself:

```
hx-get="/crawl" hx-trigger="every 2s" hx-select="#crawl-panel" hx-swap="outerHTML"
```

When the crawl finishes, the response's `#crawl-panel` *is* the idle start
form, which carries no `hx-trigger` — so htmx stops polling because the
element that was polling no longer exists. No exit-code check on the
client, no `location.reload()`, no failure counter, no `HX-Refresh`
header, and no `/api/crawl/status` consumer. `jobs.py` and
`api.py:crawl_status` are untouched (the JSON endpoint stays for anyone
scripting against it).

Server-side diff: one wrapper element in `crawl.html`. No new route.

### D5 — Two vendored files, checksummed, fetched by `make`

`static/vendor/htmx.min.js` and `static/vendor/alpine-csp.min.js`, both
committed, both pinned to an exact version, both listed in a
`SHASUMS256` file that a test verifies. `make vendor` re-fetches and
re-verifies. No npm, no lockfile, no `node_modules`, and — because they
are ordinary files under `/static` — no CDN, no SRI, and no network
request from the browser. The dependency audit ADR-0066 worried about is
two files reviewable by `wc -l` and a diff.

### D6 — htmx is configured by `<meta>`, and four defaults are wrong for us

Configuration goes in a `<meta name="htmx-config">` tag in `base.html`.
A meta tag is not inline script, so it passes both the CSP and the
inline-grep test — which is the only reason this is possible without a
third static file. The four settings that are not optional:

| Setting | Default | Why we change it |
| --- | --- | --- |
| `includeIndicatorStyles` | `true` | htmx otherwise calls `head.insertAdjacentHTML("beforeend", "<style>…")` on init to define `.htmx-indicator`, breaking `style-src 'self'` and `test_no_inline_script_or_style_on_any_page`. The three rules go into `app.css` by hand. |
| `historyCacheSize` | `10` | htmx otherwise keeps ten rendered pages in `sessionStorage["htmx-history-cache"]`. This database holds third-party personal data whose boundary is a file path (ADR-0022); rendered post text must not become browser storage. `0` also removes the key outright. |
| `allowEval` | `true` | Hard-disables the function-construction paths (`hx-on:`, `js:` prefixes, expression trigger filters) so a future attribute cannot quietly require `unsafe-eval`. |
| `allowScriptTags` | `true` | htmx otherwise executes `<script>` elements found in swapped content. Our responses are Jinja-autoescaped and contain none, so this changes nothing today — and it means a future escaping bug cannot become script execution. |
| `selfRequestsOnly` | `true` | Already correct in htmx 2.0.6. Set explicitly so the intent lives in the file rather than in a version's release notes. |

htmx does offer `inlineStyleNonce` and `inlineScriptNonce` as the other way
out of the first row. They are rejected here: `APP_CSP` in `hardening.py`
is one frozen string, and a nonce has to be minted per response and
threaded into both the header and the template. Three hand-written CSS
rules cost less and keep `hardening.py` untouched.

### D7 — Progressive enhancement is tested, not asserted

"It still works without JavaScript" is a claim that rots. Every element
carrying an `hx-get` must also carry the `href` or `action` that does the
same thing, and [Step 04](./04-tests-and-gate.md) adds a grep test over
the template directory that fails if one does not — the same discipline
`INLINE_SCRIPT` already applies to inline script.

## Non-goals

- SSE or WebSocket streaming for crawl output. The poll becomes
  declarative and stays a poll. SSE would mean a long-lived streaming
  route over a subprocess reader in `jobs.py`, an htmx extension file, and
  a third connection state to reason about — for a two-second latency
  improvement on a single-user tool.
- Any restyling. `app.css` gains four `.htmx-indicator` rules and nothing
  else.
- Client-side sorting, virtual scrolling, or an interactive chart. The
  chart stays server-generated inline SVG (ADR-0066's `_build_chart`).
- Touching `api.py`, `queries.py`, `jobs.py`, `format.py`, or
  `hardening.py`. If a step needs one of them changed, that is a signal
  the step is wrong — with the one deliberate exception in
  [Step 04](./04-tests-and-gate.md), which only adds tests.
- Removing `/api/crawl/status`. It stops having a browser consumer but
  remains a documented endpoint.
