# crawler-social — build plan

> **Legacy expansion plan.** Do not implement this document from top to bottom. The active,
> cross-platform v1 is [SIMPLE_PLAN.md](./SIMPLE_PLAN.md), with executable steps in
> [plans/v1/](./plans/v1/README.md). This older design is retained for later multi-source
> work and contains macOS-specific assumptions (Keychain, FileVault, and launchd) that are
> not requirements for v1. Linux equivalents must be designed and verified before any of
> those later features are promoted into the active plan.

A personal ingestion tool that pulls your social and messaging activity into SQLite and
keeps the original payload, so you can re-parse history later without going back to the
platform. One core, several connectors. Facebook is connector #1; Telegram is #2; Reddit
and X follow; Zalo is deferred with its trigger written down.

Status: **planning only — no code written yet.** This document records the legacy expansion
order; it is not the active v1 build order. Everything under `docs/` is reference material
for that later design.

The single most important thing to understand before reading further: **this tool is not
a browser automation project.** Browser automation is the correct transport for exactly
one of the five sources. Reddit is a REST API, Telegram is MTProto, X is REST plus a file
import, Zalo has no viable transport at all. A connector is defined by a capability
contract; how it gets its bytes is its own private business. Any design that assumes
"crawler == browser" is wrong and will be rejected in review.

---

## 0. Document map

| Document | What it settles |
|---|---|
| [./README.md](./README.md) | What this repo is, the reading order, current status, and the next actions |
| **PLAN.md** (this file) | Scope, per-platform risk, the frozen decisions in summary, the milestone plan, first-run, failure modes, the deferred list, the verify list, the open questions |
| [./ARCHITECTURE.md](./ARCHITECTURE.md) | The connector contract (`Envelope`, `Cursor`, `Coverage`, `Budget`, `Cap`), the core/connector seam, registry, **the CLI surface**, scheduling topology, rate limiting, error taxonomy, secrets, testing |
| [./docs/DATA-MODEL.md](./docs/DATA-MODEL.md) | The frozen DDL — **the only copy** — both database files, threading, metrics, raw storage, FTS5, canonical queries and their query plans |
| [./docs/GOVERNANCE.md](./docs/GOVERNANCE.md) | Privacy classes, the two-file split, encryption at rest, retention, redaction, upstream deletes, export gating, Vietnamese law |
| [./docs/DECISIONS.md](./docs/DECISIONS.md) | The full rationale behind each frozen decision, including the options rejected and why |
| [./docs/sources/facebook.md](./docs/sources/facebook.md) | Connector #1 — surface, extraction spike, stealth posture, walls, harvest mechanics, per-field extraction, watermark, canary |
| [./docs/sources/telegram.md](./docs/sources/telegram.md) | Connector #2 — MTProto, Telethon pin, cursors, the broadcast/conversation split |
| [./docs/sources/reddit.md](./docs/sources/reddit.md) | Connector #3 — OAuth, the credential spike, listing cap, Arctic Shift backfill |
| [./docs/sources/x.md](./docs/sources/x.md) | Connector #4 — pay-per-usage economics, the archive ZIP importer, every rejected scraping path |
| [./docs/sources/zalo.md](./docs/sources/zalo.md) | Deferred — the three transports, why each fails, and the trigger that unblocks each |

Read README, then this file, then ARCHITECTURE, then DATA-MODEL. GOVERNANCE before you
write a line of the Telegram connector. The source docs are reference, read when you
build that one.

**Three boundaries worth knowing so you do not read the same thing twice.** §6 below is
the build order — numbered milestones with acceptance checks you can run. §5 below is the
frozen decisions in summary, one line of rationale each; DECISIONS carries the full
argument and the rejected alternatives, and when the two disagree DECISIONS is right and
this one has drifted. And **the DDL appears in exactly one place** — DATA-MODEL §3. This
file names tables and columns; it never re-prints their definitions, because a second copy
of a schema is a copy that rots.

---

## 1. What this is

**A personal archive with a re-parse guarantee.** Three properties, in priority order:

1. **Raw-first.** Every fetch stores the verbatim transport bytes before anything is
   parsed out of them. When Facebook rotates its DOM in month seven, you fix the parser
   and re-run it over six months of stored HTML — no re-crawl, no lost history. This is
   the whole reason the project exists in this shape; every other decision bends to it.

   **The replay window is not infinite, and the number differs by privacy class.** Raw
   payloads for `broadcast` targets are kept forever. For `joined` and `conversation`
   targets the envelope body expires after **90 days** (`envelopes.purge_after`), because
   holding other people's messages in their fattest form forever is the thing this design
   is least willing to do. After that the parsed rows remain and the bytes are gone, so a
   parser fix reaches back 90 days on those targets and to the beginning on public ones.
   That is a real limit and it is stated here rather than discovered during a reparse.
2. **One store, many sources.** A cross-source timeline query, a cross-source text
   search, one backup file. Adding a connector is not a schema migration.
3. **Honest about what it does not have.** A gap is a row in a table, not a silence. A
   partial comment tree says it is partial. A timestamp you inferred from "3h ago" is
   marked as inferred. The failure mode this design works hardest to prevent is *a run
   that looks successful and was not.*

**Run modes:**

| Mode | Command | Cursor behaviour |
|---|---|---|
| One-time | `crawler crawl --source X --target T` | Advances the durable watermark |
| Limit-N | `crawler crawl --source X --target T --limit 20` | **Does not** advance the watermark — a sampled run must never poison an incremental cursor. Recorded as `runs.mode='limit'` |
| Daily routine | `crawler tick` via launchd, 08:05 and 20:35 | Advances by cursor, never by "fetch yesterday" |

---

## 2. Scope

**In scope**

- **Facebook**: Pages (public) and Groups (public, and private ones your account has
  already joined). Runs as *your* logged-in session; it collects what you can already
  see in a browser.
- **Telegram**: channels, groups, and named private conversations, via the official
  MTProto user API. Read-only, enforced by the connector importing no send/forward/join
  method at all.
- **Reddit**: subreddits and comment trees over the official Data API, if credentials
  can be obtained. See §3 — this is not a given.
- **X**: your own data archive ZIP (including your own DMs), and optionally third-party
  timelines over the paid API behind a hard spend cap.
- SQLite storage, raw-first, with full-text search tuned for Vietnamese.
- Three run modes: one-time, `--limit N`, daily routine.

**What the tool keeps, and for how long.** These are the two numbers most likely to
surprise you later, so they are here in Scope rather than only in GOVERNANCE:

| Class | Parsed rows | Raw envelope bodies |
|---|---|---|
| `broadcast` (Pages, subreddits, public channels) | **forever** | **forever** |
| `joined` (closed FB group, private subreddit, large public supergroup) | **730 days**, then hard delete | **90 days** |
| `conversation` (any DM or private chat) | **no default — you must set a number** | **90 days** |

The conversation row is deliberate. An earlier draft defaulted conversation retention to
180 days with an unattended hard delete, which would have quietly destroyed the DM archive
the tool exists to build. `conversation.retention_days` is now `null` and
`retention_days_must_be_explicit: true`, so enrolling your first conversation target makes
you type a number — `forever` is a legal answer, and so is `365`. The first destructive
retention sweep additionally requires `retention.confirmed: true` in config; before that
it prints its plan and deletes nothing. See [./docs/GOVERNANCE.md](./docs/GOVERNANCE.md) §6.

**Out of scope — deliberately not built**

- CAPTCHA-solving services, proxy/IP rotation, multi-account farming, credential
  automation, account rotation. None of these are needed to keep a legitimate session
  healthy, and each turns a personal data-collection tool into abuse tooling.
- Anything that defeats authentication. If content needs an account you don't have, the
  crawler stops and says so.
- **Any scraping path for X.** twscrape, RSSHub/RSS-Bridge with session cookies,
  self-hosted Nitter, third-party resellers — all permanently rejected, with dates, in
  [./docs/sources/x.md](./docs/sources/x.md). The saving is about $15/month against
  permanent suspension of the account carrying your real identity.
- **Selenium against `chat.zalo.me`.** Permanently rejected. Zalo Web encrypts its own
  request params with a per-session AES key, so a driver gets no usable JSON and is
  reduced to scraping an obfuscated virtualised DOM with no stable message ids for
  deduplication — at the same ban risk as the unofficial clients and an order of
  magnitude slower. This rejection is in the plan on purpose: it is the concrete proof
  that transport is a per-connector decision.
- **Facebook Messenger / Messenger DMs.** Out of scope at v1. (Note: Meta announced in
  February 2026 that the standalone `messenger.com` site closes in April 2026 and web
  users are redirected into `facebook.com/messages` — *likely*, press reporting. Nothing
  in this plan depends on it.)
- **Push transports of any kind at v1.** No `Receiver`, no spool directory, no second
  LaunchAgent. Every candidate producer is deferred or impossible — see §11 and
  [./docs/DECISIONS.md](./docs/DECISIONS.md) ADR-0036, which keeps the design for the day
  a push transport actually arrives and marks it not-built.
- **Sending anything, anywhere.** There is no write path in this tool. Read-only is
  enforced by absence of capability, not by a flag, because a flag is a thing that can
  be set to true.

**A note on comments and media:** comments are **in**, but bounded — a partial tree
records `more_remaining` and declares itself partial. Media is **link-only** at v1 for
every privacy class, with per-target `download` opt-in behind a mime allowlist and a byte
ceiling. Media is the only unbounded cost in the project. See §13 for what that costs you.

---

## 3. Per-platform reality

The honest version. Transport, contract position, what it costs you when it goes wrong,
and how confident the evidence is.

| Source | Transport | ToS class | Failure currency | Ship |
|---|---|---|---|---|
| **Facebook** | Headful Chrome, your logged-in session | **Prohibited by contract** | **Your account** — checkpoint, feature block, disablement | #1 |
| **Telegram** | MTProto user API (Telethon), self-issued `api_id` | **Official, documented** | Account limiting under abusive volume; a duplicated session file is destroyed permanently | #2 |
| **Reddit** | REST over OAuth (`prawcore` auth + `httpx` wire) | **Official** | Nothing — *if* you can get credentials at all | #3 |
| **X** | REST pay-per-usage, plus the free data-archive ZIP | **Official** | **Money.** The only connector where a bug bills you | #4 |
| **Zalo** | None viable for a personal account | — | — | Deferred |

**Facebook.** Meta's Terms prohibit accessing or collecting data from their products by
automated means without prior permission, with no carve-out for content your own
logged-in account can already see. This is a contract term, not a criminal statute — the
realistic consequence is account enforcement, not prosecution. Stated once:

> Automated collection violates Facebook's Terms of Service even for content your own
> account can see, and enforcement lands on your real account (checkpoint, temporary
> block, in the worst case disablement). That is the actual risk here — not legal
> exposure, account exposure. The pacing defaults below are deliberately conservative
> because of it. Consider using an account you can afford to lose access to for a week.

*(Confidence: the prohibition is established; the exact clause text in this document set
is quoted second-hand, because the research rule for this project forbids fetching
facebook.com. Read the Terms yourself if the wording matters to you.)*

**Telegram.** The one source where reading your own messages programmatically is a
first-class documented capability rather than a tolerated scrape. `api_id`/`api_hash` are
self-issued at `my.telegram.org` with **no review queue and no business gate** — but the
self-service form is reported to fail opaquely for some accounts, returning a bare
`ERROR` with no diagnostic, so **confirm you can actually obtain one on day one** (§6, T0).
The entire "Telegram is connector #2" decision rests on that credential existing; if T0
fails, the slot changes hands, and §6 pre-makes that call rather than leaving it to be
discovered at M11. Two terms bear directly on this project, both from Telegram's own API
terms page *(quoted from search extracts, not a direct fetch — verify before relying on
the exact wording)*: client apps must guard their users' privacy, and developers are
prohibited from using or aggregating Telegram data to train or fine-tune ML models. That
second one is a hard architectural boundary if anything downstream involves an LLM —
summarization, embeddings, semantic search over chat history. It is surfaced in the export
manifest so the constraint travels with the data.

**Reddit.** The headline risk is not rate limits, it is that you may not be able to
register an OAuth app at all. Self-service registration ended in **November 2025** under
the Responsible Builder Policy; every new client now goes through manual ticket review
with a stated ~7-day target, and small personal read-only projects are widely reported to
be declined or ghosted *(likely — the mechanism is corroborated by the existence of the
official help article; the decline rate is anecdotal)*. Also on the clock, and both dates
are checkboxes in M0: an r/redditdev post dated **2026-08-05** describes moving
third-party automation onto the Developer Platform, with **30 September 2026** as an
app-registration deadline — *this month* — and **31 December 2026** as the reported
deadline to opt into Reddit's Migration Program *(both likely, second-hand)*. The December
date is the one that decides whether an approved app survives into 2027, and it is exactly
the kind of thing discovered after it has passed. Reddit's Data API terms additionally
require dropping content deleted upstream, even de-identified, which is why the delete
policy for Reddit is `follow`, locked, and not overridable from the CLI.

**X.** The February 2026 pricing change flipped the answer. Free and self-serve tiers were
replaced with pay-per-usage credits at **$0.005 per post read**, deduplicated within a
24-hour UTC day. Two independent recon passes fetched `docs.x.com/x-api/getting-started/pricing`
on 2026-09-01 and agree on every figure, so the prices are `[verified]` against the
published docs — but **`console.x.com` is the billing authority and docs lag**, so the
spend cap is expressed as a dollar ceiling you type at enable time rather than as a count
derived from a price this plan believes. A 20-account daily poll lands around $15/month.
That removes the $200/month floor that used to make X pointless at personal scale. Your
own DMs never touch the API — they come from the free archive ZIP.

