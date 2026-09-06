# 02 — The three htmx interactions

## Outcome

Filtering and paginating `/posts`, `/runs` and `/snapshots` swaps only the
results region and pushes the same URL a form submit would have produced.
`/crawl` polls itself and stops on its own when the crawl ends. The 68
lines of `app.js` that did those two jobs by hand become 6. No new route,
no new template, and no handler in `pages.py` changes.

## Depends on

- [Step 01](./01-vendor-and-config.md) complete and verified.

## The one pattern

Every interaction here is the same three lines, and understanding them
once is understanding the whole step:

```
hx-get="<the URL the element already points at>"
hx-select="#results"   →  cut this element out of the full response
hx-target="#results"   →  replace this element in the page
hx-swap="outerHTML"
hx-push-url="true"
```

The response is the **complete page**, rendered by the same Jinja
templates and the same `pages.py` handler as a normal navigation. htmx
throws away the chrome and keeps the region. That is why there is no
partial template and no `HX-Request` branch anywhere in this step, and why
the ~105 existing HTML assertions do not move.

## Work, in order

### 1. `#results` — a stable id on three pages

Give each list page's results region the same id, so the shared
pagination partial can target it without knowing which page it is on.

**`posts.html`** — wrap the chips block and the whole `{% if rows %}` /
`{% elif no_matches %}` / `{% else %}` chain (including the
`{% include "partials/pagination.html" %}`) in one element:

```html
<div id="results">
  {% if chips %} … {% endif %}
  {% if rows %} … {% elif no_matches %} … {% else %} … {% endif %}
</div>
```

The chips belong inside: removing a filter has to update them. The
`.filter-bar` form stays **outside** — swapping a form while someone is
typing into it destroys the caret and the selection.

**`snapshots.html`** — same wrapper around its `{% if rows %}` chain.
**`runs.html`** — same wrapper around its `{% if rows %}` chain, which
also contains the `{% if any_stale %}` notice.

### 2. The `/posts` filter form

Replace `data-auto` with the htmx attributes. The trigger list is
deliberately a one-to-one translation of the `app.js` block it deletes —
`change` on selects and dates, a 500 ms debounce on the search box — so
that this is a refactor and not a retuning:

```html
<form class="filter-bar" method="get" action="/posts"
      hx-get="/posts"
      hx-trigger="submit,
                  change from:select,
                  change from:input[type='date'],
                  keyup changed delay:500ms from:input[type='search']"
      hx-select="#results"
      hx-target="#results"
      hx-swap="outerHTML"
      hx-select-oob="#csv-link"
      hx-push-url="true"
      hx-indicator="#results">
```

Notes on each choice:

- **`submit` is in the trigger list.** Overriding `hx-trigger` removes the
  form's natural `submit` trigger, so without it the Filter button would
  do a full-page submit while everything else swapped — working, but
  inconsistent. With it, htmx intercepts.
- **`change from:select` and `change from:input[type='date']`, not bare
  `change`.** A `<select>` fires `input` as well as `change` in current
  browsers, and a bare `input` trigger would fire twice per selection.
  Explicit sources also make the deleted `app.js` lines auditable against
  this attribute.
- **`hx-select-oob="#csv-link"`** solves the one thing `#results` cannot
  reach: the Download CSV button lives in `.page-head`, outside the swap
  region, and its `href` carries the current filter — so a stale one
  exports the wrong rows. `hx-select-oob` pulls that element out of the
  same response by id and swaps it out of band, with **no markup change
  in the response** and no `hx-swap-oob` attribute to add. Give the
  existing anchor `id="csv-link"`; that is the whole change:

  ```html
  <a class="button" id="csv-link" href="{{ csv_url }}">Download CSV</a>
  ```

  When the database is missing, `csv_url` is empty and the anchor is not
  rendered at all — the response then has no `#csv-link` and htmx skips
  the out-of-band swap, which is the correct outcome because there are no
  results either.
- **`hx-indicator="#results"`** puts htmx's `.htmx-request` class on the
  results region during the fetch, which is what the three hand-written
  rules from Step 01 §7 hang off.

### 3. The `/posts` page-size form

Same treatment, and it already carries the hidden inputs that preserve the
current filters:

```html
<form class="page-size-form" method="get" action="/posts"
      hx-get="/posts"
      hx-trigger="submit, change from:select"
      hx-select="#results" hx-target="#results" hx-swap="outerHTML"
      hx-select-oob="#csv-link" hx-push-url="true">
```

Leave the `<noscript><button type="submit">Apply</button></noscript>`
exactly as it is. It is the no-JS path and this step does not touch it.

### 4. The `/snapshots` filter form

Identical to §2 minus the search box and the CSV link:

```html
hx-get="/snapshots"
hx-trigger="submit, change from:select, change from:input[type='text']"
hx-select="#results" hx-target="#results" hx-swap="outerHTML"
hx-push-url="true" hx-indicator="#results"
```

The Run ID field is `type="text"` with `inputmode="numeric"`, so it takes
`change` (on blur), not a keystroke debounce — matching today's behaviour,
where `app.js` only debounced `input[type='search']` and this field was
never auto-submitted at all.

