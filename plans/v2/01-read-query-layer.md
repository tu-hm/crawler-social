# Step 01 — Build the read-only query layer

## Outcome

A `queries.py` module answers every question the UI will ask — posts with filters and
paging, one post, runs, snapshot metadata, snapshot bytes, and summary counts — without
ever writing to the database.

## Depends on

- [Step 00](./00-scope-and-preflight.md) is complete.

## Why a separate module

`db.connect()` in `db.py` runs `executescript(SCHEMA_SQL)` on every open. That is correct
for the crawler and wrong for a viewer: the server must not be able to create tables. The
read path gets its own connect helper and its own module so the distinction is enforced by
construction, not by discipline.

## Work, in order

1. Add `src/crawler_social/queries.py`.
2. Implement `connect_ro(path)`:
   - open `sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)`;
   - set `row_factory = sqlite3.Row` so results carry column names;
   - apply `PRAGMA busy_timeout=5000`;
   - raise a clear `DatabaseMissingError` when the file does not exist, naming the path
     and suggesting `crawler crawl`.
3. Implement the query functions. Every one takes an open connection and returns plain
   dicts or lists of dicts — no `sqlite3.Row` objects escape the module.
   - `list_posts(conn, *, limit, offset, contains=None, page_url=None, since=None, until=None, order="newest")`
     returns `(rows, total_count)`. Reuse the `LIKE ... ESCAPE '\'` escaping already in
     `db.list_posts`; never build SQL by string interpolation of user input.
   - `get_post(conn, post_id)` returns one post or `None`.
   - `list_pages(conn)` returns each distinct `page_url` with its post count and newest
     `published_at`, for the filter dropdown.
   - `list_runs(conn, *, limit, offset)` returns `(rows, total_count)` from `runs`,
     newest first, each row carrying its snapshot count.
   - `get_run(conn, run_id)` returns one run or `None`.
   - `list_snapshots(conn, *, run_id=None, page_url=None, limit, offset)` returns
     `(rows, total_count)` selecting `id, run_id, page_url, captured_at, sha256,
     length(html) AS size_bytes` — never the blob itself.
   - `get_snapshot_html(conn, snapshot_id)` returns the raw `bytes` or `None`. This is the
     only function that reads the blob.
   - `get_state(conn)` returns every `state` row.
   - `summary(conn)` returns total posts, total snapshots, total runs, last run status and
     time, newest post time, and total snapshot bytes.
4. Cap `limit` inside the module at a constant `MAX_PAGE_SIZE = 200`, and clamp `offset`
   at zero. The HTTP layer must not be the only thing enforcing this.
5. Sort posts by `COALESCE(published_at, last_seen)`, matching `db.list_posts`, so the CLI
   and the UI agree on ordering. Support `order="oldest"` as the reverse.
6. Add the indexes the viewer needs, as a one-off migration run by the **crawler**, not
   the server — extend `schema.py` so `db.connect()` creates them:
   - `CREATE INDEX IF NOT EXISTS idx_posts_page_url ON posts(page_url)`
   - `CREATE INDEX IF NOT EXISTS idx_posts_sort ON posts(COALESCE(published_at, last_seen))`
   - `CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots(run_id)`
   - `CREATE INDEX IF NOT EXISTS idx_snapshots_page ON snapshots(page_url)`

## Required tests

Build a temporary database with `db.connect()` and known fixture rows, then query it
through `connect_ro`.

- `connect_ro` on a missing file raises `DatabaseMissingError` naming the path.
- `connect_ro` cannot `CREATE TABLE` or `INSERT`.
- `list_posts` returns newest first, and `order="oldest"` reverses it.
- `list_posts` paging is stable: `limit=2, offset=0` and `limit=2, offset=2` do not
  overlap and together match `limit=4, offset=0`.
- `list_posts(contains=...)` matches a substring and returns the right `total_count`.
- A `contains` value of `%` or `_` matches literally, not as a wildcard.
- `list_posts(page_url=...)` filters to one page and excludes the others.
- `limit=10_000` is clamped to `MAX_PAGE_SIZE`; `offset=-5` is clamped to `0`.
- `list_snapshots` rows carry `size_bytes` and no `html` key.
- `get_snapshot_html` returns the exact bytes stored, and `None` for a missing id.
- `summary` on an empty database returns zeros rather than raising.
- Applying the extended schema twice is still safe (the new indexes are `IF NOT EXISTS`).

## Verification

```console
uv run pytest tests/test_queries.py
uv run crawler posts --limit 5
```

The tests pass and the CLI is unaffected by the new indexes.
