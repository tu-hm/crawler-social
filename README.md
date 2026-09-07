# crawler-social

> **The first version is intentionally small:** one public Facebook Page, one SQLite
> database, five parsed fields, and two CLI commands. (One thing has since moved on:
> captures are parsed for their text and their markup discarded — see "What a crawl
> keeps" below.) The larger documents below are reference material for
> later expansion, not prerequisites for starting implementation. The active v1 supports
> both macOS and desktop Linux.

**Current status: planning only; no application code has been written.** The detailed
documents below capture the larger, originally macOS-oriented multi-source design frozen on
**2026-09-01**. Keep them as reference, but implement the smaller cross-platform v1 first
and only bring decisions forward when the working product actually needs them.

---

## Active v1

Build one public Facebook Page crawler. Do not apply for other platform credentials,
design private storage, or install a scheduler for this version.

### Getting a session

Facebook serves a login wall in place of Page content, at the Page's own URL. Sign in
once, by hand, before the first crawl — the crawler never types credentials:

```console
uv run crawler login     # a window opens; sign in yourself, 2FA included
uv run crawler session   # "session looks live (ok)"
uv run crawler crawl "https://www.facebook.com/<page>" --limit 5
```

If signing in that way gets challenged, attach to a Chrome you started yourself instead
— a browser automation did not launch is not flagged as automated:

```console
make chrome-cdp          # your Chrome, with --remote-debugging-port=9222
# log in in that window, leave it open, then:
CRAWLER_ATTACH_MODE=cdp uv run crawler crawl "https://www.facebook.com/<page>"
```

A crawl that meets a login wall, checkpoint, or rate limit records the offending snapshot
as evidence, leaves the watermark untouched, and exits `3` with the remedy.

## What a crawl keeps

A crawl stores **text, not pages**. Page markup is never written anywhere: a
capture is hashed and measured on the way past, the parser reads it in memory,
and what lands in the database is the text — the posts, and their top comments
when you ask for them — plus one `snapshots` row per capture recording run,
page, capture time, byte size, and SHA-256. One Facebook page load is several
megabytes; fifty of them grew the database past 300 MB with nothing in it you
could search.

The checksum is over the captured bytes, so a re-capture of an unchanged page
is still recognised and skipped, and a login wall still lands as evidence on
the run. What is gone is any way to re-read a capture after the fact: a parser
fix applies to the next crawl, not to old ones.

Databases written when markup was still stored migrate themselves on the next
open — the `html` column is dropped, each snapshot's size is preserved, and the
file is VACUUMed back down. Every post, comment, run, and snapshot row
survives.

## Web viewer

The crawler stores everything in one SQLite database, and a small web UI lets you browse
it without the terminal:

```console
uv run crawler serve          # http://127.0.0.1:8765
```

- **Loopback and read-only by default.** The server binds `127.0.0.1`, every request
  opens its own read-only SQLite connection, and no route creates or migrates anything.
  The one deliberate exception is the "Crawl now" button on `/crawl`, which spawns the
  same `crawler crawl` subprocess you would otherwise run by hand.
- **Non-loopback binds are refused** unless you pass `--allow-remote` *and* set
  `CRAWLER_SERVE_TOKEN` (at least 32 characters). With a token configured, every request
  must present it — once, as `?token=…` or `Authorization: Bearer`, after which an
  HttpOnly `SameSite=Strict` cookie keeps you signed in; `/healthz` stays open for
  supervisor probes. A remote bind means your crawl data leaves the machine over plain
  HTTP; an SSH tunnel to a loopback server is the safer way to browse it from elsewhere.
- **No captured markup is ever served.** The viewer has no route that renders Facebook
  HTML, because none is stored; the app's own pages carry a strict CSP with no inline
  script or style, stored post and comment text is escaped, and error pages expose
  nothing but a request id.
- **Server-rendered, with two vendored libraries and no build step.**
  Jinja renders every page complete; htmx then re-requests the *same* route and swaps
  one region out of the full response, so filtering `/posts` and following `/crawl`
  output no longer reload the page. Alpine (the CSP-friendly build, because the CSP
  carries no `unsafe-eval`) handles the small client state. Both are plain files under
  `static/vendor/`, committed and checksummed — there is no npm, no bundler and no
  `node_modules`, so a clone plus `uv sync` gives you a working viewer offline. Use
  `make vendor` to re-fetch them and `make vendor-verify` to check them.
- **Everything still works with JavaScript disabled.** Every `hx-get` sits on a link
  or form that already worked, which `tests/test_htmx_contract.py` asserts rather than
  assumes.

The HTML pages and the JSON API under `/api` share one parameter vocabulary (`q`,
`page_url`, `since`, `until`, `order`, `limit`, `offset`), so a UI URL differs from an
API URL only by its path, and `/api/export/posts.csv` streams the current filter's full
result set.

### A short tour

- **Overview (`/`)** — totals, a health panel (last run status and age, posts added in
  the last 24 hours and 7 days, database size), a posts-per-day chart for the last 30
  days, and the newest posts.
- **Posts (`/posts`)** — search, filter by page and date range, sort, page size, and a
  CSV export of the current filter. Each row links to the post's detail page.
- **Post detail (`/posts/<id>`)** — all stored fields, previous/next navigation in the
  current sort order, and the snapshots in which that post was seen.
- **Snapshots (`/snapshots`)** — every captured page with its run, size, and checksum.
  A snapshot is a record that a page was fetched, not a copy of it, so its detail page
  links to the posts parsed from that page rather than to the markup.
- **Runs and state (`/runs`, `/state`)** — each crawl run's status, duration, snapshots,
  and *approximate* yield (posts carry no run foreign key, and the UI says so), plus the
  watermark table that decides where the next crawl stops.
- **Crawl (`/crawl`)** — the "Crawl now" button, live output, and a stop control. It
  works only on a desktop session with a visible browser, and refuses a second crawl
  while one is running. The output panel polls itself every two seconds and stops on
  its own when the crawl ends, because the idle panel it swaps back in carries no
  trigger.

## Long posts and comments

Facebook truncates a long post body behind a **See more** button, and the hidden tail is
genuinely absent from the DOM until that button is clicked — no amount of parsing
recovers it afterwards. So the crawler clicks it at capture time, before it reads the
page. This is on by default:

```console
uv run crawler crawl <page-url>                  # bodies expanded
uv run crawler crawl <page-url> --no-expand      # capture what is on screen
```

Only elements with the button role are clicked, each click is budgeted and re-queried,
and if a click ever navigates away the crawler goes back and stops expanding — an
expansion click must never turn into a page visit you did not ask for.

**Comments are off by default**, because each post you ask for costs one extra page
visit: they are read from the post's own permalink page, not from the feed, which is
virtualized and does not order comments by relevance.

```console
uv run crawler crawl <page-url> --comments 10                       # top 10 per post
uv run crawler crawl <page-url> --comments 10 --comments-max-posts 5
uv run crawler comments --post-id <id> --limit 20                   # read them back
```

- **"Top N" means the first N in Facebook's own default order.** The crawler does not
  re-rank; `rank_index` records the position a comment held when it was captured.
  Nested replies are excluded — only top-level comments are stored.
- **Comments never cost you posts.** The pass runs *after* the post transaction commits,
  so a permalink that will not load, or a comment thread that fails to expand, becomes a
  diagnostic on a run that still reports `completed`. A wall (login, checkpoint, rate
  limit) is the one exception: it stops the run, and the posts stay committed.
- **Defaults live in `.env`** as `CRAWLER_TOP_COMMENTS`, `CRAWLER_COMMENTS_MAX_POSTS`,
  and `CRAWLER_EXPAND_TEXT`; the command-line flags override them per run.

The viewer shows comments on a post's detail page and serves them at
`GET /api/posts/<id>/comments`. Adding these was an additive migration: `posts` gained a
`post_url` column and a `comments` table appeared, both applied in place on the next
crawl. The viewer opens the database read-only and so cannot migrate anything — it
detects what the file actually has, and a database written before v3 still browses
normally.

## Legacy multi-source design

A personal ingestion tool that pulls one person's social and messaging activity into SQLite
and **keeps the original transport bytes**, so that when Facebook rotates its DOM in month
seven you fix the parser and re-run it over six months of stored HTML — no re-crawl, no lost
history. One core, several connectors, one store, three run modes (one-time, `--limit N`,
a twice-daily routine under launchd).

Five sources: **Facebook** (headful Chrome on your own session), **Telegram** (MTProto user
API), **Reddit** (REST over OAuth, if credentials can be obtained at all), **X** (your own
archive ZIP, plus a paid REST connector that ships switched off), and **Zalo** (deferred,
with the evidence for why written down).

**Two ideas do most of the work, and everything else follows from them.**

**1. A connector is a capability contract, not a browser.** Browser automation is the right
transport for exactly one of the five sources. Reddit has an official API; Telegram has a
first-class user-account protocol that makes scraping strictly inferior; X is a pricing
question wearing a technical costume; Zalo has no legitimate personal-account path at all.
So one object crosses the connector boundary — an `Envelope` of verbatim bytes plus a cursor
proposal plus a coverage claim — and how a connector gets its bytes is its own private
business. Any design that assumes "crawler == browser" is wrong.

**2. Conversations contain other people.** Pages and subreddits are broadcast. DMs and
private group chats hold the personal data of people who never agreed to be in anyone's
database. That is treated as an engineering requirement with structural consequences —
privacy is a property of the *container*, the encryption boundary is a **file path** rather
than a `WHERE` clause, conversation *envelopes* go to the encrypted file too, enrolment is
forward-only, and there is deliberately no bulk-enrol command anywhere in the CLI.

---

## Legacy reference documents

| # | Document | What it settles | Lines |
|---|---|---|---|
| 1 | **README.md** (this file) | What the repo is, the reading order, status, next actions | 130 |
| 2 | [docs/DATA-MODEL.md](./docs/DATA-MODEL.md) | **The frozen DDL — the only copy in the repo** — both database files, threading, metrics, raw storage, FTS5 tuned for Vietnamese, the canonical queries and their real query plans | 2,568 |
| 3 | [docs/GOVERNANCE.md](./docs/GOVERNANCE.md) | Privacy classes, the two-file split, encryption at rest, retention, redaction, upstream deletes, export gating, Vietnamese law | 921 |
| 4 | [docs/DECISIONS.md](./docs/DECISIONS.md) | 63 ADRs: the full argument behind every frozen decision, including what was rejected and what would have to change to reopen it | 1,753 |
| 5 | [docs/sources/](./docs/sources/) | One document per connector: [facebook](./docs/sources/facebook.md) · [telegram](./docs/sources/telegram.md) · [reddit](./docs/sources/reddit.md) · [x](./docs/sources/x.md) · [zalo](./docs/sources/zalo.md) | 3,824 |

Do not read these documents before building v1. When a verified v1 is ready to expand, read
DATA-MODEL first. Read GOVERNANCE before adding private data. DECISIONS and the source
documents remain references for the feature being added.

**Two boundaries, so you never read the same thing twice.**

- **The DDL appears in exactly one place**, DATA-MODEL §3. Every other document names tables
  and columns and never re-prints their definitions. A second copy of a schema is a copy that
  rots — and it did, in the draft this set replaced.
- **The CLI is defined in exactly one place**, `src/crawler_social/cli.py`. Every other
  document names commands and flags and never re-prints their definitions.

---

## Legacy design status

**Planning complete. No code written. Nothing has been run against any platform.**

- Every rate limit, price, library version, endpoint and library-maintenance status was
  researched against primary sources where one could be reached. **Nothing was fetched from
  facebook.com, x.com, reddit.com, telegram.org or zalo.me** — that was a hard rule for the
  research, and it is why some claims are labelled second-hand rather than verified.
- Confidence labels are uniform across all five documents: `[verified]` = read from a
  primary source · `[likely]` = multiple consistent secondary sources, no primary ·
  `[unverified]` = must be confirmed before it is relied on. Each document carries its own
  list of unverified claims, with how to check one, roughly how long that takes, and what
  it blocks.
- **The frozen DDL was executed**, not just written. It runs clean on SQLite 3.51.0 and
  produces 30 tables, 41 indexes, 5 triggers and 1 view — 77 rows in `sqlite_master`. Both
  `ack_third_party` triggers abort, the `media.kind` and `usage_counters.metric` constraints
  reject what they are supposed to reject, the `local_day` generated column works, and FTS5
  with `remove_diacritics 2` matches `chao` against `chào`. Query plans in DATA-MODEL §11 are
  real `EXPLAIN QUERY PLAN` output.
- Three defects were found that way and are written up in DATA-MODEL §13, including one
  `CHECK` constraint SQLite rejects outright.

---

## Legacy next actions — deferred until after v1

**Four things on day one, before any code.** Each has a multi-day latency you do not control,
each is free, and each changes the plan if it comes back wrong.

1. **R0 — apply for a Reddit OAuth app.** Self-service registration ended in November 2025;
   every new client goes through manual review with a stated ~7-day target. This is the
   highest-variance assumption in the whole plan. Two dated checkboxes ride along with it:
   **2026-09-30** to register an existing app, **2026-12-31** for the Migration Program
   decision.
2. **T0 — get a Telegram `api_id`.** There is no review queue, but the self-service form is
   reported to fail opaquely for some accounts, and the entire "Telegram is connector #2"
   decision rests on that credential existing. If it fails, the slot changes hands.
3. **X0 — request your X data archive.** ~24h turnaround, and **the download link expires 7
   days after generation.** Then `unzip -l` it and paste the manifest into
   [docs/sources/x.md](./docs/sources/x.md) §2.3 — one command closes that connector's
   largest unknown.
4. **P0 — start the Facebook profile clock.** Log in once, then use that profile by hand like
   an ordinary browser. Session age is an asset and the thresholds are enforced, not advisory:
   **≥ 3 days** before the first Page crawl, **≥ 14 days** before the first group.

**Then answer the six remaining open questions.** Four
earlier ones are already frozen as defaults with their costs stated. Of the six that remain,
two are genuinely urgent:

- **What retention do you want on other people's messages?** There is deliberately no
  default. Enrolling your first conversation target will make you type a number, and
  `forever` is a legal answer. An earlier draft defaulted to 180 days with an unattended hard
  delete, which would have quietly destroyed the archive the tool exists to build.
- **Does anything downstream of this corpus involve an LLM?** Telegram's API terms prohibit
  using or aggregating Telegram data to train or fine-tune models. That is an architectural
  boundary rather than a footnote, and it must be decided before the Telegram connector is
  built rather than discovered after the archive exists.

**Check the unverified claims before you commit to any number.**
Nothing in this set is allowed to become a stated fact somewhere else without being checked
first, and a plan built on a hallucinated rate limit is worse than one that says *confirm
this*.

---

*No implementation files exist in this repository, deliberately. Code inside fenced blocks —
interface signatures, dataclass shapes, SQL DDL, config examples, CLI transcripts — is
illustrative and is there to make the design concrete, not to be executed.*