**Zalo.** There is no official API of any kind for a personal Zalo account. The three
things that exist are: the Official Account OpenAPI (genuinely reads conversation
history, but requires a business-verified OA — a Vietnamese business licence plus the
legal representative's ID — and a paid package); the Bot Platform (free to individuals,
but strictly forward-only with zero history read — it creates a new inbox, it does not
recover an old one); and `zca-js`, the only living unofficial personal client, which has
`getGroupChatHistory` for groups but **no 1:1 DM history method at all**, is Node-only,
and carries a March-2026 report of Zalo wiping a user's entire friend list. Every
available transport fails the actual goal. Deferring is the conclusion the evidence
forces, not hand-waving. Triggers that would unblock each are in §11 and in
[./docs/sources/zalo.md](./docs/sources/zalo.md).

**Legal baseline, corrected.** Decree 13/2023/NĐ-CP was **repealed on 2026-01-01** and
replaced by Law 91/2025/QH15 plus Decree 356/2025/NĐ-CP. Any document citing Decree 13 is
citing a repealed rule. Whether the PDPL carries a purely-personal/household exemption is
**unconfirmed at article level**, so this design assumes none applies — which is also the
honest assumption, since the tool stores third parties' messages either way.

---

## 4. The two constraints that shape everything

### 4.1 Transport is a per-connector decision

The core owns scheduling, pacing, retry, secrets, the raw store, cursors, gap accounting
and governance. A connector owns its transport **entirely**. The four transports that
ship — headful Chrome, REST over OAuth, MTProto, a local file import — share exactly one
thing worth abstracting: the bytes they produce.

So there is **one** object that crosses the connector boundary: `Envelope` — verbatim
transport bytes, plus a cursor proposal, plus a coverage claim. There is no `Fetcher`
base class, no `Transport` abstraction, no shared connector superclass. `Connector` is a
`typing.Protocol`, duck-typed, with shared *helpers* a connector may import and decline.

**The litmus test, run at every review:** delete `connectors/facebook/` and core must
still compile and pass its tests.

### 4.2 Conversations contain other people

Pages and subreddits are broadcast content. DMs and private group chats contain the
personal data of people who never agreed to be in anyone's database. That is an
engineering requirement, and it produces four structural consequences, not four warnings:

1. **Privacy is a property of the container, not the connector.** One Telegram session
   file emits broadcast channels *and* private DMs. If privacy were a per-connector
   setting you would have to either split the connector (which collides on the session
   flock and risks a permanently destroyed auth key) or lose the boundary. Containers
   carry `privacy IN ('broadcast','joined','conversation')`; the connector *proposes*,
   core *resolves*, config may only ever raise sensitivity, and an unclassifiable target
   fails closed to `conversation`.
2. **The encryption boundary is a file path, not a `WHERE` clause.** `broadcast` and
   `joined` go to `data/social.db` (plain). `conversation` goes to `data/private.db`
   (SQLCipher). Identical DDL in both. `rm data/private.db` is a complete, consistent
   operation. A query-builder bug cannot cross a file boundary.
3. **Conversation *envelopes* go to `private.db` too** — not just the parsed rows. This is
   the half everyone forgets. Raw-first means the payload contains everything the parsed
   rows contain and more; putting raw TL slices in the plain file while parsed DMs sit in
   the encrypted one defeats the entire separation.
4. **Forward-only enrolment.** Enrolling a conversation target sets `enrolled_at = now`
   and ingests only after it. Backfill is explicit and per-conversation. There is no
   `--all-dms`, no `--all-dialogs`, no `sync-everything` command anywhere in the CLI —
   the absence is the design. This is the highest-leverage control in the whole project
   and it costs nothing: every other control here is damage limitation for data already
   in the database; this one keeps it out.

   **Forward-only bounds what enters. Retention bounds what stays.** They are different
   controls and you need both answers — see §2 and §13 question 3.

---

## 5. Frozen decisions

Summary only. Full rationale lives in [./docs/DECISIONS.md](./docs/DECISIONS.md); do not
re-litigate these here. **Schema definitions are not repeated here** — see
[./docs/DATA-MODEL.md](./docs/DATA-MODEL.md) §3 for the one authoritative copy.

### 5.1 Architecture

| Decision | Choice | Why |
|---|---|---|
| Core/connector seam | `Envelope` only | It is already the thing core must durably store. Anything upstream of it forces a Selenium scroll, an HTTP GET and a 100-message TL batch into one call shape none of them fit |
| Connector base class | **None.** `typing.Protocol` + importable helpers | Inheritance would impose an HTTP-shaped lifecycle on the browser connector |
| Fetch/parse split | Absolute. `fetch()` is a lazy generator that may not parse or touch the DB; `parse()` is pure — no network, no DB, no clock | Raw-first storage is worthless if re-parsing needs credentials or a browser |
| Enforcing that | `test_parse_needs_no_secrets` constructs every connector with `secrets={}` and parses every fixture | Without the test, "parse is pure" is a comment that decays the first time someone needs one more request |
| The purity tax | Paid, not negotiated. If a post's comments need a second navigation, `fetch()` emits a **second envelope**. `parse()` never fetches | Six months of re-parseable HTML is worth more than the browser minutes |
| Cursor durability | Core commits the envelope, **then** the cursor, in one transaction. There is no `advance_cursor()` method | "The cursor advanced past data we never stored" is the one silent-corruption bug that would otherwise be written five times and gotten wrong once. `kill -9` mid-run costs at most one envelope and zero correctness |
| Cursor recovery | `crawler cursors --rebuild` re-derives every watermark from stored envelopes | Turns a correctness argument into an executable recovery path |
| Ephemeral vs durable cursors | Different **storage locations**. Durable watermark → `cursors`. In-flight page token (Reddit `after`, X `next_token`) → `runs.params`, never `cursors`, guarded by a `CHECK` | A page token persisted forever is the bug that silently breaks daily runs months later |
| Coverage claims | Every envelope declares `exact \| partial \| opaque` over an interval plus a `withheld` count, driving a core-owned `gaps` table | Reddit's 1000-item wall, Telegram's `UpdatesTooLong`, X's ~3,200-post ceiling and Zalo's enrolment floor become one table instead of four bespoke silent-data-loss bugs. `opaque` is a first-class honest value — Facebook will claim it forever |
| Plugin discovery | Hardcoded `REGISTRY: dict[str, str]` of lazy `"module:factory"` strings; `registry.py` is the only module allowed to import a connector | Entry points would need each connector to be a distributable package and would import eagerly, dragging selenium into `crawler reparse` |
| Capability rule | A `Cap` flag may exist **only if core branches on it**, and the per-source `caps = (...)` block in the source doc is the source of truth. Free-text `capabilities_note()` carries everything else | Otherwise the enum rots into documentation with three lies in it |
| CLI surface | **One normative table**, [./ARCHITECTURE.md](./ARCHITECTURE.md) §11. Every other document links to it and invents no spellings | Five documents each inventing a command name is how `crawl`/`sync` and `init-keys`/`init-private` both ended up in this plan |

### 5.2 Data model

| Decision | Choice | Why |
|---|---|---|
| Table shape | **One `items` table**, typed universal columns + JSONB `extra`. Per-source detail tables documented as an escape hatch but **empty at v1** | An FTS5 external-content index binds to exactly one content table and `bm25()` scores are incomparable across indexes — per-source tables make ranked cross-source search inexpressible |
| Escape hatch | `ALTER TABLE items ADD COLUMN … GENERATED ALWAYS AS (extra ->> '$.x') VIRTUAL` + a partial index | Verified on 3.51.0. Only `VIRTUAL` can be added to a **populated** table, which is why `local_day` must be `STORED` in the initial DDL — see 5.2's note below |
| Feed item vs chat message | **One entity.** The distinction lives on `containers.shape IN ('feed','conversation')` | The three things that actually differ — sort order, privacy class, child fan-out — are all container properties. This is what lets one Telegram connector write channels and DMs through one path with zero branching |
| `item_type` | Provenance, **not** a discriminator. Nothing in core branches on it | Standing test for whether a discriminator is being abused |
| Threading | Adjacency (`parent_id`) is truth; `parent_ref` (raw platform string) is **always** written even when unresolvable; `thread_path`/`root_id`/`depth` are derived and may be NULL | Every platform delivers children before parents somewhere. A path-only design makes those rows unwritable. Correctness falls back to a recursive CTE, never to `thread_path` |
| Closure table | **No.** A 20k-comment thread at depth 30 costs ~200k rows to buy reparenting, and none of these platforms ever reparents | |
| Raw payloads | `envelopes` (one row per distinct byte sequence, `sha256` UNIQUE) **split from** `envelope_fetches` (one row per fetch event) | The single most important correction to the original schema, whose `sha256 UNIQUE` on a table also carrying `captured_at` collapsed Mon/Tue/Wed fetches of an unchanged page into one row with Monday's timestamp — destroying the exact input to soft-delete detection |
| `envelopes.seq` | `INTEGER PRIMARY KEY AUTOINCREMENT`, not plain rowid | Retention pruning and redaction both delete envelope rows; rowid reuse would silently break every `WHERE seq > :last_processed` reparse scan. One word, real bug |
| Provenance | `items.first_seq`/`last_seq` plus `item_envelopes(item_id, envelope_seq, role)` with `role` **constrained to `'primary'`** | The single-valued CHECK is the point: adding a second role is a migration, which is the review checkpoint. It makes "metric batches write no provenance rows" a schema property rather than a convention in the writer |
| Metrics | `metric_observations` is a **change-log**, not a sample-log — a row only when the value moved — plus denormalized `items.metrics_current` | Per-source metric columns die on Telegram's per-emoji reactions alone; an unconditional daily sample is ~180M rows/year at 100k items |
| `approximate` flag | Load-bearing. Reddit vote-fuzzes; Facebook rounds above 1k in the UI (`1.2K` → 1200) | A ±3 delta is noise and the schema must say so rather than letting a chart imply signal |
| Edits | `item_versions(item_id, content_hash, …)` on the same conditional-insert rule | Otherwise an edit overwrites `text` in place and is recoverable only by hand from raw envelopes |
| FTS5 | **One** unconditional-trigger external-content index **per file**, `unicode61 remove_diacritics 2`, `detail=full`, no prefix index | The file split makes the trigger predicate unconditional in both files, which structurally eliminates the delete-trigger corruption trap. `detail=full` because Vietnamese is syllable-segmented so phrase queries are the primary form |
| Tokenizer, precisely | `remove_diacritics 1` leaves diacritics in place on a **single codepoint carrying more than one diacritic**, which is most of the Vietnamese vowel set (`ế` U+1EBF, `ộ` U+1ED9). Only `2` folds them | This says nothing about NFD-decomposed input. If any source can deliver decomposed text, normalise to NFC before insert — a mixed-normalisation index fails silently |
| FTS audit | **Never** audit the privacy split with `count(*)` on an FTS table | External-content FTS5 reads the content table's row count, so it will falsely pass. Use `MATCH` assertions |
| `local_day` | `TEXT GENERATED ALWAYS AS (date(published_at,'unixepoch','+7 hours')) STORED`, in the **initial** DDL | Vietnam is UTC+7 year-round with no DST. **The restriction is real but the engine only enforces it once the table has rows** — on SQLite 3.51.0 `ALTER TABLE … ADD COLUMN … STORED` succeeds on an *empty* table and fails with `cannot add a STORED column` on a populated one. So a migration tested against an empty dev DB passes and then fails on your real database. Put it in the initial DDL; do not rely on the engine to stop you |
| Runtime floor | Python 3.13, **SQLite ≥ 3.45** asserted in `db.connect()` and `doctor` | JSONB, `->>`, STRICT tables and FTS5 features all have version floors, and a uv-managed interpreter bundles its *own* SQLite. `sqlite3.version` was removed in Python 3.14 — use `sqlite_version_info` only |

### 5.3 Governance

