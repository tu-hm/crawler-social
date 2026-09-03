# Step 05 — Make repeated runs safe

## Outcome

Running the same Page twice does not duplicate posts, and interruption never advances state
past committed posts.

## Depends on

- [Step 04](./04-parser.md) is complete.

## Work, in order

1. Read the Page's last successful state before capture begins.
2. Track post IDs seen during the current run.
3. Stop after five consecutive already-stored, non-pinned candidate posts.
4. Enforce `--limit N` as a hard ceiling on newly emitted posts.
5. Update `state` only in the same transaction that commits parsed posts.
6. On browser or parser failure:
   - keep committed snapshots;
   - roll back uncommitted posts and state;
   - finish the run as failed with a short diagnostic;
   - return a non-zero exit code.
7. Handle `SIGINT` and `SIGTERM` by closing the browser and preserving the last completed
   transaction. Use the same Python signal handling on macOS and Linux.
8. Add a maximum wall-clock duration so a changed feed cannot scroll forever.
9. Keep one writer with the portable POSIX file lock already used for the browser profile.

## Required tests

- Two identical runs leave one row per Facebook post ID.
- A second run advances `last_seen`.
- A failure after snapshot commit preserves that snapshot.
- A failure before the post transaction commits preserves the old state.
- `--limit 3` adds at most three new posts.
- A simulated signal closes resources and leaves the database valid.
- A second process receives a clear lock message instead of corrupting the profile or DB.

## Verification

Run the same command twice:

```console
uv run crawler crawl "https://www.facebook.com/<page>" --limit 20
uv run crawler crawl "https://www.facebook.com/<page>" --limit 20
```

The second run adds only genuinely new Facebook post IDs. `PRAGMA integrity_check` remains
`ok`, and the command prints counts for captured snapshots, new posts, existing posts, and
errors.
