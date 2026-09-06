# v4 — htmx + Alpine over the existing Jinja viewer

v2 built the viewer as server-rendered Jinja2. v4 keeps every line of that
rendering and adds two vendored JavaScript files so the three interactions
that currently cost a full page load stop costing one:

1. **`/posts` filtering and pagination** swap only the results region.
2. **`/crawl` live output** polls a region instead of running 60 lines of
   hand-rolled `fetch` and `setTimeout` in `app.js`.
3. **Small client state** (copy-to-clipboard) moves from a
   `querySelectorAll` + `addEventListener` block into a declarative Alpine
   component.

Measured afterwards: the imperative wiring htmx replaced was 68 lines of
`app.js` and became 6. The file does not shrink overall — 109 lines to 116
— because three Alpine components move in, two of which are new
behaviour. The win is which code is gone, not how much.

## Why this does not need a new ADR

[ADR-0066](../../docs/DECISIONS.md#adr-0066) rejected "any JS framework or
bundler" for three named reasons: *a build step, a `node_modules` tree, and
a second dependency audit*. This plan adds none of the first two — the two
libraries are vendored as plain files, fetched once by a `make` target and
checksummed. What survives intact:

- Jinja renders every page, complete, on the server.
- There is **one** rendering path. htmx re-requests the same route and
  swaps a fragment out of the same full response (`hx-select`), so no
  partial template ever duplicates a page template. This is the specific
  thing ADR-0066 called out as "two rendering paths to keep in sync," and
  avoiding it is the central design choice of this plan.
- No Node, no npm, no bundler, no `node_modules`, no CDN, no build output
  in the package.
- Every page still works with JavaScript disabled.

What genuinely changes is the file count: ADR-0066 says "exactly two static
files (`app.css`, `app.js`)" and this makes it four. [Step 05](./05-adr-addendum.md)
records that in eight lines as a *Revisited* note on ADR-0066 — an
addendum, not a superseding entry, because the decision itself still holds.

## The two constraints that dictate the design

Both come from `hardening.py`, and both kill the naive version of this
plan:

**`script-src 'self'` has no `unsafe-eval`.** Stock Alpine compiles
attribute expressions with the `Function` constructor, so
`x-on:click="open = !open"` throws on the first evaluation and the page
silently does nothing. The **`@alpinejs/csp` build is mandatory**, and it
changes the authoring grammar: components are registered in `app.js` via
`Alpine.data()` and attributes may only *name* a property or method.
htmx core needs no eval, but `hx-on:`, the `js:` prefix on `hx-vals` /
`hx-headers`, and expression trigger filters all do — so those are banned
and `allowEval` is turned off to enforce it.

**`style-src 'self'` with no `style-src-attr`.** CSP3 applies `style-src`
to inline `style=` attributes too. Today's 14 templates contain zero of
them. htmx, left at defaults, calls
`document.head.insertAdjacentHTML("beforeend", "<style>…")` on init to
define `.htmx-indicator` — which breaks the CSP *and* trips
`test_no_inline_script_or_style_on_any_page`. `includeIndicatorStyles`
must be `false` and those three rules hand-written into `app.css`.

Everything asserted about both libraries in this directory was checked
against the actual files rather than their documentation; the results are
in [Step 01](./01-vendor-and-config.md) §0.

## Steps

| Step | File | What it delivers |
| --- | --- | --- |
| 00 | [00-scope-and-decisions.md](./00-scope-and-decisions.md) | What each library is allowed to do, the decisions that shape the rest, non-goals |
| 01 | [01-vendor-and-config.md](./01-vendor-and-config.md) | `static/vendor/`, the `make` fetch target, checksums, the `htmx-config` meta tag |
| 02 | [02-htmx-interactions.md](./02-htmx-interactions.md) | `/posts` region swaps, `/crawl` self-terminating poll, `app.js` shrinks |
| 03 | [03-alpine-components.md](./03-alpine-components.md) | `Alpine.data()` components for clipboard and log anchoring |
| 04 | [04-tests-and-gate.md](./04-tests-and-gate.md) | New grep tests, vendor integrity, no-JS proof, the plans/v2/10 gate |
| 05 | [05-adr-addendum.md](./05-adr-addendum.md) | The eight-line *Revisited* note on ADR-0066 |

## Invariants v4 must not break

- **The server still never writes.** ADR-0064 untouched. htmx issues
  ordinary GETs against the same read-only handlers; the only POSTs remain
  the two `/crawl` forms behind `lock.py`.
- **No inline `<script>`, `<style>`, or `style=` attribute.** See above.
  `test_no_inline_script_or_style_on_any_page` stays green with no
  exemptions added to it.
- **No network request leaves the machine.** The libraries are files on
  disk under `/static`, not CDN URLs. The existing assertion that rendered
  HTML references no external URL keeps holding.
- **No rendered content in browser storage.** htmx caches up to ten
  rendered page snapshots under `sessionStorage["htmx-history-cache"]` by
  default. This database holds third-party personal data whose boundary is
  a file path (ADR-0022); rendered post text must not leak into browser
  storage. `historyCacheSize: 0` both disables the cache and removes the
  key.
- **Every page works with JavaScript off.** htmx and Alpine attributes are
  purely additive: every `hx-get` sits on an element that already has a
  working `href` or `action`. The `<noscript>` apply button on the
  page-size form stays.
- **One rendering path.** No partial template may render data that a page
  template also renders. Fragments come out of full responses via
  `hx-select`.
- **`make test-server` stays the gate.** Every `hx-get` / `hx-post` target
  must be a registered, smoke-tested route — enforced by a new test, not
  by review.
- **The URL vocabulary is unchanged.** `q`, `page_url`, `since`, `until`,
  `order`, `limit`, `offset`. `hx-push-url` writes exactly the URL a
  no-JS form submit would have produced, so a copied link still works.

## What v4 is not

- Not a redesign. The 534 lines of `app.css` are good — tokens, dark mode,
  focus rings, reduced-motion, skip link. Nothing here restyles anything.
- Not a feature. Every page shows the same data afterwards, so the whole
  migration reviews as a refactor.
- Not SSE or WebSockets. The `/crawl` poll becomes declarative but stays a
  poll; [Step 02](./02-htmx-interactions.md) notes what SSE would cost if
  the two-second cadence ever stops being enough.
