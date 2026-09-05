# 00 — Scope and decisions

## The two problems

**Truncated text.** `_extract_text()` reads whatever is in the stored HTML.
Facebook renders a long post as a few hundred characters plus a `See more`
button (`div[role="button"]`), and the rest of the text is *not in the DOM*
until that button is clicked. No parser change can recover it. So this is a
capture-time fix, not a parse-time fix.

**No comments at all.** Nothing in the schema or the parser knows what a
comment is. On a Page feed, comments are mostly not rendered; the reliable
place to read them is the post's own permalink page.

## Decisions

### D1 — Expand before `page.content()`, not after

`facebook.inspect()` is the single point where the live DOM becomes stored
bytes. Expansion happens immediately before it, once per scroll iteration.
Consequence: the bytes in `snapshots.html` are complete, so a re-parse
months later produces the same full text. Nothing downstream changes.

### D2 — Only accessible-role buttons are clicked

Expansion uses `page.get_by_role("button", name=<anchored regex>)`. That
excludes `<a href>` (role `link`), so a click can expand text but cannot
navigate away. Every click is individually guarded, capped by a click
budget, and the page URL is checked afterwards.

### D3 — Comments come from the permalink page, not the feed

Alternatives considered:

- *Expand comments inline in the feed.* Fewer navigations, but the feed is
  virtualized: the article can be recycled out of the DOM mid-expansion,
  and "most relevant" ordering is not applied there.
- *Permalink page per post.* One navigation per post, but the comment tree
  is really rendered, default ordering **is** "most relevant" (= "top"),
  and each post gets its own snapshot row, which makes the pass
  re-parseable and debuggable.

We take the permalink page, capped hard by `--comments-max-posts`.

### D4 — "Top N" means "the first N in Facebook's default order"

We do not re-sort and we do not click the ordering menu. Facebook's default
for a post is *Most relevant*; `comments.rank_index` records the document
order we saw, and that ordering is what "top" means here. Replies nested
under a comment are excluded — only top-level comments are ranked.

### D5 — The comment pass runs in its own browser session

`capture_snapshots()` owns its session from `goto` to teardown, and three
test modules stub it by exact signature. Threading a live `page` out of it
would break that contract for a modest gain. `capture_comments()` opens its
own session after the feed pass has committed. In `cdp` mode — the mode the
README recommends — that is a new tab in the user's own Chrome and costs
nothing; in `launch` mode it is a second Chrome start.

### D6 — Comments never fail a crawl that already stored posts

Posts commit first, in their own transaction. A per-post failure in the
comment pass (permalink 404, timeout, unparseable) becomes a diagnostic and
the pass moves on. Only a *wall* (login/checkpoint/rate limit) stops the
pass, and it is reported the same way a blocked feed capture is.

### D7 — Off by default

`--comments 0` is the default. The extra navigations are the most
bot-visible thing this project does; nobody gets them by accident.

## Non-goals

- Reply threads under comments.
- Reaction breakdowns, comment attachments, images, or authors' profile ids.
- Backfilling comments for posts stored by earlier runs (a future
  `crawler comments --backfill` could reuse `capture_comments()` as-is).
- Any change to the walls/verdict logic in `wall.py`.
