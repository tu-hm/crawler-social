# crawler-social — simple v1 plan

## The goal

Build the smallest useful version of crawler-social:

> Given one public Facebook Page URL, save its recent posts and the original HTML to a
> local SQLite database so the posts can be searched and re-parsed later.

Version 1 is successful when this command works twice without creating duplicate posts:

```console
uv run crawler crawl "https://www.facebook.com/<page>" --limit 20
```

This plan intentionally optimizes for getting a real, reliable result before building a
multi-source platform.

Detailed, executable checklists live in [plans/v1/](./plans/v1/README.md). They support
both macOS and desktop Linux. Browser capture needs a graphical desktop session; database
work and parser tests do not.

## Platform support

| Concern | macOS | Linux |
|---|---|---|
| Browser | Chrome or Chromium | Chrome or Chromium |
| GUI session | Logged-in desktop | X11 or Wayland desktop |
| Profile data | `~/Library/Application Support/crawler-social/` | `${XDG_STATE_HOME:-$HOME/.local/state}/crawler-social/` |
| Paths in code | `platformdirs` + `pathlib` | `platformdirs` + `pathlib` |
| Scheduling after v1 | LaunchAgent | `systemd --user` timer |

There are no Keychain, FileVault, launchd, or systemd requirements in v1 because it stores
only public data and runs manually. Windows and headless Linux servers are not supported by
this first version.

## What v1 includes

- One source: a public Facebook Page.
- One local Chrome profile, logged in manually when necessary.
- One SQLite file: `data/social.db`.
- Raw HTML saved before parsing.
- Five post fields: Facebook ID, page URL, text, author, and published time.
- Incremental runs that do not duplicate already stored posts.
- Offline parser tests using saved HTML fixtures.
- Two commands: `crawl` and `posts`; `posts` can filter by a text fragment.

## What waits until later

- Facebook Groups, comments, reactions, media downloads, and DMs.
- Telegram, Reddit, X, and Zalo.
- The generic connector protocol and capability registry.
- The encrypted private database and conversation-retention system.
- Coverage claims, gap accounting, field statistics, and canary scoring.
- Keychain integration, SQLCipher, exports, redaction workflows, and identity linking.
- Automatic scheduling, retries across machines, and a complete administration CLI.

These features are not rejected. They become candidates only after the simple v1 has run
reliably for seven manual crawl sessions.

## Simple design

Keep the program as a straight pipeline:

```text
Facebook Page
    -> capture visible HTML
    -> save the raw snapshot
    -> parse posts from that saved snapshot
    -> upsert posts into SQLite
    -> print a short run summary
```

Use four tables:

1. `runs` — when a crawl started, ended, and whether it succeeded.
2. `snapshots` — captured HTML, its hash, URL, and capture time.
3. `posts` — the five parsed fields plus first-seen and last-seen times.
4. `state` — the last successful post ID/time for each Page URL.

The important durability rule remains: commit a snapshot before parsing it, then update
`state` only after its posts have been committed. This uses two short transactions so a
parser failure cannot discard the original HTML.

Suggested code layout:

```text
src/crawler_social/
  cli.py          # crawl and posts commands
  db.py           # schema, connections, and upserts
  facebook.py     # browser capture only
  parser.py       # pure HTML-to-post parsing
tests/
  fixtures/facebook/
  test_db.py
  test_parser.py
```

Do not add an abstraction until two real implementations need it.

## Build it step by step

### [Step 0 — Verify the environment](./plans/v1/00-environment.md)

Choose one public Page, verify Python/`uv`/SQLite, and confirm Chrome or Chromium can run in
a desktop GUI session. Use the platform-specific profile directory documented in the step.

Done when the browser, GUI, runtime, Page URL, and private profile path are known.

### [Step 1 — Create the runnable shell](./plans/v1/01-scaffold.md)

Timebox: half a day.

1. Initialize a Python project with `uv`.
2. Add only the dependencies needed for the first slice: browser automation, HTML
   parsing, a small CLI library, `platformdirs`, and pytest.
3. Add the two command stubs:
   - `crawler crawl PAGE_URL --limit N`
   - `crawler posts --limit N [--contains TEXT]`
