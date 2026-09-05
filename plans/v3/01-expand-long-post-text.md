# 01 — Expand long post text ("See more")

## Goal

The HTML committed to `snapshots` holds the *whole* post body, so
`parser._extract_text()` stores the whole body.

## Changes — `src/crawler_social/facebook.py`

### Label patterns

```python
SEE_MORE_PATTERN      # anchored: "See more" / "Xem thêm" / "Voir plus" / ...
MORE_COMMENTS_PATTERN # "View N more comments" / "Xem thêm bình luận" (step 03)
MORE_REPLIES_PATTERN  # "View N replies" / "Xem N phản hồi"          (step 03)
```

`SEE_MORE_PATTERN` is anchored (`^...$`) against the *accessible name*, so
"Xem thêm bình luận" ("view more comments") cannot match it.

### `_click_repeatedly(page, pattern, *, max_clicks, rng, ...) -> int`

One shared, defensive click loop:

- re-queries after every click, because the click mutates the DOM;
- scans at most `_SCAN_LIMIT` matches for the first visible one;
- gives every Playwright call an explicit short timeout, so a stuck
  element costs a second, not the default 30;
- swallows per-element failures and moves on;
- stops when nothing visible is left or the click budget runs out;
- returns the number of clicks that landed.

### `expand_post_text(page, options, rng) -> int`

Wraps `_click_repeatedly` with `SEE_MORE_PATTERN`, then checks that the
click did not navigate: if `page.url` left the URL we were capturing, go
back and stop expanding (D2).

### `CaptureOptions` gains

```python
expand_text: bool = True
max_expand_clicks: int = 12
```

### `capture_snapshots()`

Call `expand_post_text()` at the top of each loop iteration, *before*
`inspect()`. Guarded by `options.expand_text`.

## Changes — `src/crawler_social/parser.py`

`Post` gains `url: Optional[str]` — the post's permalink, extracted from
the article's own links, needed by step 03 and useful in the viewer.

`_extract_post_url(article, page_url)`:

- accepts `href`s matching `/posts/`, `/permalink.php`, `story_fbid=`,
  `/videos/`, `/photos/`, `/reel/`, `/groups/<id>/posts/`;
- resolves relative hrefs against `https://www.facebook.com`;
- strips Facebook's tracking parameters (`__cft__[0]`, `__tn__`, `_rdr`,
  anything starting with `__`), keeping `story_fbid`/`id` which are load
  bearing for `permalink.php`;
- returns `None` when nothing matches — the caller falls back to
  `facebook.post_permalink(page_url, post_id)`.

## Verification

`tests/test_parser.py` gains cases for permalink extraction and tracking
parameter stripping. Expansion itself is browser behaviour: it is covered
by a fake page object in `tests/test_expand.py` that records clicks, not by
a real browser.
