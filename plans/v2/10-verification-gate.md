# Step 10 — Prove the server before expanding

## Outcome

The server, the UI, and the data viewer are demonstrably correct on real crawled data.
This is the gate: nothing new gets added to the web surface until every check below
passes.

## Depends on

- [Step 09](./09-hardening.md) is complete.

## Work, in order

1. Add `make serve`, `make serve-check`, and `make test-server` targets to the `Makefile`,
   following the existing style where `crawl` depends on `test`.
2. Add a `tests/test_smoke_routes.py` that walks the entire route table — every HTML page
   and every API endpoint, with representative parameters — against a seeded temporary
   database and asserts none returns 5xx. New routes get added here as they are written.
3. Add a fixture-backed end-to-end test that does not need a browser:
   - build a temporary database;
   - insert `tests/fixtures/facebook_page_sample.html` as a snapshot;
   - run `parser.parse()` and upsert the resulting posts through `db.py`;
   - serve that database and assert the posts appear on `/posts`, each detail page renders,
     and the snapshot's reparse view reports the same posts.
   This is the test that catches a schema or parser change breaking the viewer.
4. Update `README.md` with a "Web UI" section: the start command, the URL, a short tour of
   the five pages, and the security posture from Step 09.
5. Update `docs/DATA-MODEL.md` noting that the server reads the same four tables and adds
   only indexes.
6. Record in `docs/DECISIONS.md`: FastAPI over a bare stdlib server, server-rendered HTML
   over a JS framework, subprocess over thread for crawl triggering (with the
   `signal.signal` reason), and read-only-by-construction for the query layer.

## The gate — run these by hand

Manual checks, on a machine with real crawled data:

1. Start the server; the home page numbers match `uv run crawler posts --limit 10` and
   `sqlite3 data/social.db "SELECT COUNT(*) FROM posts"`.
2. Search `/posts` for a term you know appears in one post; exactly that post is listed.
3. Page to the end of the result set; no row is duplicated and none is skipped.
4. Open a post; every field matches the row from
   `sqlite3 data/social.db "SELECT * FROM posts WHERE post_id = '...'"`.
5. Open a snapshot preview; the browser's network tab shows zero external requests.
6. Open the snapshot source view; the markup is escaped text, not a rendered page.
7. Run the reparse view; the post count matches what that snapshot originally produced.
8. Start a crawl from the UI while one is already running from the terminal; the refusal
   is readable and no second browser opens.
9. Delete `data/social.db`, reload the home page; the empty state appears with the crawl
   command, and no traceback reaches the browser.
10. Crawl once more, reload; new posts appear without restarting the server.

## Required tests

- `tests/test_smoke_routes.py` covers every registered route: assert the count of routes
  exercised equals `len(app.routes)` minus the static mount and the docs routes, so a new
  route cannot be added without a smoke test.
- The fixture end-to-end test passes with no browser and no network.
- `uv run pytest` passes from a clean checkout after `uv sync --dev`.
- `PRAGMA integrity_check` on the database after a UI-triggered crawl returns `ok`.

## Verification

```console
make test
make test-server
uv run crawler serve
```

Everything is green, the ten manual checks pass, and the CLI behaves exactly as it did
before v2 started.

## Not in v2

Deliberately out of scope, to be planned separately if wanted:

- editing or deleting data through the UI;
- authentication beyond a single shared token;
- multi-user access, accounts, or roles;
- a second crawl source (the v1 plans' Telegram, Reddit, X, Zalo steps);
- a scheduler — `plans/09-daily-scheduling.md` covers that and stays a CLI concern;
- any JavaScript framework or build step.