4. Ignore `data/`, browser profiles, database files, and local environment files.

Done when:

```console
uv run crawler --help
```

shows both commands and the test suite starts successfully.

### [Step 2 — Add the small database](./plans/v1/02-storage.md)

Timebox: half a day.

1. Create the four tables listed above in one schema file.
2. Add a `connect()` function that enables foreign keys, WAL mode, and a busy timeout.
3. Add `save_snapshot()`, `upsert_post()`, `get_state()`, and `set_state()`.
4. Commit raw snapshot storage in its own transaction.
5. Commit post upserts and the state update together in a second transaction.
6. Test that running the same insert twice produces one post and one raw body hash.

Done when all database tests pass against a temporary SQLite file.

### [Step 3 — Capture real HTML](./plans/v1/03-facebook-capture.md)

Timebox: one day. Stop and reassess if it takes longer.

1. Create a dedicated Chrome or Chromium profile outside the repository using the macOS
   or Linux path from Step 0.
2. Log in manually; never automate credentials or CAPTCHA handling.
3. Open one public Page URL.
4. Scroll until 20 posts have been seen or a fixed time limit is reached.
5. Save HTML after each scroll because Facebook may remove off-screen nodes.
6. Store every captured snapshot before attempting to parse it.
7. Copy a small, non-private sample into `tests/fixtures/facebook/`.

Done when the database contains raw HTML from one real Page and the same capture can be
replayed without opening Chrome.

### [Step 4 — Parse and store five fields](./plans/v1/04-parser.md)

Timebox: one day.

1. Implement a pure function: `parse(html, captured_at) -> list[Post]`.
2. Extract only Facebook ID, Page URL, text, author, and published time.
3. Return `None` for a missing optional field; do not guess.
4. Test the parser entirely from fixtures with no browser, database, secrets, or clock.
5. Feed the parsed posts into `upsert_post()`.
6. Make `crawler posts` print the newest saved posts and support a simple
   `--contains TEXT` filter.

Done when one command captures and stores at least 10 real posts, and offline fixture tests
produce the same post IDs.

### [Step 5 — Make repeat runs safe](./plans/v1/05-safe-repeat-runs.md)

Timebox: one day.

1. Read the Page's last successful state at the beginning of a run.
2. Stop after several consecutive already-seen posts.
3. Update state only after snapshots and posts commit successfully.
4. Make `--limit N` a hard upper bound.
5. Record browser and parser errors in `runs`; leave the previous state unchanged.
6. Run the same Page twice and verify there are no duplicate posts.

Done when an interrupted run can be retried and a second successful run only adds new
posts.

### [Step 6 — Prove it before expanding it](./plans/v1/06-reliability-gate.md)

Run the crawler manually seven times over at least three days. For every run, check:

- Did it stop within the configured limit/time?
- Were raw snapshots written?
- Were new posts stored without duplicates?
- Did missing fields stay missing instead of becoming incorrect values?
- Could all saved snapshots be parsed offline?

Fix failures inside the simple design. After seven reliable runs, choose exactly one next
feature based on actual need:

1. automatic scheduling;
2. comments for the same Facebook Page;
3. a second public source and the connector abstraction it proves necessary; or
4. private/conversation data, which first requires the governance and encrypted-store
   work in the detailed design.

## Rules that keep v1 small

1. One active milestone at a time.
2. Each milestone must end in a command or test you can run.
3. No framework for a future connector before a second connector exists.
4. No private data in v1.
5. No scheduler until manual runs are reliable.
6. If a task does not help the first Page crawl or its safe replay, defer it.

## Today’s checklist

- [ ] Choose one public Facebook Page URL for the test.
- [ ] Confirm Python, `uv`, SQLite, and Chrome or Chromium work locally.
- [ ] Confirm a macOS, X11, or Wayland graphical desktop session is available.
- [ ] Create the project shell and the two CLI commands.
- [ ] Implement the four-table schema and its duplicate-insert test.
- [ ] Create the dedicated browser profile at the documented macOS or Linux location and
      log in manually.

Stop after this checklist. The next working session begins with Step 3, not with another
architecture document.
