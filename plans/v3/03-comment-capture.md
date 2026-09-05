# 03 — Comment capture (the permalink pass)

## `facebook.CommentOptions`

```python
top_n: int = 0            # comments wanted per post; 0 disables the pass
max_posts: int = 10       # hard ceiling on permalink navigations per run
max_more_clicks: int = 6  # "view more comments" clicks per post
max_expand_clicks: int = 20
settle_ms: int = 2500     # after goto, before touching anything
pause_ms: int = 2000      # between posts, jittered
```

## `facebook.post_permalink(page_url, post_id) -> str`

`{page_url without query, trailing slash}/posts/{post_id}` — the fallback
for a post whose article carried no usable link (step 01).

## `facebook.CommentCapture`

```python
@dataclass(frozen=True)
class CommentCapture:
    post_id: str
    post_url: str
    captured_at: str | None
    html: bytes | None
    error: str | None = None
```

A generator cannot return a diagnostics list, and a per-post failure must
not abort the pass (D6) — so failures are *yielded* as a record with
`error` set and `html` unset.

## `facebook.capture_comments(targets, conn, run_id, config, options=None, should_stop=None)`

`targets` is `[(post_id, permalink_url), ...]`, already capped by the
caller. One `browser_session` for the whole pass (D5). Per target:

1. `should_stop()` / deadline check.
2. `page.goto(url)`, settle.
3. `inspect()` → wall verdict. A blocking verdict commits the snapshot as
   evidence and raises `BlockedError`, exactly as the feed loop does. The
   pass ends; already-yielded comments are already committed.
4. Click `MORE_COMMENTS_PATTERN` until the visible comment count reaches
   `top_n`, the count stops growing, or the click budget is spent.
5. Expand every `SEE_MORE_PATTERN` — comment bodies truncate too.
6. `inspect()` again, `db.save_snapshot(...)` (raw before parse), yield.
7. Jittered pause before the next post.

Steps 4–5 are wrapped so a per-post Playwright failure yields a
`CommentCapture` with `error` and continues to the next target.

## `pipeline.run_crawl`

After the post transaction commits, and only when `comment_options.top_n`:

- build targets from `pending_posts` — **new posts first**, then
  already-known ones, capped at `max_posts`;
- for each yielded capture: `parser.parse_comments(...)`, then one
  transaction per post writing its comments;
- count into `RunSummary.comments_captured` / `posts_with_comments`;
- `BlockedError` sets `summary.blocked` / status `failed` (posts stay
  committed); any other exception becomes a diagnostic and the run stays
  `completed`.

`RunSummary` gains `comments_captured: int = 0` and
`posts_with_comments: int = 0`, both defaulted so nothing that builds a
summary today breaks.

## Verification

`tests/test_comments_pipeline.py` stubs `facebook.capture_comments` the way
the existing tests stub `capture_snapshots` and asserts: comments land in
the table, a per-post `error` record is a diagnostic and not a failure, a
`BlockedError` mid-pass keeps the posts and marks the run blocked, and
`--comments 0` never calls the pass at all.
