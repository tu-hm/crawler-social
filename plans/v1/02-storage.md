# Step 02 — Build the four-table database

## Outcome

`data/social.db` stores crawl runs, raw snapshots, parsed posts, and incremental state using
portable SQLite available through Python on macOS and Linux.

## Depends on

- [Step 01](./01-scaffold.md) is complete.

## Work, in order

1. Add `db.py` and one schema file.
2. Create only these tables:
   - `runs`: start time, finish time, status, and error text;
   - `snapshots`: run ID, Page URL, capture time, SHA-256, and raw HTML bytes;
   - `posts`: Facebook post ID, Page URL, text, author, published time, first seen, and
     last seen;
   - `state`: Page URL and last successful post ID/time.
3. Put a unique constraint on snapshot SHA-256 and Facebook post ID.
4. Implement `connect(path)` with:
   - `PRAGMA foreign_keys=ON`;
   - `PRAGMA journal_mode=WAL`;
   - `PRAGMA busy_timeout=5000`.
5. Implement `start_run()`, `finish_run()`, `save_snapshot()`, `upsert_post()`,
   `get_state()`, and `set_state()`.
6. Commit `save_snapshot()` immediately in its own transaction.
7. Commit all post upserts and `set_state()` together in a second transaction.
8. Use `pathlib`; never build paths with `/` string concatenation.
9. Keep timestamps as explicit UTC ISO-8601 strings.

## Required tests

- Applying the schema twice is safe.
- Saving identical HTML twice stores one body hash without raising.
- Upserting the same Facebook post twice leaves one post row.
- The second upsert preserves `first_seen` and advances `last_seen`.
- A failed parser simulation leaves the committed raw snapshot present.
- A failed post transaction leaves the previous `state` unchanged.
- `PRAGMA integrity_check` returns `ok`.

All tests use temporary paths and may not depend on macOS or Linux home-directory layouts.

## Verification

```console
uv run pytest tests/test_db.py
uv run crawler posts --limit 10
```

The tests pass and the empty database query exits successfully with a useful message.
