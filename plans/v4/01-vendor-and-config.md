# 01 — Vendor the two libraries and configure htmx

## Outcome

`/static/vendor/htmx.min.js` and `/static/vendor/alpine-csp.min.js` are
served from disk, pinned, checksummed, and loaded by `base.html`. No page
behaviour changes yet — this step is only the loading and configuration
surface, so that Steps 02 and 03 are pure template edits.

## Depends on

- [Step 00](./00-scope-and-decisions.md) read, in particular D5 and D6.

## 0. What was already verified

These are not assumptions. Both files were fetched and inspected while
this plan was written, so the pins below are known-good and the checks in
§5 are known to discriminate:

| | htmx | Alpine (CSP build) |
| --- | --- | --- |
| Package | `htmx.org@2.0.6` | `@alpinejs/csp@3.14.9` |
| Size | 51,007 bytes | 45,328 bytes |
| SHA-256 | `b6768eed4f3af85b73a75054701bd60e17cac718aef2b7f6b254e5e0e2045616` | `820bc9503874024057da244dc30c21301548329eb9a00e7937e91c4d3908b273` |

Confirmed in the bytes: all four htmx config keys exist with the defaults
[Step 00](./00-scope-and-decisions.md) D6 claims; the indicator `<style>`
injection is real and its exact rules are reproduced in §7; the history
cache is `sessionStorage["htmx-history-cache"]`, not `localStorage`;
`hx-select-oob` and the `:strategy` suffix are present; and the CSP build
is distinguishable from stock only by the marker in §5 — **not** by
grepping for `Function(`, which appears identically in both.

If you pin different versions, redo §5 rather than trusting this table.

## Work, in order

1. Create `src/crawler_social/server/static/vendor/`. It sits inside the
   package so `uv_build` ships it exactly the way `app.css` and `app.js`
   already ship, and so `StaticFiles(directory=STATIC_DIR)` serves it with
   no mount change.

2. Add a `vendor` target to the `Makefile` and to its `.PHONY` line. Pin
   exact versions in variables at the top so the pin is one edit:

   ```make
   HTMX_VERSION ?= 2.0.6
   ALPINE_CSP_VERSION ?= 3.14.9
   VENDOR := src/crawler_social/server/static/vendor

   vendor:
   	curl -fsSL -o $(VENDOR)/htmx.min.js \
   	  https://unpkg.com/htmx.org@$(HTMX_VERSION)/dist/htmx.min.js
   	curl -fsSL -o $(VENDOR)/alpine-csp.min.js \
   	  https://cdn.jsdelivr.net/npm/@alpinejs/csp@$(ALPINE_CSP_VERSION)/dist/cdn.min.js
   	cd $(VENDOR) && shasum -a 256 htmx.min.js alpine-csp.min.js > SHASUMS256
   	@echo "pinned htmx $(HTMX_VERSION), alpine-csp $(ALPINE_CSP_VERSION)"

   vendor-verify:
   	cd $(VENDOR) && shasum -a 256 --check SHASUMS256
   ```

   Both versions above are the ones verified in §0, so `make vendor`
   followed by `make vendor-verify` should reproduce the two SHA-256
   digests in that table exactly. If they differ, the upstream artifact
   changed under a fixed version tag and that is worth understanding
   before committing it.

3. Commit all three files: both `.js` files and `SHASUMS256`. This is the
   point of the exercise. `make vendor` exists to *re-fetch and diff*, not
   to be a build step someone has to run before the server works. A fresh
   `git clone` plus `uv sync` must produce a working viewer with no
   network access and no Node installed.

4. Write `src/crawler_social/server/static/vendor/VERSIONS.md` — four
   lines naming each file, its version, its upstream URL, and the date
   fetched. `SHASUMS256` proves the bytes did not change; this says what
   they were supposed to be.

5. Confirm the vendored Alpine is the **CSP build** before going further,
   because the failure mode is silent and the obvious check does not work.
   Both builds are ~45 KB, both are named `cdn.min.js` upstream, and
   grepping either for `Function(`, `new Function` or `AsyncFunction`
   returns the same answer — stock Alpine reaches its evaluator through
   `Object.getPrototypeOf(async function(){}).constructor`, which survives
   minification in both files.

   The reliable discriminator is a string only the CSP build carries:

   ```console
   $ grep -c 'CSP-friendly build' src/crawler_social/server/static/vendor/alpine-csp.min.js
   1
   ```

   One or more means the CSP build. Zero means stock was fetched, and
   every Alpine attribute will raise a CSP violation on first interaction
   while the page looks perfectly fine on load.

