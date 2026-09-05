# Step 03 — Expose the JSON API

## Outcome

Every piece of stored data is reachable over HTTP as JSON, with paging, filters, and
honest error codes. The UI in later steps is a client of this API.

## Depends on

- [Step 02](./02-server-skeleton.md) is complete.

## Work, in order

1. Add `src/crawler_social/server/api.py` as an `APIRouter` mounted at `/api`.
2. Define Pydantic response models — `PostOut`, `RunOut`, `SnapshotOut`, `SummaryOut`,
   `Page[T]` — so the shape is declared once and the docs are accurate. `Page[T]` carries
   `items`, `total`, `limit`, `offset`.
3. Implement the endpoints, each delegating to `queries.py` and adding no SQL of its own:

   | Method | Path | Notes |
   | --- | --- | --- |
   | `GET` | `/api/summary` | counts, last run, newest post, total snapshot bytes |
   | `GET` | `/api/posts` | `limit`, `offset`, `q`, `page_url`, `since`, `until`, `order` |
   | `GET` | `/api/posts/{post_id}` | 404 when unknown |
   | `GET` | `/api/pages` | distinct pages with counts, for the filter dropdown |
   | `GET` | `/api/runs` | `limit`, `offset`, newest first |
   | `GET` | `/api/runs/{run_id}` | 404 when unknown; includes its snapshot list |
   | `GET` | `/api/snapshots` | `run_id`, `page_url`, `limit`, `offset`; metadata only |
   | `GET` | `/api/snapshots/{id}/raw` | the stored bytes; see Step 06 for the headers |
   | `GET` | `/api/state` | watermark rows from `state` |

4. Validate query parameters with FastAPI types, not hand-written checks: `limit` as
   `int = Query(50, ge=1, le=200)`, `offset` as `int = Query(0, ge=0)`, `order` as a
   `Literal["newest", "oldest"]`. Malformed input must produce 422, never 500.
5. Parse `since` and `until` as ISO-8601 dates or datetimes and compare against the stored
   UTC ISO-8601 strings. Reject an unparseable value with 422 naming the parameter.
6. `post_id` values come from Facebook and can contain characters that need escaping in a
   path. Accept them as a path parameter, and make sure a value with a slash or a percent
   sign round-trips — add a test for it rather than assuming.
7. Never return the snapshot blob from a list endpoint. `size_bytes` only.
8. Add `GET /api/export/posts.csv` streaming the current filter's full result set as CSV
   with a `Content-Disposition: attachment` header. Stream it row by row; do not build the
   whole file in memory.
9. Keep the response of every endpoint JSON-serializable with no `datetime` objects — the
   database already stores UTC ISO-8601 strings, so pass them through unchanged.

## Required tests

Seed a temporary database with a known set of posts, runs, and snapshots.

- `/api/summary` matches the seeded counts.
- `/api/posts` defaults to 50 items, newest first.
- `/api/posts?limit=201` returns 422; `limit=0` returns 422; `offset=-1` returns 422.
- `/api/posts?q=...` filters, and `total` reflects the filter, not the table size.
- `/api/posts?page_url=...` filters to one page.
- `/api/posts?since=2026-01-01` excludes older posts; `since=not-a-date` returns 422.
- `/api/posts?order=oldest` reverses the order; `order=sideways` returns 422.
- Paging two windows covers the same rows as one wide window, with no duplicates.
- `/api/posts/{id}` returns the post; an unknown id returns 404 with a JSON body.
- A post id containing `/` and `%` is retrievable through its URL-encoded path.
- `/api/snapshots` rows contain `size_bytes` and no `html`.
- `/api/runs/{id}` for an unknown id returns 404.
- `/api/export/posts.csv` returns the filtered rows with a header line and an attachment
  disposition.

## Verification

```console
uv run pytest tests/test_api.py
uv run crawler serve &
curl -s "http://127.0.0.1:8765/api/summary" | head -c 400
curl -s "http://127.0.0.1:8765/api/posts?limit=3" | head -c 800
```

Real crawled data comes back, and the counts match `uv run crawler posts`.