| Decision | Choice | Why |
|---|---|---|
| Privacy classes | Three: `broadcast`, `joined`, `conversation`. **Two** physical files | Three because retention, media, export, delete-following and rescan window genuinely differ across all three; two files because the encryption boundary must be a path |
| Encryption | FileVault is a **tool-checked prerequisite** (`fdesetup status` in `doctor`; enrolling a conversation target refuses if it is off), plus SQLCipher via `sqlcipher3` 0.6.2 for `private.db` only | The realistic threat is a Time Machine snapshot or a `~/Dropbox` symlink, not a stolen laptop — and FileVault does nothing for that while the machine is running. 0.6.2 (2026-01-07) ships self-contained `macosx_11_0_arm64` wheels for cp310–cp314 with SQLCipher 4.x vendored |
| SQLCipher's limits | Documented verbatim, not implied: while a crawl runs the key is in process memory and the DB is decrypted for that connection. It covers backup and sync exfiltration, **not** malware running as your own uid | A design that implies otherwise creates a false sense of security that is *worse* than none, because it changes behaviour |
| Identical DDL, with a named fork | Both files get the same statement list — **conditional on FTS5 being present in the SQLCipher build.** M0 settles it; if FTS5 is absent, `private.db` gets the same DDL minus the FTS objects, `schema_versions` records the divergence as a declared state, and `crawler search --private` degrades to a `LIKE` scan | "Nothing else changes" was wrong: without FTS5 the statement list cannot be applied at all, which breaks the invariant that makes `rm private.db` safe. The fork is written down instead of assumed away |
| Retention | `broadcast` forever; `joined` 730 days; **`conversation` has no default and must be set explicitly.** Raw bodies: broadcast forever, joined and conversation 90 days. The first destructive sweep requires `retention.confirmed: true` and prints a plan first | A default that destroys the user's primary goal gets switched off in anger the day it is noticed, which leaves the whole class ungoverned |
| Pseudonymization | At **export**, not at storage. Identity is stored `clear` in both files | A private DM archive full of `P-7f3a` labels is useless to its owner, and pseudonymizing the author column while storing full message text is theatre — names appear in the text. The controls that actually work are scope, encryption, retention, export gating and purge. There is no per-target `identity_mode` column, because it would have had no reader |
| Identity join key | `authors.actor_hmac`, always present; `platform_uid`/`display_name`/`handle` nullable | Redaction is one `UPDATE` that nulls the clear columns without breaking a foreign key. Pepper (32 random bytes) lives in Keychain, never in the DB. Documented honestly as pseudonymization, **not** anonymization |
| Cross-platform identity linking | **Not in the schema at v1.** Adding it is a schema migration *and* an ADR | Two tables with zero writers is a lot of DDL for a tripwire. The tripwire is now a written rule (ADR-0023), which is cheaper and just as binding |
| Phone numbers | **There is no `phone` column anywhere in the schema.** `scrub()` drops `phone\|email\|latitude\|longitude\|access_hash\|file_reference\|online\|status\|read_outbox_max_id` before persistence, with `assert_clean()` re-checking on every non-broadcast write | Structural, not a policy note: there is nowhere to put a phone number, so no connector can accidentally persist one. There is also no `location` media kind, for the same reason. Telegram's `access_hash` is the non-obvious one — it is an account-scoped capability token, so persisting it stores *the ability to look strangers up* |
| Redaction durability | Every purge writes a `redactions` row with `block_reingest = 1` that every ingest path consults before insert | Without it, tomorrow's 08:05 run silently re-creates every row you just deleted. A redaction you believe worked but did not is worse than none. Tested directly |
| Purge honesty | `PRAGMA secure_delete=ON` at **creation** (persistent header flag), `VACUUM` after any conversation purge, `PRAGMA wal_checkpoint(TRUNCATE)`, and the tool **prints** that APFS local snapshots may still hold the pre-purge file | It does not run `tmutil deletelocalsnapshots` itself — that is a system-wide destructive act and your call |
| Upstream deletes | `--respect-upstream-deletes=auto`, resolving per class: `tombstone` for broadcast, `follow` for joined and conversation, `follow` **forced and unoverridable** for Reddit | For a public page the *fact* of a deletion is often the datum. An unsend in a DM is a request expressed in the only vocabulary the other person has. Reddit's terms leave no choice |
| Delete detection | `absence_strikes` (3 broadcast, 2 conversation) inside a positively-covered range, gated on `containers.access_state='ok'` **and** `runs.status='ok'` | Otherwise one Facebook checkpoint marks 4,000 posts deleted |
| **Facebook deletes are not detectable** | `rescan_window_days: 0` for Facebook. An `opaque` coverage claim contributes no absence evidence, so a feed re-scroll can never accumulate a strike — it would only spend the scarcest budget in the project for a signal the design discards | Saying so is the honest version, and it mirrors what X already does. The sweep arrives with Telegram, where coverage is exact and it actually works |
| Export | Default-deny by class. `--include-private` is **refused outright** when stdin is not a TTY | Not prompted — refused, so a scheduled job can never emit conversation data. Output paths under iCloud/Dropbox/OneDrive/CloudStorage or an untracked git work tree are rejected. Conversation envelopes are not exportable at all; no flag exists |

### 5.4 Operations

