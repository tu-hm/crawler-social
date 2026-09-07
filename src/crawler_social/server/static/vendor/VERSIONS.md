# Vendored JavaScript

Served from disk under `/static/vendor/`. No npm, no bundler, no CDN at
runtime. Re-fetch with `make vendor`; check with `make vendor-verify`.

| File | Package | Version | Upstream | Fetched |
| --- | --- | --- | --- | --- |
| `htmx.min.js` | `htmx.org` | 2.0.6 | https://unpkg.com/htmx.org@2.0.6/dist/htmx.min.js | 2026-09-06 |
| `alpine-csp.min.js` | `@alpinejs/csp` | 3.14.9 | https://cdn.jsdelivr.net/npm/@alpinejs/csp@3.14.9/dist/cdn.min.js | 2026-09-06 |

`alpine-csp.min.js` must be the **CSP build**, not stock Alpine. The two
are the same size and the same upstream filename, and grepping either for
`Function(` gives the same answer — the reliable check is the string
`CSP-friendly build`, which only the CSP build carries. `script-src 'self'`
in `hardening.py` has no `unsafe-eval`, so stock Alpine cannot evaluate a
single attribute.
