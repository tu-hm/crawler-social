# Step 05 — Build the posts data viewer

## Outcome

`/posts` is the tool you actually use: a searchable, filterable, paged table of every
stored post, with a link into each one.

## Depends on

- [Step 04](./04-ui-shell.md) is complete.

## Work, in order

1. Add `templates/posts.html` and the route `GET /posts`.
2. Read the filters from the query string and pass them straight to
   `queries.list_posts`: `q`, `page_url`, `since`, `until`, `order`, `limit`, `offset`.
   The HTML route and the JSON route share one parameter vocabulary, so a UI URL differs
   from an API URL only by its path.
3. Render the table with these columns:
   - published time (UTC, as stored) with the relative age in the title attribute;
   - author;
   - a text excerpt of ~180 characters, single-lined, with the query term highlighted;
   - the page, shown only when more than one page is stored;
   - first seen / last seen, in a secondary style;
   - a link to `/posts/{post_id}`.
4. Implement highlighting server-side by splitting the escaped text around case-insensitive
   matches and wrapping the matches in `<mark>`. Escape first, then wrap — never wrap then
   escape, and never pass raw text through `| safe`.
5. Build the filter bar as a plain `<form method="get">`: a text input for `q`, a select
   for `page_url` from `/api/pages`, two date inputs, and an order select. It must work
   with JavaScript disabled. `app.js` may debounce and submit on typing, as an enhancement
   only.
6. Show, above the table, the exact result count and the active filters, each with a link
   that removes just that filter.
7. Paging: page-size selector of 25 / 50 / 100 / 200, previous/next, and "showing X–Y of
   Z". Preserve all filters across page changes using the shared pagination partial.
8. Empty results get a distinct message from an empty database — "no posts match these
   filters" with a clear-filters link, versus "no posts stored yet" with the crawl command.
9. Add a "Download CSV" link that points at `/api/export/posts.csv` carrying the current
   query string, so the export always matches what is on screen.
10. Make every row keyboard-reachable: the post link is a real `<a href>`, not a JS click
    handler on the row.

## Required tests

- `GET /posts` returns 200 and lists the seeded posts newest first.
- `GET /posts?q=term` shows only matching rows and reports the filtered count.
- The `q` term appears wrapped in `<mark>` in the response.
- A `q` value of `<img src=x onerror=1>` appears escaped and produces no raw tag, and the
  `<mark>` wrapping does not reintroduce unescaped text.
- `GET /posts?page_url=...` narrows to one page.
- `GET /posts?limit=25&offset=25` shows the second window and the pagination links carry
  `q` and `page_url` forward unchanged.
- `GET /posts?q=zzzz` renders the "no matches" state with a clear-filters link, while a
  missing database renders the "no posts stored yet" state.
- The CSV link's query string equals the page's current query string.
- Every rendered post links to a `/posts/{id}` URL that returns 200.

## Verification

```console
uv run pytest tests/test_posts_view.py
uv run crawler serve
```

Open `http://127.0.0.1:8765/posts`. Search for a word you know is in a crawled post, page
through the results, clear the filters, and download the CSV — the row count in the file
matches the count shown on screen.