### 5. Pagination — one edit, three pages

`partials/pagination.html` is included by all three list pages, and every
link in it already carries the correct `href`. Add the same four
attributes to both anchors:

```html
<a class="button" href="{{ pagination.prev_url }}"
   hx-get="{{ pagination.prev_url }}"
   hx-select="#results" hx-target="#results" hx-swap="outerHTML"
   hx-push-url="true">← Previous</a>
```

The disabled states are `<span class="button disabled">` with no `href`
and get nothing. Because the partial lives *inside* `#results`, each swap
replaces the pagination controls along with the rows — which is required,
since the offsets change.

`hx-select-oob="#csv-link"` is not needed here: paging does not change the
filter, so the CSV link is already correct.

### 6. `/crawl` — a panel that polls itself out of existence

`crawl.html` already renders two mutually exclusive states: the running
panel when `status.running`, and the start form when it is not. Wrap the
warnings block, both states, and the "Last crawl from this page" section in
one element, and attach the poll **conditionally**:

```html
<div id="crawl-panel"
     hx-disinherit="hx-target hx-swap hx-select hx-select-oob"
     {% if status.running %}
     hx-get="/crawl"
     hx-trigger="every 2s"
     hx-select="#crawl-panel"
     hx-target="this"
     hx-swap="outerHTML"
     hx-select-oob="#crawl-output:innerHTML, #crawl-announce:innerHTML"
     {% endif %}>
```

That `{% if %}` around the attributes is the whole termination mechanism.
When the crawl ends, the response's `#crawl-panel` is the idle start form,
which carries no `hx-trigger` — so after that swap there is nothing left
polling. No exit-code check on the client, no `location.reload()`, no
failure counter, no consumer of `/api/crawl/status`. `jobs.py` and
`api.py` are untouched.

`hx-disinherit` is hygiene: `hx-target`, `hx-swap`, `hx-select` and
`hx-select-oob` are inheritable, and the Stop button's form sits inside
this element. It does a native POST today, so nothing breaks without the
attribute — but it means a future `hx-post` on that form cannot silently
inherit a target meant for the poll.

### 7. `/crawl` — keep the log element alive

Moving the log `<pre>` out of the swapped region is what preserves the
reader's scroll position for free. Take `#crawl-output` **out** of
`#crawl-panel` and let the out-of-band `#crawl-output:innerHTML` swap from
§6 replace only its text:

```html
{% if status.running or status.exit_code is not none %}
<pre class="source-view" id="crawl-output">{% for line in status.lines %}{{ line }}
{% endfor %}</pre>
{% endif %}
```

**The guard is `status.running or status.exit_code is not none`, not
`status.lines`.** An out-of-band swap can only replace an element that
already exists. Gated on `status.lines`, the `<pre>` would be absent for
the first seconds of a crawl — before any output arrives — and the swap
would then have no target, so the log would stay empty until the next full
page load. The element has to exist for as long as anything might poll it.

The idle "Last crawl from this page" section rendered its own second copy
of the log; delete that, since this one now covers both states.

The `:innerHTML` strategy suffix on `hx-select-oob` is the important part.
The default out-of-band strategy is `outerHTML`, which would replace the
`<pre>` element itself and reset `scrollTop` to 0 on every poll — the
exact jump this is avoiding.

### 8. `/crawl` — a live region that announces twice, not thirty times

Today `aria-live="polite"` sits on a paragraph whose text `app.js`
rewrites every two seconds, so a screen reader hears the elapsed-second
count tick for the whole crawl. Replace it with a persistent,
visually-hidden region **outside** `#crawl-panel` that carries no ticking
value:

```html
<p id="crawl-announce" class="visually-hidden" aria-live="polite">
  {% if status.running %}Crawl running.
  {% elif status.exit_code is not none %}Crawl finished with exit code {{ status.exit_code }}.
  {% endif %}
</p>
```

Because the out-of-band swap uses `:innerHTML`, the live-region element
itself is never replaced — which is what makes the announcement work at
all — and because the text contains no seconds, it changes exactly twice
per crawl. The visible elapsed counter stays where it is inside the panel,
unannounced. `.visually-hidden` already exists in `app.css`.

The `aria-live` attribute comes **off** the visible `<p>` in the running
panel as part of this. Leave the comment above it updated to say why.

### 9. Rewrite `app.js` down to the scroll anchor

Delete two of the three blocks:

- the `form[data-auto]` block (§2–§4 replace it),
- the crawl-status polling block, all ~60 lines (§6 replaces it).

Keep `document.documentElement.classList.add("js")`. The clipboard block
moves to Alpine in [Step 03](./03-alpine-components.md) — until that step
lands, leave it exactly where it is.

Add the one thing htmx cannot do declaratively — preserving "don't yank a
reader who has scrolled up," which the deleted polling block did by hand:

```js
// The log's innerHTML is swapped out of band on every /crawl poll. Only
// follow the tail for a reader who is already at the bottom, so polling
// never pulls the view away from a line being read.
var logAtBottom = true;
document.addEventListener("htmx:beforeSwap", function (event) {
  var log = document.getElementById("crawl-output");
  if (!log) return;
  logAtBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
});
document.addEventListener("htmx:afterSwap", function (event) {
  var log = document.getElementById("crawl-output");
  if (log && logAtBottom) log.scrollTop = log.scrollHeight;
});
```

