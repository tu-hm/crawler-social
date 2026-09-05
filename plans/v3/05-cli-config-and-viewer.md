# 05 — CLI, config and viewer

## Config — `config.py`

| Env | Default | Meaning |
| --- | --- | --- |
| `CRAWLER_TOP_COMMENTS` | `0` | Comments per post; `0` disables the pass |
| `CRAWLER_COMMENTS_MAX_POSTS` | `10` | Permalink navigations per run |
| `CRAWLER_EXPAND_TEXT` | `true` | Click "See more" before capturing |

Parsed with the same explicit validation style as `CRAWLER_SERVE_PORT`: a
bad value raises with a message naming the variable and the value.
`.env.example` documents all three.

## CLI — `cli.py`

```
crawler crawl URL [--limit N] [--comments N] [--comments-max-posts M]
                  [--expand / --no-expand]
crawler comments [--post-id ID] [--limit N]
```

The three crawl options default to `None` and fall back to the config, so
`.env` stays the place to set them permanently. The summary line grows a
`comments: N` field only when the pass ran.

## Web viewer

- **`server/jobs.py`** — `start()` takes keyword-only `comments` and
  `comments_max_posts` and appends `--comments`/`--comments-max-posts` to
  the argv. Keyword-only with defaults, so existing callers and the
  crawl-trigger tests are unaffected.
- **`server/pages.py`** — the crawl form reads a `comments` field, clamped
  to `0..100`; post detail loads the post's comments.
- **`templates/crawl.html`** — a "Top comments per post" number input with
  a one-line explanation that 0 means off.
- **`templates/post.html`** — a Comments panel: rank, author, time, likes,
  body; empty state says comments were never crawled for this post.
- **`server/api.py`** — `CommentOut` and
  `GET /api/posts/{post_id:path}/comments`, registered **before**
  `GET /api/posts/{post_id:path}` so the greedy path converter does not
  swallow it. `PostOut` gains `post_url`.

## Docs

`README.md` gets a "Long posts and comments" section: what `--comments`
does, why it is off by default, what "top" means, and the note that the
permalink pass is one extra navigation per post.

## Verification

`tests/test_smoke_routes.py` discovers the new API route automatically and
its count assertion keeps it covered. New cases: the crawl form passes
`comments` through to `jobs.start`, and the post page renders comments.