| Decision | Choice | Why |
|---|---|---|
| Scheduler | **One LaunchAgent.** `…crawlersocial.tick` = `StartCalendarInterval` 08:05 + 20:35, `LimitLoadToSessionType=Aqua`, `ProcessType=Interactive`, wrapped in `caffeinate -i`, deliberately **no** `KeepAlive` | launchd already is a supervisor; a second supervisor is a second thing that can be down. The always-on receivers agent is cut from v1 — it had zero producers |
| Writers | **Exactly one process ever writes SQLite** | One writer means a batch job never contends with anything else on the WAL, and it is what makes `Cap.SINGLE_FLIGHT` a complete answer to `AUTH_KEY_DUPLICATED` |
| launchd semantics | Load-bearing: launchd **defers** a missed `StartCalendarInterval` until wake and **coalesces** multiple misses into one invocation | A closed lid delays the job; a week away yields one run, not seven. Which makes it non-negotiable that the daily job catches up **by cursor** and never by "fetch yesterday" |
| Rate limiting | One `TokenBucket` behind one `Budget`, configured from either a published quota (Reddit, header-corrected) or a paranoia budget (Facebook, no feedback possible). `mode:` in config is a comment for humans that **no code branches on** | If anyone writes `if mode == 'paranoia'`, the abstraction has failed and must be split |
| `Budget` | Unifies `max_items` (`--limit N`), `deadline_ts` (Facebook's 25-minute session cap), `spend_units` (X's per-run ceiling), `max_requests`, and the bucket. ~20 lines | The one piece of pacing that genuinely *is* shared, unlike the bucket constants |
| X's monthly allowance | **Money, not rate.** A persisted `usage_counters` row with `metric='cost_micros'` checked *before* the run starts, and a `spend_cap` the user types **as dollars** at enable time | A bucket smooths a rate; a counter enforces a budget. Expressing the cap in dollars rather than in reads means a repricing cannot silently widen it |
| X run time | 01:00 UTC (08:00 ICT), all retries bounded by the UTC day | X deduplicates billing within a 24-hour UTC window, so same-day retries are free and a midnight-straddling run is double-billed. The config carries a comment saying so, because it otherwise reads as arbitrary |
| Error taxonomy | Six in-process dispositions — `OK / RETRY / WAIT / HUMAN / STOP / DROP` — plus `SUSPECT`, which is never raised by a connector but detected by core. Projected onto exit codes `0 / 69 / 75 / 77 / 78 / 86`, `DISARMING = {77, 78, 86}` | The same taxonomy survives any process boundary — launchd today, a Zalo Node sidecar later. `classify(exc) -> Verdict` is pure and fixture-testable |
| `STOP` | Disarms the schedule and **never** retries. Writes `sources.state='stopped'`; every subsequent run is an immediate no-op exit 0 until `crawler resume --source X` | A retry loop is how a temporary Facebook block becomes a permanent one, and how a Telegram `PeerFloodError` turns into an account loss |
| `WAIT` | Honours server-supplied durations literally where they exist (Telegram's `FloodWaitError.seconds` is authoritative). Facebook supplies nothing, so it gets policy backoff 15m → 1h → 4h → stop | |
| Secrets | macOS Keychain via `security(1)`, service prefix `vn.moonbase.crawler-social.*`; resolution is env-var → Keychain → **loud** failure that prints the literal `security add-generic-password` command | Never a silent fallback to a file. A gitignored `.env` is one `git add -A` from a commit and one Time Machine snapshot from a plaintext copy that outlives the mistake |
| The two Keychain can't hold | Telegram `.session` and the Chrome profile → 0600/0700 paths **outside the git worktree** | So no `.gitignore` mistake can ever stage them. The `.session` is a bearer credential equivalent to password + 2FA, and `AUTH_KEY_DUPLICATED` destroys it permanently on any concurrent use from two IPs — hence `Cap.SINGLE_FLIGHT`, flocked, never in a synced directory, never copied to a second machine |
| Packaging | **One** uv project with per-connector dependency groups | Five lockfiles is complexity spent on isolation you do not need yet. The test lane runs `--group core --group reddit` and asserts `'selenium' not in sys.modules` after a full reparse, which proves the invariant mechanically. Trigger to split: an actual dependency conflict, or a second machine |
| Compression | `codec` column from day one (`raw\|zlib\|zstd`) with a `zdict` table; **zlib at v1**, zstd + trained dictionary with connector two | The measured 5.5x-vs-1.8x win is specifically for small JSON/msgpack records where there is no window to find repetition in 754 bytes; Facebook's large HTML already compresses ~8:1 under zlib. Schema frozen now so the switch is a flag flip |
| Testing | `FixtureConnector` satisfies the same Protocol, replays recorded envelopes respecting `budget`, and delegates `parse()` to the **real** connector. `crawler tick --fixture tests/fixtures/` runs the entire production pipeline with no browser and no network | Fixtures are captured by `crawler capture --redact`, never hand-copied. No real DM or private-group message enters the repo, redacted or otherwise |
| Golden parses | `sha256(canonical_json(ParseResult))` per fixture per `parser_version` | How you notice an "innocent" selector tweak silently stopped populating a field — the exact failure raw-first storage exists to let you recover from |

### 5.5 Renames from the original plan

| Was | Is now |
|---|---|
| `fbcrawl` | `crawler` |
| flat module map | `core/` + `connectors/facebook/` |
| `posts` + `comments` | `items` (one table, `containers.shape` carries the distinction) |
| `post_counts` | `metric_observations` (change-log; gains `approximate` for `1.2K` rounding, and `source_kind`) |
| `watermarks` | `cursors` |
| `crawl_runs` | `runs` (status gains `empty \| suspect \| partial \| rate_limited \| needs_human \| blocked`) |
| `raw_payloads` | `envelopes` + `envelope_fetches` |
| `authors` | `authors`, gaining `actor_hmac` |
| the original §7 pacing table | the `facebook` entry in shared rate config, **numbers unchanged** (§8) |
| the original §2 `stealth.py` isolation argument | generalized: every third-party client (Telethon included) lives inside its connector directory and never leaks a type into core. There is no `stealth.py` module — driver construction and its stealth options live in `connectors/facebook/driver.py` |

**The tables that are new**, listed mechanically from the frozen DDL rather than by hand
(`sqlite3 :memory: < core/schema.sql` then `.tables`, minus the original plan's set):

```
accounts          containers        cursors           envelope_fetches  envelopes
field_stats       gaps              item_envelopes    item_relations    item_versions
media             metric_schedule   metrics           parsers           redactions
schema_versions   sources           usage_counters    zdict
```

Plus `items_fts` and its four FTS5 shadow tables, and `sqlite_sequence` (created by
`AUTOINCREMENT`). Full object count and the executed verification are in
[./docs/DATA-MODEL.md](./docs/DATA-MODEL.md) §3.

---

## 6. Milestone plan

**Sixteen milestones (M0–M15) in five phases (0–4).** Each is independently testable and
each has an acceptance check you can actually run.

**Two ordering rules the plan obeys, both learned the hard way:**

1. **A real row lands early.** The first real Facebook post is queryable in
   `data/social.db` at **M2 — the third milestone**, before the contract, the run loop,
   the full parser or the CLI exist. Eight milestones of scaffolding before the riskiest
   external assumption is tested even once is the framework-first trap, and the earlier
   draft of this plan walked straight into it. M2 is a deliberately thin walking skeleton;
   M3 refactors it behind the frozen `Protocol`, which is a day's work because the seam is
   `Envelope`.
2. **One-shot decisions still happen first.** M1 (the DDL) stays ahead of M2 because
   `local_day STORED` cannot be added to a populated table, so the schema is genuinely
   cheaper to write now than to migrate later. That is the *only* thing that earns a slot
   ahead of the first row.

**M1, M3, M4, M6 and M7 need no browser and no network at all.** That is where most of the
real logic lives — most of this is ordinary testable Python, not scraping roulette.

**The shapes are fixed today; the machinery arrives just in time.** The full DDL and the
contract dataclasses are written at M1 and M3 because they are cheap to write and
expensive to migrate. The billed counter, the metric sweep, the gap drain, the archive
importer and the zstd dictionary are *specified* now and *unbuilt* until the connector
that needs each one arrives.

---

### Phase 0 — Preflight (day one; costs three forms, one browser session, and an afternoon)

#### M0 — Scaffold, prerequisites, and the four day-one external asks

**Goal:** the project exists, the environment is proven capable, and every
high-variance external dependency has been fired off **today**, because each has a
multi-day latency you do not control.

**The four day-one asks.** These are not milestones; they are forms and clocks. Fire all
four before writing any code, because each answer arrives days later and each one changes
the plan if it comes back wrong.

- [ ] **R0 — Reddit credential spike.** Submit a script-app registration under the
      Responsible Builder Policy. Stated response target ~7 days. The highest-variance
      assumption in the plan, free to test, and everything about Reddit forks on the answer.
  - [ ] by **2026-09-30** — register any existing/grandfathered Reddit app so its feedback
        counts *(likely, second-hand — check the r/redditdev post directly)*
  - [ ] by **2026-12-31** — decide on the Migration Program opt-in, **or record in writing
        the decision not to**. This is the date that decides whether an approved app
        survives into 2027
- [ ] **T0 — Telegram `api_id`.** Log in to `my.telegram.org` → API development tools →
      create an application; store `api_id`/`api_hash` in Keychain. There is no review
      queue, **but the form is reported to fail opaquely for some accounts** with a bare
      `ERROR` and no diagnostic, and the reported workarounds are folklore (different
      browser, incognito, different network, wait a day). **This blocks the entire
      Telegram connector, so it is confirmed on day one, not at M11.**
      **If T0 fails:** Telegram is not connector #2. The slot goes to whichever of Reddit
      (if R0 came back approved) or the X archive importer is available, and the private
      store (M11) ships with whichever of those first needs it — the X archive needs it for
      DMs, so it always does. Pre-made here rather than discovered mid-phase.
- [ ] **X0 — request the X data archive.** Settings → Your Account → Download an archive of
      your data. **~24h turnaround (up to 48h for large accounts) and the download link
      expires 7 days after generation** — download it immediately and keep it outside the
      worktree. Then `unzip -l` it and paste the real manifest into
      [./docs/sources/x.md](./docs/sources/x.md) §2.3, which closes that connector's largest
      unknown for the cost of one command.
- [ ] **P0 — start the Facebook profile clock.** Run `crawler login --source facebook`
      into the dedicated profile, then **use that profile by hand like an ordinary browser**
      — read some feeds, follow some links, leave it alone between sessions. Session age
      and history are assets; a brand-new profile driving a group feed with an automation
      stack attached is the single highest-risk action in the project. The clock starts on
      day one so it has run down by the time it matters. Thresholds are enforced, not
      advisory: **≥ 3 days** before M2 touches a public Page, **≥ 14 days** before M5
      touches a group. `crawler doctor` reports profile age from the user-data-dir mtime
      and `crawl` refuses below the threshold with an actionable message.

**The scaffold:**

- [ ] `uv init`; Python 3.13 pinned in `.python-version`; entry point `crawler = "cli:main"`
- [ ] Dependency **groups**, not one flat list: `core` (stdlib only), `facebook`
      (`selenium`, `seleniumbase`, `beautifulsoup4`, `lxml`), `telegram` (`telethon`,
      `cryptg`, `msgpack`), `reddit` (`prawcore`, `httpx`), `crypto` (`sqlcipher3`),
      `dev` (`praw`, `pytest`)
- [ ] `core/config.py`: one `Config` dataclass, every default in it, YAML loader —
      the pattern from `crawler-pages/webcrawler/config.py`
- [ ] **Prove the runtime, don't assume it.** Assert `sqlite3.sqlite_version_info >= (3,45,0)`
      on the *project* interpreter (a uv-managed CPython bundles its own SQLite, which is
      not the one your system `sqlite3` CLI links)
- [ ] **Settle the FTS5-in-SQLCipher fork before anything depends on it.** Run
      `SELECT * FROM pragma_compile_options()` on a `sqlcipher3` connection and grep for
      `ENABLE_FTS5`. This is a **gate with two written branches**, not a footnote — see
      [./docs/DATA-MODEL.md](./docs/DATA-MODEL.md) §14 item 1 for both
- [ ] `fdesetup status` — FileVault on, verified not assumed
- [ ] `crawler init-keys`: generate the two Keychain items — the 32-byte identity pepper
      and the `private.db` key
- [ ] `.gitignore`: `data/`, `exports/`, `profiles/`, `*.db*`, `*.session*`, `.env*`

**Done when:** `uv run crawler --help` prints (every command may be a stub); `uv run
crawler doctor` reports the SQLite version, FileVault state, the FTS5-in-SQLCipher answer,
both Keychain items resolving, and the Facebook profile age; the Reddit application has a
ticket number; `api_id`/`api_hash` are in the Keychain **or** T0's failure is recorded with
today's date; the X archive has been requested.

---

### Phase 1 — Core and Facebook

Facebook first because it is what you actually want, because it is the only source that
forces the GUI lane, and because it is the hardest — design the contract against the
worst source and let Telegram over-deliver on it later.

#### M1 — Storage layer *(no browser, no network)*
**Goal:** the frozen DDL exists, applied once, migratable, with every invariant tested.

This is the one milestone that legitimately precedes a real row, because `local_day` is a
`STORED` generated column and SQLite refuses to add one to a populated table.

- [ ] `core/schema/_core.sql` — the frozen DDL from
      [./docs/DATA-MODEL.md](./docs/DATA-MODEL.md) §3, **verbatim, copied not paraphrased**.
      `PRAGMA journal_mode=WAL` and `PRAGMA secure_delete=ON` at **creation** (both
      persistent; `secure_delete` set later does not retroactively zero freed pages)
- [ ] `core/db.py`: `connect(store)`, per-connection pragmas (`synchronous=NORMAL`,
      `foreign_keys=ON`, `busy_timeout=5000`), the version assertion, and a migration
      runner off **per-source** `schema_versions` counters — so Facebook's inevitable
      DOM-rotation migrations do not drag Telegram through a version bump
- [ ] Only `data/social.db` is created at M1. The `open_store(STORE_PRIVATE)` branch
      exists and raises `NotImplementedError` until M11
- [ ] All upserts, with `COALESCE` on every optional field and the
      `visibility='redacted_locally'` guard **on every content column, not just
      `visibility`** — see [./docs/DATA-MODEL.md](./docs/DATA-MODEL.md) §13.2, which
      reproduces the resurrection bug the naive form allows
- [ ] `fcntl.flock` cross-process lock helper
- [ ] Tests: migrate twice (no-op); upsert the same item twice (one row, `last_seen`
      bumped, `first_seen` unchanged); `envelope_fetches` gains a row on a re-fetch of
      identical bytes while `envelopes` does not; a metric that did not move writes no
      `metric_observations` row; both `targets_ack_*` triggers ABORT; `PRAGMA
      integrity_check` returns ok after an insert/update/delete cycle

**Done when:** `pytest` green; `sqlite3 data/social.db "SELECT count(*) FROM sqlite_master"`
returns **77**; the re-fetch test proves the `envelopes`/`envelope_fetches` split actually
preserves "when did I last confirm this existed".

#### M2 — Walking skeleton: the first real Facebook rows *(browser; needs profile age ≥ 3 days)*
**Goal:** ten real posts from a public Page are queryable in `data/social.db`. No contract,
no registry, no run loop — just enough to prove the riskiest external assumption in the
project against reality.

This milestone deliberately targets **a public Page, not a group**. Groups wait for M5 and
the 14-day profile threshold; a Page is the lowest-risk surface and it is all the skeleton
needs.

- [ ] `connectors/facebook/driver.py`: SeleniumBase UC Mode driver construction, all of
      it behind this one module so swapping the driver later is a one-file change.
      Dedicated user-data-dir at
      `~/Library/Application Support/crawler-social/chrome/default/`, mode 0700,
      **outside the git worktree**. Profile-lock detection with a clear error when Chrome
      already has the profile open. Forced `en_US`, `Asia/Ho_Chi_Minh`, fixed realistic
      viewport, and a **fingerprint consistency check** — UA, `navigator.platform`,
      `Accept-Language` and timezone must tell **one** story, because an inconsistent
      spoof is louder than no spoof
- [ ] **The extraction spike, timeboxed to half a day.** Test the three candidate
      transports in the order given in
      [./docs/sources/facebook.md](./docs/sources/facebook.md) §3. Note up front:
      Selenium's **BiDi network module does not document any way to read a real server
      response body** (only intercept, provide, fail, and auth) — *verified from
      selenium.dev, 2026-09-01*. SeleniumBase **CDP Mode** does:
      `add_handler(mycdp.network.ResponseReceived, …)` then
      `page.send(mycdp.network.get_response_body(request_id))` returning `(body, is_base64)`
      — *verified from the SeleniumBase repo's own `examples/cdp_mode/raw_xhr_sb.py`*.
      **This is a parse-quality gate, not a project gate:** capture `fb.feed_html`
      viewport snapshots from day one unconditionally, and if response-body interception
      works capture `fb.graphql.feed` **alongside**, with the parser preferring the richer
      kind. Write the outcome into facebook.md §3 as a dated line
- [ ] Scroll-and-snapshot against one hardcoded target. Facebook unmounts offscreen posts
      while you scroll, so harvesting once at the end silently loses most of the feed —
      snapshot each viewport as you go. **This is the single most common way these
      crawlers quietly under-collect**
- [ ] A minimal parser extracting exactly three fields: `platform_item_id`,
      `published_at`, `text`. Nothing else. The fallback chains arrive at M4
- [ ] A direct `INSERT` through M1's upsert. No `Envelope`, no `commit_envelope`, no
      cursor — those arrive at M3
- [ ] Save the six fixtures listed in facebook.md §3.3, each with a `meta.json` sidecar
      reproducing the `Envelope` non-body fields. Everything after this milestone can run
      offline against them

**Done when:** `sqlite3 data/social.db "SELECT local_day, substr(text,1,60) FROM items
ORDER BY published_at DESC LIMIT 10"` prints **ten real vnexpress posts**; the spike
decision is recorded with a date; and `tests/fixtures/facebook/` contains at least six
fixtures with sidecars.

#### M3 — The contract and the run loop; refactor the skeleton behind it *(no browser, no network)*
**Goal:** core can run a complete tick against a fake connector, with commit ordering,
budget accounting, coverage checking and SUSPECT detection all real — and M2's skeleton now
runs through it without losing a row.

- [ ] `core/contract.py`: the frozen dataclasses — `Envelope`, `Cursor`, `Coverage`,
      `Verdict`, `Budget`, `Cap`, `GovernanceProfile`, `Target`, and the `*Draft` types.
      Frozen dataclasses, module-level string constants for kinds,
      `from __future__ import annotations` — house style from `challenge.py`. Reproduced in
      full in [./ARCHITECTURE.md](./ARCHITECTURE.md) §5
- [ ] `core/registry.py`: the hardcoded lazy `REGISTRY` dict. A typo yields
      `UnknownSourceError` listing the valid names
- [ ] `core/pace.py`: `Bucket` + `Budget.take()`
- [ ] `core/verdict.py`: the six dispositions and the exit-code projection
- [ ] `core/commit.py`: **`commit_envelope()` — one transaction.** Dedupe body on
      `sha256`; insert the envelope; *always* append an `envelope_fetches` row; upsert the
      drafts; write `item_envelopes` rows with `role='primary'`; cross-check `coverage`
      against `res.found`; record any gap; write `field_stats` from
      `ParseResult.field_stats`; **and only then** write `cursor_after`. The cursor cannot
      outrun the bytes
- [ ] **The SUSPECT set, all three rules:** (a) claimed `exact` over a non-empty interval
      with `found == 0` — fires on run one; (b) zero items from a target that produced
      items in each of its last three runs — catches what (a) cannot see; (c) a run whose
      `runs.stop_reason` is a watermark stop at scroll ≤ 2 with `items_new == 0` — catches
      the broken-pinned-guard failure that (a) and (b) both go blind on. Any of the three
      rolls back the cursor write and writes `status='suspect'`; two consecutive escalate
      to HUMAN
- [ ] `connectors/fixture.py`: `FixtureConnector` — replays recorded envelopes in order
      respecting `budget`, delegates `parse()` to the real connector, `probe()` returns OK
- [ ] **Refactor M2's skeleton behind the Protocol.** The seam is `Envelope`; retrofitting
      a working generator behind it is a day, not a rewrite. `crawler tick --fixture
      tests/fixtures/`

**Done when:** re-running M2's target through `commit_envelope` produces the **same item
count** as the direct-INSERT version plus a real `envelopes` row and cursor; a fixture tick
writes envelopes, items, a run row and a cursor, in that order; killing the process
mid-tick and re-running loses at most one envelope and produces no duplicate items; a
fixture whose coverage claims `exact` but parses to zero items produces `status='suspect'`
and an **unchanged** cursor; and `rm -rf connectors/facebook/ && pytest` is still green.

#### M4 — Parsers *(no browser, no network)*
**Goal:** pure functions from bytes to drafts, with a documented fallback chain per field.

- [ ] `connectors/facebook/parse.py`: `parse(env) -> ParseResult`. Pure — no network, no
      DB, no clock (use `env.captured_at`), no self-mutation
- [ ] The per-field fallback chain from
      [./docs/sources/facebook.md](./docs/sources/facebook.md) §8. A missing field returns
      `None` and never raises
- [ ] Timestamps: find the **absolute** time (aria-label / `title` / JSON field), not the
      "3h" relative text — and set `published_prec` honestly when you can only get a day
- [ ] **The tri-state fields.** `is_pinned`, `is_sponsored` and `more_remaining` are
      `None` when the selector matched nothing and `False`/`0` only when the parser
      **positively observed the absence** of the badge. Without this, a broken selector
      is indistinguishable from a real negative and the canary can never fire on the one
      field that matters most (§10 item 1)
- [ ] `ParseResult.field_stats: dict[str, tuple[int, int]]` — per field, `(seen, filled)`.
      This, not `diagnostics`, is what core writes into `field_stats`
- [ ] Reaction counts parsed from the rounded UI string (`1.2K` → 1200) get
      `MetricDraft(approximate=True)`
- [ ] Tests over the M2 fixtures, table-driven in the style of
      `crawler-pages/tests/test_challenge.py`
- [ ] `test_parse_needs_no_secrets`: construct the connector with `secrets={}`, parse every
      fixture

**Done when:** every fixture parses with all core fields populated; a deliberately mangled
fixture yields `None`s instead of a traceback; and the golden-parse snapshot
(`sha256(canonical_json(ParseResult))`) is committed per fixture per `parser_version`.

#### M5 — Feed harvest loop, watermark and guards *(browser; groups need profile age ≥ 14 days)*
**Goal:** a lazy generator that yields envelopes and stops when the budget says so, and an
incremental watermark that cannot silently stop collecting.

- [ ] `connectors/facebook/fetch.py`: the full scroll-and-harvest **generator**, replacing
      M2's skeleton loop. Dedupe in-flight by `platform_item_id`; expand every "See more"
      **before** capturing that post's subtree, because a truncated body captured raw is
      permanently truncated and no reparse recovers it
- [ ] Group feeds get `?sorting_setting=CHRONOLOGICAL` *(the parameter is
      community-established, not documented by Meta — treat it as needing re-confirmation;
      the "Recent posts" button is hidden on most groups, which is why people type it by
      hand)*
- [ ] Human pacing with a **seedable** `random.Random` so tests stay deterministic
- [ ] Every envelope claims `Coverage(kind=COVER_OPAQUE, note="scroll_unmount")` — forever.
      Inventing a scroll-position interval would be worse than claiming none, because core
      would then confidently record "no gap" over a feed that unmounted half its posts
- [ ] `Budget.deadline_ts` enforced: the session ends at 25 minutes by quitting the driver
- [ ] SIGTERM trapped: checkpoint by yielding, then return
- [ ] **The watermark and its three guards**, per facebook.md §9: stop after 5 consecutive
      already-seen posts; never fire the stop rule on an empty database; and **exclude
      `is_pinned` and `is_sponsored` rows from the counter**, because pinned posts sit at
      the top forever and would trip it on scroll one
- [ ] The connector stamps `stop_reason` and `scroll` into the last envelope's `meta` so
      core's SUSPECT rule (c) has something honest to read
- [ ] First group target enrolled here, gated on the 14-day profile threshold

**Done when:** a manual run reports "harvested N unique posts in M scrolls" with N growing
sensibly; a second run on the same feed yields the same N ±1; a run with `--limit 20` stops
at 20 without exhausting the scroll and writes **no** durable cursor; and a simulated
broken pinned-badge selector produces `status='suspect'`, not a silent success.

#### M6 — Persistence and reparse *(no browser, no network)*
**Goal:** prove the raw-first payoff before you need it.

- [ ] `crawler reparse --source facebook` re-runs the parser over stored envelopes with
      **zero network**, driven by `WHERE parser_version < (current)` and ordered by `seq`
- [ ] Parent repair pass: resolve `parent_ref` → `parent_id` so ingestion order does not
      matter
- [ ] Backfill `thread_path` for the rows it can, leaving NULL where it cannot, iterating
      until it changes zero rows
- [ ] The redaction guard tested through reparse specifically: redact, then reparse, then
      assert both zero FTS hits and NULL content

**Done when:** crawling the same fixture set twice gives an identical item count and a
second `envelope_fetches` row per envelope; bumping `parser_version` and running
`reparse` rewrites rows without touching Facebook; and the test lane runs
`uv sync --group core --group facebook`, does a full reparse, and asserts
`'selenium' not in sys.modules`. **This is the payoff of raw-first storage — prove it
works before you need it.**

#### M7 — Wall taxonomy and verdicts *(no browser, no network for the tests)*
**Goal:** every Facebook wall is detected, classified, and produces the right action.

- [ ] `connectors/facebook/walls.py`, ported from
      `~/dev/crawler-pages/webcrawler/challenge.py`. Take the whole design: the frozen
      dataclass, the kind constants, the `human_clearable` property, the conservative
      "would rather miss a wall than mislabel a real page" stance, and the
      `_WALL_TEXT_LIMIT` length guard that stops a long article *mentioning* a checkpoint
      from being classified as one. Swap Cloudflare markers for Facebook ones.
      `detect(html, title, status, final_url)` is **pure**
- [ ] `connectors/facebook/errors.py`: `classify(exc) -> Verdict`, pure and
      fixture-testable
- [ ] Each wall carries its verdict: login redirect → HUMAN(78); `/checkpoint/` → STOP(86);
      "temporarily blocked" → WAIT with policy backoff; group-not-joined → DROP;
      empty-feed-with-200 → detected by core as SUSPECT, not by the connector. Full table
      in [./docs/sources/facebook.md](./docs/sources/facebook.md) §6
- [ ] Every detection emits an `fb.wall_html` envelope before exiting, so the classifier
      can be improved against real evidence later
- [ ] **Hard blocks must never retry-loop** — that is how a temporary block becomes a
      permanent one. STOP writes `sources.state='stopped'` and every subsequent scheduled
      run is an immediate no-op exit 0 until `crawler resume --source facebook`

**Done when:** the classifier's unit tests pass on saved wall HTML; a simulated block
halts the run, writes `status='blocked'` and exit code 86, and a second immediate `tick`
exits 0 without opening a browser.

> The empty-feed-with-200 case is the one that bites. It looks like a clean successful
> crawl of a quiet day, so the watermark advances and every subsequent daily run skips
> real posts forever. Treat "0 items from a target that had items yesterday" as a
> **failure**, not a result: don't advance the cursor, and flag the run. Core does this
> for every connector via the SUSPECT set (M3) — the Facebook connector contributes
> nothing to it beyond an honest coverage claim and an honest `stop_reason`.

#### M8 — CLI surface
**Goal:** every command in the normative table is wired, with checkable argument validation.

- [ ] Implement **every row** of [./ARCHITECTURE.md](./ARCHITECTURE.md) §11, which is the
      single normative CLI surface. No other document invents a spelling
- [ ] CLI validation reads capabilities: `--since` / `--backfill` require `Cap.BACKFILL`
      **or** `Cap.BACKFILL_CAPPED` (the capped flag implies the plain one for argument
      validation, and prints the ceiling); a `Cap.FILE_IMPORT`-only connector rejects
      `crawl` with an actionable message
- [ ] Rotating file log plus console, with `source`/`target`/`run_id` context on every line
      so one `grep` covers all sources
- [ ] Meaningful exit codes from the frozen table; actionable errors instead of tracebacks

**Done when:** every command in ARCHITECTURE §11 has a working `--help` **and a test
exercising its argument validation**, and `crawler capabilities` renders each connector's
`capabilities_note()` free text.

#### M9 — Daily job
**Goal:** it fires on schedule, unattended, and you can tell afterwards that it did.

- [ ] `core/tick.py`: iterate enabled targets by `(enabled, priority, id)`, per-target
      limits, flock for `Cap.SINGLE_FLIGHT` sources, pacing between targets, backoff, run
      report
- [ ] `scripts/run-tick.sh` (`set -euo pipefail`, wrapped in `caffeinate -i`)
- [ ] `scripts/vn.moonbase.crawlersocial.tick.plist` — 08:05 + 20:35,
      `LimitLoadToSessionType=Aqua`, `ProcessType=Interactive`, **no `KeepAlive`**.
      **One agent, not two** — the receivers agent is cut from v1 (§11)
- [ ] `launchctl bootstrap gui/$(id -u) …`; verify one real overnight run
- [ ] HUMAN verdicts must be **loud**: `osascript -e 'display notification …'`. A silent
      auth failure at 08:05 that you discover in November has cost you three months

**Done when:** it fires on schedule; `crawler status` shows the run; closing the lid over
a scheduled time and reopening produces exactly **one** deferred run, not two.

#### M10 — Hardening and the canary
**Goal:** you find out that Facebook changed the DOM in days, not weeks.

- [ ] **The canary**, `field_stats(run_id, field, seen, filled)`, written by core from
      `ParseResult.field_stats`. Alert when a field's fill rate falls below 50% of its
      trailing 10-run baseline over a run that saw at least 20 items. This works only
      because M4 made `is_pinned` tri-state; on a `bool = False` default its fill rate is
      structurally 100% and the alarm can never sound
- [ ] `ANALYZE` after the first substantial crawl and in the daily job — at low row counts
      SQLite picks a different index plus a `TEMP B-TREE FOR ORDER BY`
- [ ] The canonical timeline query text lives in a **named module constant**, never
      retyped, because a partial index only applies when the query repeats its predicate
      verbatim
- [ ] `sqlite3 data/social.db ".backup"` in the daily job
- [ ] `crawler doctor --repair` offers `INSERT INTO items_fts(items_fts) VALUES('rebuild')`

**Not here:** the absence sweep. Facebook claims `opaque` coverage on every envelope, so a
feed re-scan produces **zero absence evidence by design** and no Facebook item can ever
accumulate a strike from it. `rescan_window_days` is `0` for Facebook and upstream deletes
are simply not detected — a permanent property of an opaque transport, not a gap to close.
The sweep arrives with Telegram at M12, where coverage is exact and it actually works.

**Done when:** deliberately breaking the `is_pinned` selector in the parser and re-running
over fixtures raises a canary alert rather than silently writing zeroes; and
`EXPLAIN QUERY PLAN` on the canonical timeline query shows
`SEARCH items USING INDEX idx_items_recent` with no temp b-tree.

---

### Phase 2 — Telegram (connector #2)

Telegram second, not Reddit. Four reasons, in order of weight:

1. **Sequencing risk.** Reddit's credentials may simply not exist. Building the contract's
   first real test on a source that may never authenticate is unacceptable. Telegram's
   `api_id`/`api_hash` are self-issued with **no review queue** — which is why T0 exists at
   M0: the premise is checked on day one rather than assumed, because the self-service form
   does fail opaquely for some accounts.
2. **It tests the architecture where it was chosen.** Telegram is the exact case that
   settled the privacy design: one connector, one session file, emitting broadcast
   channels *and* private conversations. Building it second proves `containers.shape`,
   the two-file routing, the `enrolled_at` floor, the `CHECK` forcing `viewer_account_id`,
   and one `items` writer serving both shapes — at the earliest possible moment. If the
   design is wrong you find out at connector two, not connector four.
3. **Maximally different from Facebook** on every axis that matters: official API vs
   tolerated scrape, exact per-peer cursor vs approximate watermark, structured TL objects
   vs obfuscated DOM, conversations vs broadcast, encrypted store vs plain. Reddit differs
   from Facebook on transport but sits in the same broadcast half.
4. **Free**, and it is the source where browser automation would be most obviously wrong —
   the architecture's central thesis made concrete.

#### M11 — The private store and the governance layer *(no network)*
**Goal:** `data/private.db` exists, is encrypted, and nothing can write to the wrong file.

- [ ] `sqlcipher3` 0.6.2; `open_store(STORE_PRIVATE)` sets `PRAGMA key` **first**, then
      `cipher_memory_security=ON`, `secure_delete=ON`, `journal_mode=WAL`, then a
      `SELECT count(*) FROM sqlite_master` so a wrong key fails *there* and not later
- [ ] The **identical** DDL applied to both files by the migration runner — or M0's
      recorded fork if FTS5 is absent from the SQLCipher build, in which case
      `schema_versions` carries the divergence and `doctor` reports it as declared rather
      than as an error
- [ ] Re-run the object-count verification with `sqlcipher3` for `private.db`, so the
      77/77 claim in [./docs/DATA-MODEL.md](./docs/DATA-MODEL.md) §3 means what it says —
      the count in that document was produced with plain `sqlite3` on both files and
      therefore never exercised SQLCipher at all
- [ ] The resolved `GovernanceProfile` routes each envelope and each item to a file
      **before** any write. Conversation envelopes go to `private.db` too
- [ ] `propose_privacy()` in the contract; core resolves; config may only raise;
      `--force-tier` stamps `privacy_source='forced'` forever; unclassifiable → `conversation`
- [ ] `scrub()` + `assert_clean()` on every non-broadcast write
- [ ] `redactions` with `block_reingest=1` consulted by every ingest path
- [ ] `purge --person`, `forget <target>`, `vacuum` with the honest APFS-snapshot notice
- [ ] Retention sweep: a pure function of the clock, run at the end of every tick and the
      start of every export, frozen-clock tested. **It prints its plan and deletes nothing
      until `retention.confirmed: true` is set**, and enrolling a conversation target
      refuses without an explicit `retention_days`

**Done when:** `crawler targets add` refuses to enrol a conversation target with FileVault
off, and refuses again without an explicit retention answer; a `MATCH`-based test (never
`count(*)`) proves no conversation row is reachable from `social.db`'s FTS index; the
retention sweep on an unconfirmed config prints a plan and deletes zero rows; and the
redaction test passes — redact a person, crawl a fixture that still contains them, assert
**zero** rows re-created.

#### M12 — The Telegram connector *(pull only)*
**Goal:** channels and DMs, one connector, one persistence path, zero branching.

- [ ] Telethon pinned `==1.44.0` + `cryptg>=0.6.0`, installed from **PyPI, never a GitHub
      git URL** — the GitHub repo was archived 2026-02-21 and development moved to
      `codeberg.org/Lonami/Telethon`, whose README says GitHub "may be deleted in the
      future". Vendor the wheel. Every Telethon symbol confined to
      `connectors/telegram/` so a Kurigram swap is a one-directory change — noting that
      Kurigram is a Pyrogram-shaped API, so the swap is "one directory to rewrite", not
      "one directory to drop in"
- [ ] **Poll, do not listen.** The update stream is explicitly gap-tolerant —
      `UpdatesTooLong` / `ChannelDifferenceTooLong` mean "I will not enumerate what you
      missed, go re-read history" — and `MessageDeleted` is documented unreliable. A
      listener never removes the id-cursor reconciler, it only makes it find less, while
      adding an always-on process and real `AUTH_KEY_DUPLICATED` exposure
- [ ] Cursor: `iter_messages(entity, reverse=True, offset_id=last_id, wait_time=2.0)` at
      the library's fixed 100-per-request chunk, plus a bounded ids-refresh window (500 ids
      broadcast, 2,000 or 30 days conversation) because edits, views and reactions mutate
      and Telegram offers no "changed since" query
- [ ] `Cap.DELETE_EVENTS` is **deliberately not set**, so the absence sweep stays mandatory
- [ ] **The absence sweep arrives here**, gated on `containers.access_state='ok'` **and**
      `runs.status='ok'`. This is the first connector whose coverage claims are `exact`, so
      it is the first connector where the sweep can actually accumulate evidence
- [ ] `entities` stored **raw**, never flattened to markdown at ingest — they are UTF-16
      code-unit offsets and flattening is a lossy parse you cannot undo
- [ ] Read-only enforced by absence: the connector imports no send/forward/join method and
      a unit test asserts it by AST-walking the package. `iter_dialogs()` confined to an
      interactive `crawler telegram list-chats` that only **prints** candidates
- [ ] `classify()`: `FloodWaitError.seconds` → `WAIT(retry_after=n)` obeyed exactly, and if
      it exceeds 300s checkpoint the cursor and exit 75 for the next tick;
      `PeerFloodError` → `STOP` (account-wide, no defined duration, cannot be waited out);
      `AuthKeyDuplicatedError` / `SessionRevokedError` → `HUMAN`
- [ ] `Cap.SINGLE_FLIGHT` + flock on the `.session` path, outside any synced directory
- [ ] Machinery that arrives with this phase: `item_versions`, the absence/tombstone
      sweep, zstd + trained dictionaries (msgpack slices are exactly the small-payload case
      where 5.5x beats zlib's 1.8x)

**Done when:** one tick writes a channel post to `social.db` and a DM to `private.db`
in the same run, **with the DM's envelope in `private.db` too**; an edited message produces
a second `item_versions` row and no lost text; a `--limit` run leaves the cursor untouched;
and the read-only test passes.

---

### Phase 3 — Reddit (connector #3)

#### M13 — Reddit, forking on R0's answer
**Goal:** the coverage/gaps machinery does real work for the first time.

**If R0 was approved:**
- [ ] `prawcore` for OAuth token minting and refresh, `httpx` for the wire, so response
      bytes survive into the raw store. **Never PRAW in the ingest path** — it parses
      responses into model objects and discards the bytes, which breaks raw-first outright.
      PRAW stays a dev-only convenience, with a test asserting `'praw' not in sys.modules`
      after a tick
- [ ] Always `raw_json=1`; code-grant refresh token, not password grant (stores no
      password, survives 2FA)
- [ ] Durable `created_utc` watermark + disposable fullname page hint in `runs.params`, with
      a 6-hour re-read overlap
- [ ] The ~1000-item listing wall becomes a `gaps` row with `reason='listing_cap'`, written
      **by core** from the connector's coverage claim — the connector contributes zero
      gap-detection code. That is the payoff of the coverage design
- [ ] Bounded comment expansion at 32 `morechildren` calls per post, `more_remaining`
      recorded. Deep or old threads route to Arctic Shift's one-call tree endpoint at the
      cost of ~36h-stale scores, tagged `source_kind='archive'`
- [ ] `on_upstream_delete='follow'`, locked; the CLI override is refused with a pointer to why
- [ ] `retention_days` must be set explicitly for Reddit targets rather than inheriting the
      broadcast "forever" default
- [ ] Metric re-observation on a decaying schedule (+1h/+6h/+24h/+72h/+7d, **then stop**),
      batched through `/api/info` so 3,000 tracked posts cost ~30 requests, not 3,000

**If R0 was declined or ghosted past its stated window:**
- [ ] Ship the named degraded transport instead: authenticated Atom feeds (`user=&feed=`
      from `/prefs/feeds`) for live-edge discovery, plus Arctic Shift for bodies, comments
      and metrics. Set `sources.transport='feeds'`
- [ ] **No three-way abstraction is built.** It is one backend or the other, chosen once

**Done when:** a daily run over the configured subreddits writes items and, when it hits
page ten with the oldest item still newer than the watermark, writes a `gaps` row and does
**not** advance `complete_since`; and a gap-drain run fills it from Arctic Shift with
`source_kind='archive'` on every metric it supplies.

---

### Phase 4 — X (connector #4), the free half first

#### M14 — The X data-archive importer
**Goal:** your own complete history and your own DMs, free and at zero risk.

- [ ] **Use the ZIP you requested at M0/X0.** If more than a week has passed, the download
      link has expired and you request it again — ~24h, and the 7-day expiry is the trap
- [ ] Paste the real `unzip -l` manifest into
      [./docs/sources/x.md](./docs/sources/x.md) §2.3 **before** writing `parse()`, and
      read `TheExGenesis/community-archive`'s parser as prior art
- [ ] `Cap.FILE_IMPORT`: `fetch()` over a local path. The ZIP **is** the payload — it lands
      perfectly on raw-first
- [ ] One envelope per DM **conversation**, not per part-file, so `crawler forget
      <conversation>` and `purge --person` delete whole envelopes rather than orphaning
      parsed rows whose raw bytes still contain the person
- [ ] This is the **only** path to your own X DMs. The DM Events API is never called: the
      archive is free, complete (no 3,200-post ceiling), zero ToS risk, and has far better
      provenance — an export you personally requested for your own account, versus an OAuth
      scope that could be pointed anywhere
- [ ] DM containers route to `private.db` like any other conversation. It is easy to
      overlook because X reads like a broadcast source right up until the DM folder lands
- [ ] This proves the `Cap.FILE_IMPORT` shape, which Facebook and Zalo will both want later

**Done when:** importing the ZIP twice produces one set of items; your own timeline history
predates the 3,200-post ceiling; and DM rows are unreachable from `social.db`.

#### M15 — The X REST connector, behind `enabled: false`
**Goal:** third-party timelines, with money as a first-class budget.

- [ ] Ships **disabled**. The published prices are `[verified]` from `docs.x.com`, but
      `console.x.com` is the billing authority — reconcile there before it is switched on
- [ ] `usage_counters(source, period, metric='cost_micros', value)` checked **before** the
      run starts, and a hard spend cap set in the X console *as well as* locally — so a
      unilateral repricing cannot produce a surprise bill. **`spend_cap` is typed as a
      dollar ceiling**, not derived from a price this plan believes
- [ ] Defaults are all spending decisions: `exclude=retweets`; **replies OFF** (thread
      reconstruction bills every reply, so one viral post with 2,000 replies is ~$10 and the
      cost scales with other people's engagement, which you do not control); backfill capped
      at 200 posts/account (the full 3,200 is ~$16/account, ~$320 for 20 accounts — a real
      number this plan states out loud); `--limit N` bounds **posts fetched**, because that
      is the billable unit
- [ ] `Cap.MUTABLE_METRICS` is **not** set for X: re-observing a metric costs $0.005 per
      post per refresh, so metrics are captured once at ingest and never refreshed. This is
      the largest fidelity concession in the connector and it is purely economic
- [ ] Scheduled at 01:00 UTC with all retries bounded by the UTC day
- [ ] An integration test asserts the pagination loop terminates against a mocked infinite
      cursor. This is the one connector where a loop bug costs dollars rather than time
- [ ] A test asserting **the spend governor reads the same metric the ingest path writes**
      (`cost_micros`, both sides). A governor reading `reads` while ingest writes
      `cost_micros` silently sees a month-to-date of zero

**Done when:** a dry run reports projected spend **and prints the per-post price it read
from the console** alongside the projection, before spending anything; the monthly counter
hard-stops a run at the cap with `STOP`, not a warning; and the infinite-cursor test is
green.

---

### Not a phase — Zalo

Deferred, with the slot specified and every trigger written down (§11). What ships **now**
is: the three transports declared as separate entries under one source (`zalo.oa` /
`zalo.bot` / `zalo.user`) with honest capability notes; the `user_withdraw`-shaped purge
path, built generically for every source; and the E2EE-aware
`containers.content_unavailable` flag so the archive can say "thread exists, content not
retrievable" instead of silently looking complete. Full evidence in
[./docs/sources/zalo.md](./docs/sources/zalo.md).

---

## 7. Scheduling reality

Headful Chrome needs a GUI session. Concretely:

- **Works:** Mac awake and logged in, screen locked. A `LaunchAgent` in
  `~/Library/LaunchAgents` runs in your Aqua session and can drive a real window.
- **Does not work:** logged out, shut down, or `cron` (no GUI session).
- **Sleep:** a sleeping Mac won't run it. `caffeinate -i` wraps the job so it cannot sleep
  mid-session; schedule inside your waking hours. Do **not** use `pmset repeat wake` —
  waking a Mac at 03:00 to scroll Facebook is the exact opposite of the pacing story.

Schedule it at an hour a human might plausibly be scrolling — 08:05 and 20:35 local, not
03:47. Add jitter so it isn't the same minute every day.

**The launchd behaviour that shapes the whole design:** launchd defers a missed
`StartCalendarInterval` firing until wake and **coalesces** multiple misses into a single
invocation. So a closed lid delays the job rather than losing it, and a week away yields
one run, not seven. This is why the daily job must catch up **by cursor** and never by
"fetch yesterday" — and why coalescing is a feature here, not a bug.

**One agent, not two.** An earlier draft installed a second always-on `receivers`
LaunchAgent "empty at v1" so its `KeepAlive` semantics would be proven before anything
depended on them. It is cut: it had zero producers, nothing tested it, and an always-on
process with no job is a failure surface with no benefit. The design for it survives in
[./docs/DECISIONS.md](./docs/DECISIONS.md) ADR-0036 for the day a push transport actually
arrives.

**Not every connector needs the GUI lane.** Reddit, X and Telegram finish in seconds to
minutes with no browser. They ride the same `tick` agent purely because one process
writing SQLite is worth more than lane purity. An optional third `StartInterval=900`
batch-lane agent for fresher Reddit is deferred with a trigger (§11).

---

## 8. Pacing defaults (Facebook)

Start here and only loosen if a week is clean. These are cheap; a flagged account is not.
**Numbers unchanged from the original plan** — they become the `facebook` entry in the
shared rate config, and no code branches on the fact that they are a paranoia budget
rather than a published quota.

| Knob | Default |
|---|---|
| Scroll dwell | 2.5–6.0 s, jittered, eased |
| Post-open dwell | 4–12 s |
| Between targets | 60–180 s |
| Posts per session | 200 |
| Session length | 25 min max, then quit the driver |
| Sessions per day | 2 |
| Backoff on soft block | 15 min → 1 h → 4 h, then stop |
| Hard block | stop everything, alert, **no retry** |
| Profile age before a Page target | **≥ 3 days** |
| Profile age before a group target | **≥ 14 days** |

Facebook publishes no rate-limit headers, so this budget has **no feedback signal**. If
the numbers are too aggressive you find out by getting checkpointed, not by a 429. That
asymmetry is unmitigable; the only defence is starting conservative. Every other connector
gets a bucket corrected by real response headers or by a server-supplied wait.

**Loosening protocol:** run a full week at these values with zero walls of any kind, then
change **one** knob by at most 25%, then run another clean week. If any wall appears,
revert immediately and do not re-try that change for a month.

---

## 9. First-run sequence

Every command below is a row in [./ARCHITECTURE.md](./ARCHITECTURE.md) §11.

```bash
cd ~/dev/crawler-social
uv sync --all-groups

# ---- preflight (M0) -------------------------------------------------------
fdesetup status                                  # must say FileVault is On
uv run crawler init-keys                         # pepper + private.db key -> Keychain
uv run crawler doctor                            # sqlite>=3.45, FTS5-in-SQLCipher,
                                                 # chrome+driver versions, keychain, paths,
                                                 # facebook profile age

# ---- Facebook (M2, M5) ----------------------------------------------------
uv run crawler login --source facebook           # visible window; type password + 2FA yourself
                                                 # ...then USE this profile by hand for 3+ days
uv run crawler targets add fb:https://www.facebook.com/vnexpress --slug vnexpress
uv run crawler crawl --source facebook --target vnexpress --limit 20

sqlite3 data/social.db "
  SELECT local_day, source, substr(COALESCE(title,text),1,60)
    FROM items ORDER BY published_at DESC LIMIT 10;"

# ...after 14 days of profile age, groups become available:
uv run crawler targets add fb:https://www.facebook.com/groups/123456789 --slug my-group

# ---- schedule (M9) --------------------------------------------------------
cp scripts/vn.moonbase.crawlersocial.tick.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/vn.moonbase.crawlersocial.tick.plist
uv run crawler status

# ---- Telegram, later (M11, M12) -------------------------------------------
uv run crawler login --source telegram           # phone -> code -> 2FA, once, interactive
uv run crawler telegram list-chats               # PRINTS candidates. Does not enrol anything.
uv run crawler targets add tg:@somechannel       # -> social.db
uv run crawler targets add tg:@someone \
      --ack-third-party --retention-days forever # -> private.db, forward-only from now
uv run crawler search --private "hợp đồng"       # separate command, separate file, on purpose

# ---- X archive, later (M14) -----------------------------------------------
uv run crawler import --source x --file ~/archives/twitter-2026-09-01.zip
```

Three things in that sequence are load-bearing and easy to skip. `crawler doctor` before
anything else, because a uv-managed interpreter's bundled SQLite is not the one your
system CLI links, and you want to know that on day one rather than at the first JSONB
query. The three-day gap between `login` and the first `crawl` — the profile is the
credential and its age is an asset. And `telegram list-chats` **prints**; it does not
enrol. There is no command that enrols conversations in bulk.

---

## 10. What breaks first

Ranked by likelihood × how long it takes you to notice.

1. **Facebook DOM selectors.** Obfuscated class names rotate on Meta's deploy cadence.
   Mitigated by attribute/structure selectors, the M10 canary, and raw-first storage —
   fix the parser and re-parse history instead of re-crawling it. *This is the failure the
   whole architecture is built around.* The specific one to watch is **`is_pinned`**: if
   that badge selector rotates, the watermark stop rule fires on scroll one and the crawler
   quietly stops collecting while reporting success. That is why `is_pinned` is tri-state
   (M4), why the canary tracks it by name (M10), and why SUSPECT rule (c) exists (M3).
2. **Timestamps.** The most commonly silently-wrong field on every source. `published_prec`
   exists so a downstream query can tell a real timestamp from a guess. Never write an
   exact-looking integer for a "3h ago".
3. **The Telegram `.session` file.** `AUTH_KEY_DUPLICATED` is permanent and is caused by
   the most ordinary thing imaginable: a copied file, a cloud-synced directory, or an
   overlapping run. Flock every run, keep the file outside any synced directory, one
   session per machine, never copy.
4. **Reddit credentials that never arrive.** Not a bug — an external dependency you do not
   control. Which is why R0 is a day-one form, not a milestone, and why the degraded
   `feeds+archive` transport is specified before it is needed.
5. **Silent, unannounced platform tightening.** Reddit's 2025–2026 pattern alone: robots.txt
   (Mar 2025), Wayback Machine (Aug 2025), self-service registration (Nov 2025),
   unauthenticated `.json` (May 2026), `old.reddit` login (Jun 2026), RSS rate limits
   (Jun 2026). Every path this tool depends on should fail **loudly** and be re-verified on
   a schedule, not assumed stable.
6. **Private group access.** Leaving the group, or an admin changing settings, looks
   identical to a block. The wall classifier must distinguish them or you will chase
   ghosts — and `containers.access_state` must gate the absence sweep, or one checkpoint
   tombstones four thousand posts.
7. **Facebook deletions are simply invisible**, permanently. An `opaque` coverage claim
   contributes no absence evidence, so a Facebook post deleted upstream stays `visible` in
   your archive forever. This is a property of the transport, not a bug to fix, and it is
   the one place where Facebook is strictly worse than every other connector here.
8. **Selenium / BiDi / CDP churn.** CDP is deprecated and slated for removal in Selenium
   5.0; BiDi is still moving and does not yet expose response bodies at all. Pin
   `selenium` and `seleniumbase`, upgrade deliberately, and keep all driver construction
   in one module.
9. **Chrome auto-update vs driver version skew.** `doctor` checks it.
10. **Media as an unbounded cost.** Text history of even a huge channel is tens of MB; the
    same channel with media is potentially hundreds of GB, and each download also spends the
    flood budget. `media_mode` defaults to `link` everywhere for exactly this reason. The
    anti-trigger to state plainly: never enable download globally.
11. **Storage growth, with the real arithmetic.** The growth term is **Facebook HTML
    snapshots**, not metric re-observation. A 200-post session is roughly 20 scroll-step
    envelopes at ~50 KB compressed each ≈ **1 MB per session, ~2 MB/day/target** — call it
    ~0.7 GB per target per year *(estimate, not a measurement; check it against query A11
    after a month)*. By contrast Reddit's metric refreshes are ~30 batched `/api/info`
    envelopes a day, ~11k rows a year, and the decaying `+1h/+6h/+24h/+72h/+7d` ladder
    **stops**, so nothing is re-observed daily forever. An earlier draft claimed "over a
    million envelope rows a year" from metric refreshes and used it to justify a deferred
    compaction pass; that figure was about a hundred times too high and contradicted this
    plan's own budget table, and the compaction pass has been dropped (§11).

---

## 11. Deferred, with triggers

Everything specified but not built at v1, one row each, with the trigger that unblocks it
and — where one exists — the anti-trigger that must not. **If an item is not in this table,
it is not deferred; it is not in the design.**

| Deferred item | Trigger | Anti-trigger |
|---|---|---|
| **Arctic Shift bulk `.zst` dumps pulled to local disk** | An unfilled `gaps` row older than 30 days, **or** Arctic Shift's API becoming unreliable | — |
| **zstd + trained dictionaries** (`zdict`) | Connector two lands (Telegram). `tg.slice` msgpack is exactly the small-record case where 5.5× beats zlib's 1.8× | — |
| **Projection A/B diffing** (`--rebuild items --into items_v8`, diff fill rates, then promote) | The second Facebook parser rewrite after a DOM rotation | — |
| **Push transports: `Cap.PUSH`, `core/spool.py`, `core/receivers.py`, the `Receiver` Protocol, the receivers LaunchAgent** | The first real push producer exists. There is none today: Telegram listen mode is itself deferred, and all three Zalo transports are deferred or impossible | Do not install the agent "empty so its semantics are proven" — nothing tested it, and an always-on process with no job is a failure surface with no benefit |
| **Telegram listen mode** | Nightly latency is genuinely not good enough | **Never run a listener and a pull run against the same `.session`** — that is the `AUTH_KEY_DUPLICATED` gun, loaded |
| **The optional `StartInterval=900` batch-lane agent** (fresher Reddit) | A 12-hour Reddit lag actually annoys you | It costs a second flock-contention path |
| **`media_mode='download'`** | A named target whose media you want, **with `--mime` and `--max-bytes` set in the same command** | **Never enable it globally.** Media is the only unbounded cost in the project |
| **Per-source detail tables** | One source adds more than eight fields queried *together* on a hot path. None of the five currently does | The generated-column promotion path is verified and is one `ALTER TABLE` |
| **Cross-platform identity linking** (`persons` / `author_person_links`) | You actually want to link two accounts, by hand | Automated/inferred matching needs a schema migration **and** an ADR. Vietnamese given-name distributions make name-based matching near a coin flip |
| **Out-of-process connectors** | A Zalo Node sidecar, or a connector that hangs past its timeout | The exit-code table is already the process-level projection, so this costs no redesign |
| **`zalo.bot`** | You want a **new** Zalo inbox archived going forward and accept that **no history comes with it** | — |
| **`zalo.oa`** | You register a *hộ kinh doanh* or a company and verify an OA on a paid tier | It still never reaches your personal chats |
| **`zalo.user`** (zca-js) | You explicitly ask **and** have a secondary account you can afford to lose. Hand-edited config flag plus `--i-accept-ban-risk`; never started by the scheduler | **Never on the primary account** |
| **Zalo manual `.zip` import** | Someone demonstrates Zalo PC's *"Xuất dữ liệu"* archive is machine-readable (§12, Z1) | — |
| **Selenium against `chat.zalo.me`** | **No trigger. Rejected permanently** | — |
| **Any scraping path for X** | **No trigger. Rejected permanently, with dates** | — |
| **Splitting into five `uv` projects** | An actual dependency conflict between two connectors, or the tool needing to run on a second machine | Not aesthetics |
| **Python 3.14** (makes `compression.zstd` stdlib) | Telethon 1.44 confirmed green on 3.14 beyond its CI commit, on the actual project interpreter | — |
| **Full-archive X search** (`/2/tweets/search/all`) | Availability confirmed at `console.x.com`, **and** you want third-party history beyond the ~3,200 ceiling, **and** accept $0.005/post | — |
| **X reply / thread reconstruction** | Explicit per-post opt-in with a max-replies bound | **Never a global flag.** Cost scales with other people's engagement |
| **A compaction pass over byte-identical envelopes** | **Dropped, not deferred.** It was sized against an envelope count ~100× too high (§10 item 11). The retention sweep plus `envelopes.purge_after` already bound the store | — |

---

## 12. Verify before building

Every unverified or second-hand claim in this document set, consolidated. **Nothing here is
allowed to become a stated fact anywhere else without being checked first**, and each row
says how to check it and roughly how long that takes.

Confidence labels are uniform across all documents: `[verified]` = read from a primary
source, `[likely]` = multiple consistent secondary sources with no primary, `[unverified]`
= must be confirmed before it is relied on.

### A. Day one — fire these before writing code

| # | Claim | Status | How to check | Time | Blocks |
|---|---|---|---|---|---|
| A1 | A personal, non-commercial, read-only Reddit app is approvable under the Responsible Builder Policy | `[unverified]` | **R0.** Submit the form. There is no other way to know | 20 min + ~7 days waiting | the whole Reddit connector |
| A2 | You can actually obtain a Telegram `api_id` at `my.telegram.org` | `[unverified]` — the form is reported to fail opaquely for some accounts | **T0.** Create an application; store the pair in Keychain | 15 min | the whole Telegram connector, and the connector-#2 slot |
| A3 | The X export ZIP's internal layout — file names, directory structure, the JS-assignment wrapper | `[unverified]` | **X0.** Request the archive, then `unzip -l`. **Link expires 7 days after generation** | 5 min + ~24h waiting | all of M14 |
| A4 | The Facebook profile is warm enough to drive | n/a — policy, not a fact | **P0.** `crawler doctor` reports profile age from the user-data-dir mtime | 10 min + 3 to 14 days | M2 (Page), M5 (group) |
| A5 | Reddit registration deadlines: **2026-09-30** (register an existing app) and **2026-12-31** (Migration Program opt-in) | `[likely]` — second-hand from the 2026-08-05 r/redditdev post | Read the r/redditdev post directly in a browser. **Both are dated checkboxes in M0** | 10 min | optionality into 2027 |

### B. Blocks a design decision

| # | Claim | Status | How to check | Time | Blocks |
|---|---|---|---|---|---|
| B1 | SQLCipher's vendored build in `sqlcipher3` 0.6.2 has **FTS5** compiled in | `[unverified]` | `SELECT * FROM pragma_compile_options()` on a `sqlcipher3` connection. **Two written branches** — see DATA-MODEL §14 item 1 | 15 min | the identical-DDL invariant; M11 |
| B2 | SeleniumBase CDP Mode yields **decodable** Facebook GraphQL response bodies (the API is verified; whether it works against Facebook's flow, timing and body encoding is not) | `[unverified]` | The M2 spike, timeboxed to half a day | 4 h | whether `fb.graphql.feed` exists at all |
| B3 | Whether a **public Telegram channel's history reads without joining** | `[unverified]` — sources ambiguous | Resolve one throwaway public channel, `iter_messages(limit=5)` without joining | 15 min | whether the tool must ever perform a join (medium-risk) |
| B4 | Whether X pay-per-usage unlocks full-archive search (`/2/tweets/search/all`) | `[unverified]` — docs say "Self-serve or Enterprise", a vendor blog says 7-day only. **Direct contradiction** | `console.x.com` | 10 min | nothing at the default; decides whether `timeline_ceiling` gaps are ever fillable |
| B5 | Whether a Python MTProto client exposes **re-serializable raw TL wire bytes** | `[unverified]` | Inspect Telethon's object API during the M12 spike. **`tl_json`/msgpack is the safe default and is what the schema assumes** | 30 min | `content_type` for `tg.slice` only |
| B6 | Zalo OA `listrecentchat` / `conversation` resolve at `/v3.0/` as third-party SDKs assume, or only at the documented `/v2.0/` | `[unverified]` | Test both against a live token | 15 min | only if `zalo.oa` is ever built |

### C. Changes a number, not the design

| # | Claim | Status | How to check | Time |
|---|---|---|---|---|
| C1 | Reddit free tier is ~100 QPM over a ~10-minute rolling window | `[likely]` — consistent across independent 2026 sources, never seen in Reddit's own words | Read live `X-Ratelimit-*` headers on the first authenticated request. The `respect_headers` loop makes a wrong prior self-correcting | 1 min |
| C2 | `/api/info?id=…` accepts ~100 fullnames per call | `[likely]` | One request with 100 ids; count what comes back | 2 min |
| C3 | Which OAuth scopes reach a private subreddit you belong to (`read` + `mysubreddits` + `history`) | `[unverified]` | Mint one token, try one private sub, record the working set in `accounts.extra` | 20 min |
| C4 | Reddit still vote-fuzzes displayed scores | `[unverified]` | Fetch one post's score 5× in a minute; compare. **Set `approximate=1` either way** — it costs one bit and cannot be retrofitted onto history | 5 min |
| C5 | `syntax=cloudsearch` with `timestamp:START..END` still functions | `[unverified]` — PRAW exposing the parameter proves nothing about the server | One query. If it works it is a bonus fast path; **nothing depends on it** | 10 min |
| C6 | `/api/morechildren` still returns nested `more` placeholders, and its per-request child cap | `[unverified]` — community docs only | One call. The schema handles either behaviour via `parent_ref` | 10 min |
| C7 | Arctic Shift's coverage completeness for *your* target subreddits | `[unverified]` — one volunteer's free service, explicit "no uptime guarantees" | `GET /api/time_series?key=r/<sub>/posts/count&precision=day` vs your own counts | 20 min |
| C8 | Current Reddit RSS rate limit and the `user=`/`feed=` workaround | `[unverified]` — a community finding, not documented; cut silently around 11–12 June 2026 | Only matters if R0 fails | 15 min |
| C9 | Whether X `expansions` bill as separate resources, per-instance or per-unique | `[unverified]` — if per-instance, costs roughly triple | One request with `expansions=author_id`, then **read the credit ledger** | 20 min |
| C10 | Whether X media objects (`expansions=attachments.media_keys`) are billed | `[unverified]` — no media line item exists, which *suggests* free-with-the-post, but absence of a line item is not an exemption | Credit ledger | 20 min |
| C11 | X minimum credit purchase / top-up floor | `[unverified]` | `console.x.com` | 5 min |
| C12 | X monthly post-read cap: 3,000,000 (official docs) vs 2,000,000 (vendor blogs) | official `[verified]`, the conflict `[unverified]` | Irrelevant at ~250× headroom — recorded because it proves vendor blogs are unreliable on X pricing | — |
| C13 | Current X rate-limit values | `[verified]` as of 2026-09-01, but the doc carries no revision date | Response headers on the first live request. Four orders of magnitude of headroom | 1 min |
| C14 | Every X price figure, reconciled against the billing authority | `[verified]` from `docs.x.com` (two independent recon fetches, 2026-09-01) — but the **console is what bills you** | `console.x.com`, before `enabled: true`. The dry run prints the console price next to the projection | 15 min |
| C15 | Telegram takeout's flood-limit multiplier (`wait_time=0.5` is a guess, not a budget) | `[unverified]` — Telethon says only "some calls will have lower flood limits" | Instrument the first backfill and adjust | during M12 backfill |
| C16 | Telethon 1.44.0 runs clean on Python 3.13 (its `python_requires='>=3.5'` and 3.8-max classifiers are stale metadata) | `[unverified]` | Smoke-test on the actual uv-managed interpreter before pinning | 10 min |
| C17 | Whether `iter_messages` sends `messages.readHistory` | `[unverified]` | Read one unread channel, then check the unread badge in the Telegram app. If it does, the archive is visibly mutating account state | 10 min |
| C18 | Telegram `file_reference` validity duration | `[unverified]` — the error and the refresh path are documented; the duration is not | **Do not introduce a dependency on it.** Download at ingest or store nothing | — |
| C19 | Telegram delete-event replay to a session that was offline | `[unverified]` | Observe during M12. The design assumes it does not and keeps the sweep mandatory | 30 min |
| C20 | Telegram message-id namespace for private chats and basic groups (per-chat vs per-account) | `[unverified]` — sources conflicted | Irrelevant to correctness: `(container_id, platform_item_id)` is the key either way. **Do not write a sentence asserting the finer rule** | — |
| C21 | Bot API file limits (20 MB `getFile`, ~2 GB self-hosted) and user limits (2 GB / 4 GB Premium) | `[likely]` — secondary sources | Only matters if media download is ever enabled | 10 min |
| C22 | Facebook's `?sorting_setting=CHRONOLOGICAL` still sorts group feeds | `[likely]` — community-established, never documented by Meta | One manual load. If it stops working the stop rule degrades but does not break | 5 min |
| C23 | Every DOM selector and GraphQL operation name in facebook.md §8 | `[unverified]` — no request to facebook.com was made during planning | The M2 spike fixtures. **Record what you observe; do not trust the document** | in M2 |
| C24 | Whether `get_response_body` returns Facebook bodies base64-encoded and/or compressed | `[unverified]` — the SeleniumBase maintainer states byte-level decompression is out of scope | The M2 spike. **This is the most likely way candidate B fails in practice** | in M2 |
| C25 | macOS Keychain ACLs (`security -T <binary>`) meaningfully restricting a `uv run python` caller | `[unverified]` | Test with a second Python process. **If they do not hold, say so** rather than implying protection you do not have | 15 min |
| C26 | macOS 26.x behaviour of `LimitLoadToSessionType`, and whether the plists actually load | `[unverified]` — the launchd.info guidance predates recent releases | `launchctl bootstrap`, then `launchctl print` | 10 min |
| C27 | `local_day STORED` and FTS5 `remove_diacritics 2` survive the **uv-managed** interpreter's bundled SQLite | `[verified]` on pyenv 3.13.13 + SQLite 3.51.0; **not** on uv's | `crawler doctor` on the project interpreter before the first migration | 2 min |
| C28 | Telethon's PyPI release cadence post-archive, and whether the maintainer keeps publishing from Codeberg | `[unverified]` | Watch. v1.44 (2026-06-15) and "maintenance mode" are confirmed; future availability is not | — |
| C29 | Kurigram as a drop-in Telethon replacement | **false as usually stated** — it is a Pyrogram fork with a Pyrogram-shaped API | The one-directory containment rule makes the swap *bounded*, not *free*. Budget a rewrite of that directory | — |

### D. Legal and policy — carried as-is, never asserted

| # | Claim | Status | Note |
|---|---|---|---|
| D1 | Meta's ToS clause on automated collection | `[likely]` — quoted second-hand; the prohibition itself is not in doubt | Read `facebook.com/legal/terms` **by hand**, not via the tool |
| D2 | Reddit's Data API Terms deletion obligation — the "48 hours" figure and the "even if disassociated, de-identified or anonymized" wording | `[unverified]` — `support.reddithelp.com` returned 403, `redditinc.com` and `web.archive.org` blocked | Read first-hand **before** fixing Reddit's retention default in code. Ship `follow`-locked regardless; it is the conservative default |
| D3 | Reddit commercial pricing ($0.24/1k calls; ~$12,000 minimum, sources disagree monthly vs annual) | `[unverified]` — Reddit publishes no rate card | **Do not quote these.** Recorded so they are not repeated as fact |
| D4 | Telegram's API ToS licence wording ("retractable, limited, non-exclusive…") and the `recover@telegram.org` recourse | `[unverified]` — search extracts only | Read `core.telegram.org/api/terms` and `/api/obtaining_api_id` in a browser |
| D5 | Telegram's ML-training prohibition **and its narrow consent exception** | `[likely]` — corroborated across two Telegram ToS pages via search; bodies not fetched | **Load-bearing if any LLM use is contemplated.** Re-read before relying on the exception. Stamped into every export manifest |
| D6 | Vietnam: Decree 13/2023 repealed 2026-01-01; operative instruments are Law 91/2025/QH15 and Decree 356/2025/NĐ-CP | `[verified]` across DLA Piper, Tilleke, DFDL, Rajah & Tann | Any document citing Decree 13 is citing a repealed rule |
| D7 | Whether the PDPL contains a purely-personal/household exemption | `[unverified]` at article level | **Design as though none applies.** The tool stores third parties' messages either way |
| D8 | Zalo's specific ToS clause on automated access / scraping | **not located** | **Do not assert what Zalo's ToS says.** The claim that unofficial clients violate Zalo policy comes from the libraries' own disclaimers and community consensus |
| D9 | Current legal status of the Nitter cease-and-desist (2026-08-24 letter, 2026-08-25 deadline) | `[unverified]` — as of 2026-09-01 nitter.net was offline and the maintainer was seeking counsel; no filed lawsuit reported | The rejection in x.md §1 stands either way |
| D10 | `KeepAlive.NetworkState` | `[verified] locally` as documented *"no longer implemented"* in `man 5 launchd.plist` on macOS 26.5.2 | Moot at v1 — the receivers agent is cut (§11) |

### E. Zalo — the whole document is a verify list

Zalo's documentation quality is poor enough that acting on an unchecked claim is a real
risk. All sixteen items live in [./docs/sources/zalo.md](./docs/sources/zalo.md) §12. The
three worth doing even while Zalo is deferred:

| # | Claim | Status | How to check | Time |
|---|---|---|---|---|
| Z1 | Whether Zalo PC's *"Xuất dữ liệu"* `.zip` is genuinely encrypted / re-import-only | `[unverified]` — one Vietnamese guide says so | **Five minutes of your own hands-on checking.** This is the highest-value unknown in the Zalo document: if the ZIP is readable, Zalo goes from "experimental scraper" to "file importer" and the whole risk picture changes | 5 min |
| Z2 | Whether an individual with no business registration can complete Zalo Bot Platform signup end to end | `[unverified]` — the pricing page says "cá nhân & doanh nghiệp"; identity requirements unchecked | Attempt the signup. It is free | 20 min |
| Z3 | Whether OA verification accepts a *hộ kinh doanh* registration rather than a full company | `[likely]` — trade sources say yes; not confirmed against Zalo's own current policy | This is the difference between "unreachable" and "a cheap afternoon of paperwork" | 20 min |

---

## 13. Open questions for you

Four questions the original plan left open are **answered as frozen defaults** so nothing
downstream re-opens them, and each answer names what it costs:

| Was open | Frozen answer | What it costs |
|---|---|---|
| Comments — worth the clicks? | **Yes, but bounded.** 32 `morechildren` calls per Reddit post; `more_remaining` recorded so a partial tree is a declared state rather than a silent lie | More browser time on Facebook and a second envelope per post whose comments need their own navigation |
| Media — URLs or files? | **Link-only** for all classes at v1, per-target `download` opt-in behind a mime allowlist and `max_bytes` | A stored URL is a **dead pointer** for Telegram (`file_reference` expires), Zalo (token-bearing `*.zdn.vn` paths) and Facebook. The replay guarantee covers **structured content only** |
| History depth on first crawl? | **`--since 90d`** by default, not "everything" | An unbounded scroll of a large group is the riskiest thing you can do on day one |
| Dedicated account or your main one? | **Your own**, with §3's risk statement standing | Account exposure, priced in by the §8 pacing defaults |
| **Conversation retention** *(newly frozen, and the one most likely to bite)* | **No default.** `conversation.retention_days` is `null` and must be set consciously; raw bodies expire at **90 days**; the first destructive sweep needs `retention.confirmed: true` | You will be asked for a number when you enrol your first DM target. `forever` is a legal answer. The cost of the old 180-day default was the archive itself |

**What is genuinely still yours to decide:**

1. **Do you want a Facebook backfill at all, and how deep?**
   *Default:* `--since 90d`.
   *What changes:* anything past 90 days is a multi-hour scroll on an account you care
   about, and it is the single riskiest operation in the project. If you want it, it
   becomes a supervised one-sitting `crawler backfill` run rather than anything the
   scheduler ever touches, and the 14-day profile threshold becomes a hard prerequisite
   rather than a soft one. If you don't, M5 ships as written and nothing else moves.

2. **Which Telegram conversations, by name?**
   *Default:* none — you must name them.
   *What changes:* this list **is** the scope of the entire privacy problem. Forward-only
   enrolment means the answer is a short explicit list, and every control in GOVERNANCE is
   damage limitation for whatever is on it. Run `crawler telegram list-chats`, pick, and
   expect to pick fewer than you first think. Nothing in the design changes with the
   answer; the size of the risk does.

3. **What retention do you actually want on other people's messages, and do you want any
   Telegram backfill?**
   *Default:* forward-only from `enrolled_at`, and **no retention default at all** — you
   type a number.
   *What changes:* these are two different controls and the earlier draft conflated them.
   Forward-only bounds **what enters**; retention bounds **what stays**. "I'll decide
   backfill later" is genuinely fine — later is not lossy for anything after enrolment. "I'll
   decide retention later" is not fine, because the sweep runs unattended twice a day and
   whatever it deletes is gone. Answer with a number (or `forever`) before you enrol the
   first conversation target; M11's acceptance check refuses to let you skip it.

4. **Is X worth ~$15/month to you?**
   *Default:* the archive importer ships, the REST connector ships disabled.
   *What changes:* if no, M15 is dead code behind a flag and you lose third-party
   timelines entirely — M14 (your own complete history and your own DMs) is free and is
   the more valuable half regardless. If yes, you also inherit the spend governor, the
   monthly counter, the 01:00 UTC schedule and the obligation to reconcile the price at
   `console.x.com` quarterly.

5. **Do you want the Reddit degraded path if R0 is declined?**
   *Default:* undecided — this is the fork at M13.
   *What changes:* `feeds + Arctic Shift` is a genuinely usable tool, but it is not the
   same tool: no `upvote_ratio`, no clean `edited` timestamp, no `removed_by_category`, no
   comment trees from Reddit itself, and a ~36-hour metric lag on everything Arctic Shift
   supplies. If yes, M13 is roughly the same size either way. If no, a declined R0 simply
   deletes the Reddit connector from the plan and Phase 3 disappears.

6. **Does anything downstream of this corpus involve an LLM?**
   *Default:* assumed no.
   *What changes:* if yes, Telegram's API terms prohibit using or aggregating Telegram data
   to train or fine-tune models, and Reddit's terms reportedly carry an equivalent
   prohibition. That is an architectural boundary, not a footnote: it decides whether
   Telegram content may enter an embedding index at all, and it must be decided **before
   M12** rather than discovered after the archive exists. Summarization and semantic search
   over your own chat history is exactly the use this affects. The export manifest already
   stamps the constraint so it travels with the data — but a manifest is a record, not an
   enforcement.

---

*Companion documents: [./README.md](./README.md) · [./ARCHITECTURE.md](./ARCHITECTURE.md) ·
[./docs/DATA-MODEL.md](./docs/DATA-MODEL.md) · [./docs/GOVERNANCE.md](./docs/GOVERNANCE.md) ·
[./docs/DECISIONS.md](./docs/DECISIONS.md) · [./docs/sources/](./docs/sources/).*
