# Step 07 — Build the runs and health dashboard

## Outcome

`/runs` answers "did the last crawl work, and what did it find?" without opening a
terminal.

## Depends on

- [Step 06](./06-post-detail-and-snapshots.md) is complete.

## Work, in order

1. Add `templates/runs.html` and the route `GET /runs`: a paged table of runs, newest
   first, showing id, started, finished, duration, status, snapshot count, and the error
   text when present.
2. Style the four `runs.status` values distinctly — `running`, `completed`, `failed`,
   `interrupted`. These are the only legal values; the schema's `CHECK` constraint
   guarantees it, so an unknown value is a bug and should render as such rather than
   silently as "completed".
3. Flag a stale `running` row: a run whose `started_at` is more than an hour old and has
   no `finished_at` almost certainly died without updating its row. Show it as "running
   (stale)" with an explanation, and do not modify the row — the server does not write.
4. Add `templates/run.html` and the route `GET /runs/{run_id}`:
   - the run's fields and its full error text;
   - its snapshots, linked to the snapshot viewer;
   - the posts whose `first_seen` falls between the run's start and finish, as the run's
     approximate yield. Label it "approximate" in the UI, because `posts` has no run
     foreign key — do not imply a precision the schema does not provide.
5. Add `templates/state.html` and the route `GET /state`: the watermark table — page URL,
   last post id (linked to the post when it exists), last post time, updated at. This is
   what determines where the next crawl stops, so it belongs in front of the user.
6. Add a health panel to the home page:
   - last run status and how long ago;
   - posts added in the last 24 hours and 7 days, from `first_seen`;
   - database file size and total snapshot bytes;
   - a warning when the newest post is older than 7 days, since that usually means the
     parser broke rather than that the page went quiet.
7. Add a small posts-per-day chart for the last 30 days, drawn as inline SVG generated in
   the template from a `GROUP BY date(first_seen)` query. No chart library, no CDN.
8. Compute every duration and relative age from the stored UTC ISO-8601 strings using a
   single shared helper, so no page invents its own formatting.

## Required tests

- `GET /runs` lists seeded runs newest first with their statuses.
- Each of the four statuses renders with its own distinct marker.
- A run started two hours ago with no `finished_at` is marked stale; one started a minute
  ago is not.
- `GET /runs/{id}` shows the run's snapshots and its approximate posts, and the response
  contains the word "approximate".
- `GET /runs/{unknown}` returns a 404 page.
- `GET /state` lists watermark rows, and a `last_post_id` that exists links to its post.
- The home page health panel reports the correct 24-hour and 7-day counts against seeded
  `first_seen` values.
- The staleness warning appears when the newest post is 8 days old and not when it is 2
  days old.
- The chart renders valid SVG with one bar per day that has posts, and renders an empty
  state rather than broken markup when there are no posts.
- No route in this step opens a writable connection: assert against a database file made
  read-only on disk.

## Verification

```console
uv run pytest tests/test_runs_view.py
uv run crawler serve
```

Open `/runs` after a real crawl. The newest run matches the summary line the CLI printed,
and `/state` matches `sqlite3 data/social.db "SELECT * FROM state"`.
