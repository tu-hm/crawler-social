# 05 — The addendum to ADR-0066

## Outcome

`docs/DECISIONS.md` records what v4 changed, in eight lines, without
superseding anything.

## Why this is an addendum and not a new ADR

The file's own preamble says its purpose is *"that someone reading in six
months — very likely the author — does not relitigate a settled
question."* Someone opening `static/vendor/` next spring and finding two
minified libraries, against an ADR titled *"no JavaScript framework, no
build step,"* will relitigate it. Eight lines prevent that.

It is an addendum because the decision held. Re-read
[ADR-0066](../../docs/DECISIONS.md#adr-0066) against what v4 shipped:

| ADR-0066 says | After v4 |
| --- | --- |
| Jinja2 templates rendered on the server | Unchanged. Every page renders complete, server-side. |
| No build step | Unchanged. `make vendor` re-fetches; a clone plus `uv sync` works offline. |
| No `node_modules` tree | Unchanged. No npm, no lockfile, no bundler. |
| Rejected: fetch-and-patch, "two rendering paths to keep in sync" | Upheld, and this is the point of D1 — fragments are cut from the page's own response, so there is still exactly one path. |
| The CSP forbids inline script and style | Unchanged, and now grep-tested against two more failure modes. |
| "exactly two static files (`app.css`, `app.js`)" | **Changed.** Four files, two of them vendored third-party. |
| Consequence: "every action costs a page load" | **Changed** for three interactions. |

One sentence of the decision and one of its consequences moved. That is an
amendment to an entry, not a reversal of it.

## Work

Append to the ADR-0066 entry, above its closing `---`:

```markdown
**Revisited 2026-09-06 (plans/v4).** Two vendored files were added —
`static/vendor/htmx.min.js` and `static/vendor/alpine-csp.min.js` — so
that `/posts` filtering and `/crawl` polling swap a region instead of
reloading the page, and `app.js` shrank from 109 lines to ~30. The
decision itself stands: Jinja still renders every page complete on the
server, there is still no build step, no npm and no `node_modules`, and
htmx re-requests the *same route* and selects a fragment out of the full
response — so "two rendering paths to keep in sync" is still rejected and
there is still exactly one. What changed is the file count ("exactly two
static files" is now four) and the consequence that every action costs a
page load. Alpine had to be the `@alpinejs/csp` build: `script-src 'self'`
carries no `unsafe-eval`, and stock Alpine compiles attribute expressions
with the `Function` constructor. htmx needs
`includeIndicatorStyles: false` because it otherwise injects a `<style>`
element on init, `allowScriptTags: false`, and `historyCacheSize: 0` so
rendered post text never reaches `sessionStorage` (ADR-0022). All of it is
grep-tested in `tests/test_htmx_contract.py`.
```

Then, if v4 is taken as far as Step 03, add one line to the *Index*
table's section F so the addendum is discoverable from the top of the
file.

## Verification

Read ADR-0066 top to bottom as if for the first time. It should be
possible to answer, without opening any other file: why are there
JavaScript libraries in a project whose ADR says no framework, why is
Alpine's filename `alpine-csp`, and where is the test that stops someone
undoing either. If any of the three needs a second file to answer,
the addendum is too short.
