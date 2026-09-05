# v2 — Server, web UI, and data viewer

Give the existing crawler a local web face: an HTTP server over `data/social.db`, a
plain HTML UI, and a data viewer for posts, snapshots, and runs.

Complete these plans in order:

1. [Step 00 — Decide scope and verify the environment](./00-scope-and-preflight.md)
2. [Step 01 — Build the read-only query layer](./01-read-query-layer.md)
3. [Step 02 — Stand up the HTTP server](./02-server-skeleton.md)
4. [Step 03 — Expose the JSON API](./03-json-api.md)
5. [Step 04 — Build the UI shell](./04-ui-shell.md)
6. [Step 05 — Build the posts data viewer](./05-posts-viewer.md)
7. [Step 06 — Build post detail and the snapshot viewer](./06-post-detail-and-snapshots.md)
8. [Step 07 — Build the runs and health dashboard](./07-runs-dashboard.md)
9. [Step 08 — Trigger a crawl from the UI](./08-trigger-crawl.md)
10. [Step 09 — Harden the server](./09-hardening.md)
11. [Step 10 — Prove the server before expanding](./10-verification-gate.md)

Rules:

- The server reads; the crawler writes. Steps 01–07 open SQLite read-only and never
  create or migrate a table.
- One writer only. Anything that starts a crawl goes through the existing
  `FileLock` in `lock.py`; never run two crawls at once.
- No Node, no npm, no bundler. Templates are Jinja2; CSS and JS are static files
  served from disk.
- Bind to `127.0.0.1` by default. A non-loopback bind requires an explicit flag and
  a token (Step 09).
- Raw snapshot HTML is untrusted third-party markup. Never render it into a page;
  serve it sandboxed (Step 06).
- Every step keeps `uv run pytest` green and the existing CLI unchanged.
- Support macOS and Linux; OS-specific code stays behind helpers in `paths.py`.