This is an `htmx:*` document listener rather than an Alpine component on
purpose: the element is replaced on every poll, so any state stored *on*
it would be destroyed with it. The 24-pixel tolerance is carried over
unchanged from the code being deleted.

## Gotchas worth knowing before you start

- **Back-button behaviour.** With `historyCacheSize: 0` (Step 01), htmx
  re-requests the URL on `popstate` instead of restoring from
  `sessionStorage`. That works here only because every route renders a
  complete page — which it does, and must keep doing.
- **`attributesToSettle` includes `style`.** htmx copies `class`, `style`,
  `width` and `height` from the incoming element onto the settling one. It
  is inert here because no template renders a `style` attribute, and
  Step 04 greps to keep it that way — but it is a second reason that grep
  is worth having.
- **`hx-push-url` must produce the no-JS URL.** `hx-get` on a form
  serializes the form, so the pushed URL is the same query string a
  native submit would have built. If a pushed URL ever differs from what
  the form's own `action` + `method` produce, the two paths have diverged
  and something in §2 is wrong.
- **`LimitsMiddleware` is unchanged.** htmx sends its context in headers
  (`HX-Request`, `HX-Current-URL`), not query parameters, so
  `MAX_QUERY_BYTES` and `MAX_Q_CHARS` behave identically.
- **The token cookie rides along.** `TokenAuthMiddleware` sets an
  httponly, `samesite=strict` cookie, and htmx requests are same-origin
  GETs, so they carry it. `selfRequestsOnly` guarantees htmx never sends
  it anywhere else.
- **No `hx-on:`, no `js:` prefix, no expression trigger filters** such as
  `click[ctrlKey]`. All three need the `Function` constructor, which
  `allowEval: false` disables and `script-src 'self'` forbids. Step 04
  adds a grep test so this is enforced rather than remembered.

## Required tests

New, in `tests/test_posts_view.py` and `tests/test_crawl_trigger.py`
alongside the assertions that already cover these pages:

- **The swap region exists and contains the rows.** `GET /posts` renders
  one `id="results"` element, and the rows are inside it. Same for
  `/runs` and `/snapshots`. This is what makes `hx-select` correct, and it
  is cheap to assert with a substring index comparison.
- **`hx-get` and `href`/`action` agree.** For the filter form, the
  page-size form and both pagination anchors, the htmx URL and the
  fallback URL resolve to the same path. This is the concrete form of the
  progressive-enhancement promise.
- **A filtered request renders the region.** `GET /posts?q=kubernetes`
  with an `HX-Request: true` header returns 200 and the same
  `#results` content as without the header — proving there is no
  `HX-Request` branch, which is the point of D1.
- **`#csv-link` carries the filter.** `GET /posts?q=x&order=oldest`
  renders an `id="csv-link"` anchor whose href contains both parameters.
- **The crawl panel polls only while running.** With `crawl_job` stubbed
  running, `GET /crawl` renders `hx-trigger="every 2s"` inside
  `id="crawl-panel"`. With it idle, `GET /crawl` renders `id="crawl-panel"`
  and **no** `hx-trigger` anywhere. These two are the termination
  guarantee from D4; without the second one the poll never stops.
- **The log and the announce region are outside the panel.**
  `#crawl-output` and `#crawl-announce` appear in the response but not
  within the `#crawl-panel` element, or the out-of-band `:innerHTML` swaps
  fight the main swap.
- **The announce text carries no elapsed seconds.** Assert the
  `#crawl-announce` content while running is the fixed string, so a future
  edit cannot reintroduce a per-poll announcement.
- **Every existing test in `tests/test_posts_view.py`,
  `tests/test_runs_view.py`, `tests/test_snapshot_view.py`,
  `tests/test_ui_shell.py` and `tests/test_crawl_trigger.py` passes
  unmodified.** This is the acceptance criterion for the whole step. If
  one of them needs editing, the change stopped being a refactor —
  find out why before editing the test.

## Verification

```console
$ uv run pytest -q
$ uv run crawler serve
```

In the browser, with the console open:

- `/posts` — type in the search box. After 500 ms the rows change, the
  header and footer do not repaint, the caret stays where it was, the URL
  gains `?q=…`, and Download CSV picks up the filter. Back button returns
  the previous results.
- `/posts` — change the page-size select and click through pagination.
  The URL tracks each step and a copied link opens the same view.
- `/crawl` — start a crawl. The log grows every two seconds. Scroll up
  mid-crawl and the view stays put; scroll to the bottom and it follows.
  When the crawl ends, the start form comes back **without a page reload**
  and the Network panel shows the polling stop.
- `sessionStorage` still holds no `htmx-history-cache` key.
- Now disable JavaScript entirely and repeat all three. Filtering works
  via the Filter button, pagination works via the links, the page-size
  form works via its `<noscript>` button, and `/crawl` works with a manual
  refresh to see progress. That is the same behaviour as before v4.
