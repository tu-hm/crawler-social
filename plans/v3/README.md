# plans/v3 — full post text and top comments

v1 built the capture/parse/store pipeline. v2 built the read-only viewer.
v3 makes the *content* complete:

1. **Full post text.** Facebook truncates a long post behind a "See more"
   button. The snapshot we store today holds the truncated text, so the
   parser can only ever store the truncated text. Fix it where the
   truncation happens: click "See more" in the browser *before* reading
   `page.content()`, so the stored HTML is already complete and every
   re-parse of an old snapshot stays honest.

2. **Top N comments per post.** An opt-in second pass that visits each
   captured post's permalink, expands its top comments, stores that page
   as a normal snapshot, and parses comments out of it.

## Steps

| Step | File | What it delivers |
| --- | --- | --- |
| 00 | [00-scope-and-decisions.md](00-scope-and-decisions.md) | Boundaries, the decisions that shape the rest, non-goals |
| 01 | [01-expand-long-post-text.md](01-expand-long-post-text.md) | "See more" expansion in `facebook.py`, post permalink in `parser.py` |
| 02 | [02-comments-storage.md](02-comments-storage.md) | `comments` table, `posts.post_url`, forward-compatible reads |
| 03 | [03-comment-capture.md](03-comment-capture.md) | `facebook.capture_comments()` — the permalink pass |
| 04 | [04-comment-parser.md](04-comment-parser.md) | `parser.parse_comments()` — pure, offline, best-effort |
| 05 | [05-cli-config-and-viewer.md](05-cli-config-and-viewer.md) | `--comments N`, env config, crawl form, API and post page |

## Invariants v3 must not break

- The parser stays pure: bytes in, dataclasses out. No browser, no clock,
  no database, no network.
- Raw HTML commits before anything parses it. That holds for permalink
  snapshots too — a comment we failed to parse is still recoverable from
  the stored bytes via `/snapshots/{id}/reparse`.
- The viewer never writes. It must therefore tolerate a database written
  by an older crawler (no `comments` table, no `posts.post_url`).
- Comments are **off by default**. A crawl behaves exactly as it does
  today until someone asks for them.
