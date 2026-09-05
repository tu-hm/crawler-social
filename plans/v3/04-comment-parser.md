# 04 — Comment parser

Pure and offline, like `parse()`: bytes + post id + capture time in,
dataclasses out.

## `parser.Comment`

```python
comment_id: str
post_id: str
author: Optional[str]
text: Optional[str]
published_at: Optional[str]
like_count: Optional[int]
rank: int          # 1-based, document order
```

## `parse_comments(html, post_id, captured_at, limit=None)`

Returns `(comments, diagnostics)`.

### Finding comment nodes

1. `[role="article"]` whose `aria-label` starts with a comment word
   (`Comment`, `Reply`, `Bình luận`, `Phản hồi`, `Commentaire`, ...).
2. Fallback for the mbasic/older markup: `div[id^="comment_"]`.

Then drop any node that is a descendant of another matched node — that
removes nested replies structurally (D4) without depending on the
`aria-label` wording distinguishing a reply from a comment.

### Fields

- **id** — first of `comment_id=`, `reply_comment_id=`, `"comment_id":"…"`,
  `id="comment_…"` found inside the node. When none matches, a stable
  synthetic id `"{post_id}:h{blake2s(author|text)[:16]}"`, so a re-run
  updates the same row instead of duplicating it. Editing a comment
  changes the hash and therefore creates a new row; that is documented,
  not accidental.
- **author** — first `<a>`/`<strong>` text inside the node, else the name
  carried in `aria-label` ("Comment by <name>").
- **text** — `div[dir="auto"]` descendants, minus the author line, minus
  UI chrome (`Like`, `Reply`, `Share`, `Thích`, `Trả lời`, `Chia sẻ`,
  bare counts, bare relative timestamps), joined with newlines.
- **published_at** — `data-utime` / `<abbr>` / `<time>` via the existing
  `_extract_published_at`, then relative text on any `<a>` ("2h", "1 ngày").
- **like_count** — a reaction count from an `aria-label` such as
  `"12 reactions"` / `"Like: 3"`; `None` when nothing matches.

Every node that yields no id *and* no text becomes a `Diagnostic` and is
skipped. Facebook's markup is obfuscated and changes; the point of storing
the raw permalink snapshot is that `/snapshots/{id}/reparse` can re-run an
improved parser over old bytes.

## Verification

`tests/fixtures/facebook_post_comments.html` — a hand-written permalink
page with five top-level comments, one nested reply, one comment with no
id (exercising the synthetic path), one with an expanded long body.
`tests/test_comment_parser.py` asserts ranks, reply exclusion, id
stability across two parses, and chrome-word filtering.