6. Edit `base.html`'s `<head>`. The full diff:

   ```html
     <meta name="viewport" content="width=device-width, initial-scale=1">
   + <meta name="htmx-config" content='{"includeIndicatorStyles":false,"historyCacheSize":0,"allowEval":false,"allowScriptTags":false,"selfRequestsOnly":true}'>
     <title>{% block title %}crawler-social{% endblock %}</title>
     <link rel="stylesheet" href="/static/app.css">
   + <script src="/static/vendor/htmx.min.js" defer></script>
     <script src="/static/app.js" defer></script>
   + <script src="/static/vendor/alpine-csp.min.js" defer></script>
   ```

   Three things about that ordering and quoting:

   - **`app.js` before Alpine.** Alpine dispatches `alpine:init` when it
     starts, and `app.js` is where Step 03 registers components by
     listening for it. Deferred scripts execute in document order, all
     before `DOMContentLoaded`, so either order happens to work today —
     put `app.js` first anyway so it keeps working if a future Alpine
     build starts eagerly.
   - **Single-quoted `content`.** The JSON contains double quotes. Jinja
     autoescaping does not touch this string (it is template literal, not
     an expression), but a double-quoted attribute would need entities and
     become unreadable.
   - **The meta tag is not inline script.** `INLINE_SCRIPT` in
     `tests/test_server_security.py` matches `<script` without a `src=`,
     and `<meta>` is neither. This is the only reason htmx can be
     configured at all without adding a third static file or an inline
     block — see [Step 00](./00-scope-and-decisions.md) D6.

7. Hand-write the indicator rules htmx will no longer inject. Append to
   `app.css`, next to the existing `@media (prefers-reduced-motion)` block
   so the guard is visible:

   ```css
   /* htmx injects these itself unless includeIndicatorStyles is false, and
      an injected <style> element violates style-src 'self'. Copied from
      htmx 2.0.6 verbatim so behaviour is identical to the default. */
   .htmx-indicator { opacity: 0; }
   .htmx-request .htmx-indicator { opacity: 1; transition: opacity 200ms ease-in; }
   .htmx-request.htmx-indicator { opacity: 1; transition: opacity 200ms ease-in; }
   ```

   Those three rules are the exact ones htmx would have injected — note
   that the transition is on the `.htmx-request` selectors, not on
   `.htmx-indicator` itself, so an indicator fades in but does not fade
   out. Keep it that way; matching the default means the CSS is not a
   second thing to reason about.

   The existing `@media (prefers-reduced-motion: reduce)` block already
   zeroes transitions globally; check that it covers these and extend it if
   it does not.

8. Do not change `hardening.py`. `APP_CSP` already permits everything this
   needs: `script-src 'self'` covers both vendored files, `connect-src
   'self'` covers every htmx request, and `style-src 'self'` is satisfied
   precisely because of steps 6 and 7.

## Required tests

Add to `tests/test_server_security.py`, which is where the CSP and
inline-content discipline already lives:

- `GET /static/vendor/htmx.min.js` returns 200 with a JavaScript
  content-type. Same for `alpine-csp.min.js`.
- The vendored files' SHA-256 digests match `SHASUMS256`. Read the file,
  hash the bytes, compare — this is the test that makes the pin real, and
  it fails loudly if someone hand-edits a minified library.
- `alpine-csp.min.js` contains `CSP-friendly build` — the §5 check as a
  test, so the wrong-package mistake cannot survive a commit. Do **not**
  write this as a `Function(` assertion; §5 explains why that passes on
  both builds.
- `base.html`'s rendered output carries the `htmx-config` meta tag, and the
  parsed JSON has `includeIndicatorStyles == false`,
  `historyCacheSize == 0`, `allowEval == false` and
  `allowScriptTags == false`. Assert the parsed values, not the string: a
  reformatted attribute must not fail, and a changed value must.
- `test_no_inline_script_or_style_on_any_page` still passes unchanged, on
  every route in `HTML_ROUTES`. No `# noqa`, no added exemption. If this
  test needs an exemption, step 6 or 7 is wrong.
- The existing assertion that rendered HTML references no external
  `http://` or `https://` URL still passes — the two `<script src>` values
  are root-relative.

## Verification

```console
$ make vendor-verify
htmx.min.js: OK
alpine-csp.min.js: OK

$ uv run pytest tests/test_server_security.py -q
$ uv run crawler serve
```

Then, in the browser, with the network panel open and the machine's
network interface **off**:

- Every page still renders and the two `/static/vendor/*.js` requests are
  200s from disk.
- The console is empty. In particular there is no
  `Refused to apply inline style` or `Refused to evaluate a string as
  JavaScript` violation — the first means step 7 was skipped, the second
  means the wrong Alpine build was vendored.
- `sessionStorage` holds no `htmx-history-cache` key after navigating
  between `/posts` pages. htmx writes up to ten rendered pages there
  unless `historyCacheSize` is 0. Check `sessionStorage`, not
  `localStorage` — htmx 2.0.6 uses the former.

Nothing on any page behaves differently yet. That is the expected result
of this step.
