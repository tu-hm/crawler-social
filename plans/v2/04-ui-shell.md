# Step 04 — Build the UI shell

## Outcome

Opening `http://127.0.0.1:8765/` shows a real page: a header, navigation, a summary of
what is stored, and a consistent layout that later pages extend.

## Depends on

- [Step 03](./03-json-api.md) is complete.

## Work, in order

1. Create the directory layout inside the server package so the files ship with the
   installed module:
   ```
   src/crawler_social/server/
     templates/
       base.html
       partials/pagination.html
       partials/empty_state.html
       index.html
     static/
       app.css
       app.js
   ```
2. Configure Jinja2 with an explicit loader path derived from
   `Path(__file__).parent / "templates"` — never a path relative to the working directory,
   because the server must run from anywhere.
3. Mount `StaticFiles` at `/static` from `Path(__file__).parent / "static"`.
4. Turn on Jinja2 autoescaping. This is not optional: post text and author names are
   attacker-controlled third-party content. No template may use `| safe` on any value that
   came out of the database.
5. Write `base.html` with:
   - a `<header>` carrying the app name, the database path, and the last run's status;
   - navigation to Posts, Runs, Snapshots;
   - a `{% block content %}`;
   - a footer showing the version and the row counts.
6. Write `app.css` by hand — roughly 150 lines. Requirements:
   - a readable measure for post text (max width ~70 characters);
   - a dense, scannable table style with sticky headers;
   - `prefers-color-scheme` support for light and dark;
   - no external font, CDN, or web request of any kind. The server must work offline.
7. Add `GET /` rendering `index.html` from `queries.summary()`: total posts, total
   snapshots, total runs, database size on disk, newest post time, last run status, and a
   per-page breakdown from `list_pages`.
8. Write the shared partials:
   - `pagination.html` renders previous/next links preserving every current query
     parameter;
   - `empty_state.html` renders a short message and the command that would produce data
     (`uv run crawler crawl <url>`).
9. Add a `GET /` response header of `Cache-Control: no-store` for HTML pages so a stale
   page never masks new crawl results.
10. Return a rendered 404 page for unknown HTML routes, and keep the JSON 404 for `/api`
    routes. Decide by path prefix, not by the `Accept` header.

## Required tests

- `GET /` returns 200 and the HTML contains the seeded post count.
- `GET /` on a missing database returns 200 with the empty state and the suggested
  command, not a 503 — the home page is the one place that must always render.
- `GET /static/app.css` returns 200 with `content-type: text/css`.
- A post whose text is `<script>alert(1)</script>` renders escaped: the response contains
  `&lt;script&gt;` and does not contain a literal `<script>alert`.
- An unknown HTML path returns a 404 page; an unknown `/api` path returns JSON.
- The rendered HTML references no `http://` or `https://` external URL.

## Verification

```console
uv run pytest tests/test_ui_shell.py
uv run crawler serve
```

Open `http://127.0.0.1:8765/`. The summary numbers match `uv run crawler posts`, the page
is readable in both light and dark mode, and the browser's network tab shows no external
requests.
