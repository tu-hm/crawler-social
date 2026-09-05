# Step 06 — Build post detail and the snapshot viewer

## Outcome

You can open one post and read its full text and every stored field, and you can inspect
the raw captured HTML that produced it — safely.

## Depends on

- [Step 05](./05-posts-viewer.md) is complete.

## The safety rule for this step

`snapshots.html` is unmodified markup captured from Facebook. It contains scripts,
trackers, and remote resource references. Rendering it inside the app's own page would
hand a third party the app's origin. Every decision below follows from that.

## Work, in order

1. Add `templates/post.html` and the route `GET /posts/{post_id}`:
   - all seven stored fields, labelled, with `(none)` for nulls;
   - the full text in a `<pre class="post-text">` so line breaks survive, autoescaped;
   - `first_seen` and `last_seen` with both the UTC string and the relative age;
   - a "copy text" button implemented in `app.js` against the clipboard API;
   - links to the snapshots for the same `page_url` captured nearest in time;
   - previous/next links to adjacent posts in the current sort order;
   - a 404 page for an unknown id.
2. Add `templates/snapshots.html` and the route `GET /snapshots`: a paged table of
   snapshot metadata — id, run, page, captured time, size, and the first 12 characters of
   the SHA-256 — filterable by `run_id` and `page_url`. Never load a blob here.
3. Add `GET /snapshots/{id}` rendering a metadata page with a sandboxed preview and links
   to the raw and source views.
4. Implement the sandboxed preview as an `<iframe>` whose `src` is
   `/api/snapshots/{id}/raw` and whose `sandbox` attribute is **empty** — `sandbox=""`
   with no `allow-scripts` and no `allow-same-origin`. Add `referrerpolicy="no-referrer"`
   and `loading="lazy"`.
5. Serve `/api/snapshots/{id}/raw` with headers chosen to neutralize the content:
   - `Content-Type: text/html; charset=utf-8`
   - `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'` — blocks
     scripts, images, frames, and every network fetch the captured page would attempt
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: SAMEORIGIN`
   - `Referrer-Policy: no-referrer`
   - `Cache-Control: no-store`
   Return the bytes exactly as stored; do not re-encode or rewrite them.
6. Add `GET /snapshots/{id}/source` rendering the HTML as escaped text in a `<pre>`, with
   line numbers, for reading the markup rather than viewing it. Truncate the display at
   2 MB with a notice and a download link, so a huge snapshot cannot hang the browser.
7. Add `GET /api/snapshots/{id}/download` returning the bytes with
   `Content-Type: application/octet-stream` and a `Content-Disposition: attachment;
   filename="snapshot-{id}-{sha12}.html"`.
8. Add a "reparse this snapshot" **read-only** view: run `parser.parse()` over the stored
   bytes in memory and show the posts and diagnostics it produces, without writing
   anything. This is the debugging tool that makes parser breakage visible, and it is why
   raw HTML is stored at all.
9. Guard the blob reads: return 404 for an unknown id, and stream the response rather than
   materializing multiple copies of a multi-megabyte body.

## Required tests

- `GET /posts/{id}` returns 200 with the full text and every field label.
- A post whose text contains `</pre><script>` renders escaped inside the `<pre>`.
- `GET /posts/{unknown}` returns a 404 HTML page.
- `GET /snapshots` lists metadata and no response contains the raw HTML body.
- `GET /snapshots/{id}` contains an `<iframe` with `sandbox=""` and no `allow-scripts`.
- `GET /api/snapshots/{id}/raw` returns the exact stored bytes.
- That response carries the CSP, `nosniff`, and `no-store` headers listed above.
- `GET /api/snapshots/{id}/download` carries an attachment disposition naming the id.
- `GET /snapshots/{id}/source` escapes the markup: the body contains `&lt;html` and no
  literal `<html` from the snapshot.
- The reparse view on the checked-in fixture
  (`tests/fixtures/facebook_page_sample.html`) reports the same posts the parser tests
  expect, and writes nothing — assert the post count in the database is unchanged.
- `GET /api/snapshots/{unknown}/raw` returns 404.

## Verification

```console
uv run pytest tests/test_snapshot_view.py
uv run crawler serve
```

Open a snapshot page. The preview renders the captured layout with no network activity in
the browser's network tab, the source view is readable, and the reparse view lists the
posts and any diagnostics.
