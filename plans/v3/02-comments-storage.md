# 02 — Comment storage

## Schema — `src/crawler_social/schema.py`

```sql
CREATE TABLE IF NOT EXISTS comments (
    comment_id    TEXT PRIMARY KEY,
    post_id       TEXT NOT NULL REFERENCES posts(post_id),
    page_url      TEXT NOT NULL,   -- the permalink the comment was read from
    author        TEXT,
    text          TEXT,
    published_at  TEXT,
    like_count    INTEGER,
    rank_index    INTEGER NOT NULL, -- 1 = top comment, in Facebook's order
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id, rank_index);
```

`rank_index`, not `rank`: `RANK` is a window-function keyword and quoting
it in every statement is worse than naming it plainly.

`posts` gains `post_url TEXT` (nullable — old rows have none).

## Migration — `src/crawler_social/db.py`

`CREATE TABLE IF NOT EXISTS` does nothing for a table that already exists,
so `posts.post_url` needs a real migration. `connect()` runs
`_migrate_columns()` after the schema script: read `PRAGMA table_info`, add
whatever is missing. Additive only — no rewrites, no drops, safe to run on
the 160 MB database that already exists.

## Writes — `db.py`

```python
def upsert_comment(conn, comment_id, post_id, page_url, author, text,
                   published_at, like_count, rank_index, seen_at=None) -> None
def comment_count(conn, post_id=None) -> int
```

`upsert_comment` mirrors `upsert_post`: `COALESCE` on the nullable fields so
a later, poorer parse never erases a better one, and `last_seen` always
advances. `rank_index` *is* overwritten — the newest observed ranking is
the interesting one.

`upsert_post` gains an optional `post_url` argument, coalesced the same way.

## Reads — `src/crawler_social/queries.py`

The viewer opens the file read-only and cannot migrate it, so it must cope
with a database an older crawler wrote:

- `_post_columns(conn)` replaces the `_POST_COLUMNS` constant and appends
  `post_url` only when `PRAGMA table_info(posts)` reports it;
- `_has_table(conn, name)` guards the comment queries, which return empty
  results rather than raising `no such table: comments`.

New:

```python
def list_comments(conn, *, post_id, limit, offset=0) -> tuple[list[dict], int]
def comment_counts(conn, post_ids) -> dict[str, int]
```

`comment_counts` takes a batch of ids so the posts list can show a badge
without N+1 queries.

## Verification

`tests/test_db.py`: upsert/COALESCE/rank behaviour, and a migration test
that builds a database from the *old* schema text and asserts `connect()`
adds the column without touching the rows.
`tests/test_queries.py`: comment listing, and a read against a database
with no `comments` table.
