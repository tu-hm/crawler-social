# DECISIONS

Architecture decision record for **crawler-social**. Every entry is **frozen as of
2026-09-01**. The purpose of this file is that someone reading in six months — very
likely the author — does not relitigate a settled question, and can see at a glance
what would have to change for a decision to be worth reopening.

Each entry is deliberately one screen. Where a decision rests on a fact that is not
verified, the fact carries its confidence label here and appears in
[../PLAN.md](../PLAN.md) §12 *Verify before building*, which is the consolidated list.

Read alongside: [../ARCHITECTURE.md](../ARCHITECTURE.md) (what the system *is*),
[./DATA-MODEL.md](./DATA-MODEL.md) (the schema), [./GOVERNANCE.md](./GOVERNANCE.md)
(privacy classes and retention), [./sources/](./sources/) (per-connector evidence),
[../PLAN.md](../PLAN.md) (scope, the numbered milestones, the deferred list with its
triggers, the verify checklist and the open questions),
[../README.md](../README.md) (what this repo is and the reading order).

---

## Index

| # | Decision | The ruling in one line |
|---|---|---|
| **A. The seam** | | |
| [0001](#adr-0001) | Capability-contract core, lazily-imported in-process connectors | Core owns everything except transport. |
| [0002](#adr-0002) | `Envelope` is the only object crossing the boundary | The bytes are the abstraction; nothing upstream of them is. |
| [0003](#adr-0003) | No connector base class | `Protocol` + importable helpers, never inheritance. |
| [0004](#adr-0004) | The fetch/parse split is absolute | `parse()` is pure or raw-first is a lie. |
| [0005](#adr-0005) | The purity tax is paid, not negotiated | A second navigation is a second envelope. |
| [0006](#adr-0006) | Envelope then cursor, one transaction; no `advance_cursor()` | The cursor can never outrun the bytes. |
| [0007](#adr-0007) | Cursors are rebuildable from stored envelopes | `cursors --rebuild` turns an argument into a recovery path. |
| [0008](#adr-0008) | Durable and ephemeral cursors are different storage locations | A page token never enters `cursors`. |
| [0009](#adr-0009) | Coverage claims are persisted and drive a core-owned `gaps` table | Four silent-data-loss bugs become one table. |
| [0010](#adr-0010) | SUSPECT is three rules, not one | Each is blind exactly where the others fire. |
| **B. Data model** | | |
| [0011](#adr-0011) | One `items` table, hybrid leaning polymorphic | FTS5 and bm25 decide this, not taste. |
| [0012](#adr-0012) | The escape hatch is a generated column, not a detail table | One `ALTER TABLE` line, verified. |
| [0013](#adr-0013) | Feed item and chat message are one entity | The split lives on `containers.shape`. |
| [0014](#adr-0014) | `item_type` is provenance, not a discriminator | Nothing in core branches on it. |
| [0015](#adr-0015) | Adjacency is truth, materialized path is a derived index | Children arrive before parents on every platform. |
| [0016](#adr-0016) | `thread_path` encoding is frozen | Fixed-width hex, `/`-joined, prefix-range safe. |
| [0017](#adr-0017) | `envelopes` is split from `envelope_fetches` | Dedupe must not erase re-fetch history. |
| [0018](#adr-0018) | `envelopes.seq` is `AUTOINCREMENT` | Rowid reuse would break every `seq >` scan. |
| [0019](#adr-0019) | Metrics are a change-log, not a sample-log | Write only when the value moved. |
| [0020](#adr-0020) | Edits get `item_versions` | An edit is a new row; a re-fetch is a no-op. |
| [0021](#adr-0021) | Telegram entities are stored raw, never flattened | Flattening is a lossy parse you cannot undo. |
| **C. Privacy and governance** | | |
| [0022](#adr-0022) | Three privacy classes, two physical files | The boundary is a file path, not a `WHERE` clause. |
| [0023](#adr-0023) | Privacy is a property of the container, resolved by core; identity linking stays out of the schema | Config may raise, never lower. Unclassifiable → conversation. |
| [0024](#adr-0024) | Identical DDL both files, with one declared FTS5 fork | Routing is total; `rm private.db` is complete. |
| [0025](#adr-0025) | FileVault checked, SQLCipher for `private.db` only | Backup exfiltration is the real threat, not theft. |
| [0026](#adr-0026) | Pseudonymize at export; join on `actor_hmac`; no `identity_mode` column | Names are in the message text anyway. |
| [0027](#adr-0027) | There is no `phone` column anywhere | Structural, not a policy note. |
| [0028](#adr-0028) | Redaction must not undo itself | `block_reingest = 1`, consulted on every insert. |
| [0029](#adr-0029) | Purge tells the truth about SQLite and APFS | `secure_delete` at creation, then say what remains. |
| [0030](#adr-0030) | Upstream deletes resolve per class; `follow` locked for Reddit | Detection needs strikes and a health gate. |
| [0031](#adr-0031) | `Cap.DELETE_EVENTS` is deliberately unset for Telegram | Otherwise DM deletions are never detected. |
| [0032](#adr-0032) | Forward-only enrolment | The highest-leverage control, and it is free. |
| [0033](#adr-0033) | Export is default-deny; `--include-private` refused off-TTY | A scheduled job can never emit conversation data. |
| [0034](#adr-0034) | Media is a snapshot-at-ingest side-car with weaker guarantees | The only unbounded cost in the project. |
| **D. Operations** | | |
| [0035](#adr-0035) | One LaunchAgent, exactly one SQLite writer, no supervisor | launchd already is one. |
| [0036](#adr-0036) | If a push transport ever ships: it never opens SQLite — **NOT BUILT at v1** | Right argument, zero producers. `Cap.FILE_IMPORT` is the half that ships. |
| [0037](#adr-0037) | One `TokenBucket` behind one `Budget`; `mode:` is a comment | If code branches on `mode`, the abstraction failed. |
| [0038](#adr-0038) | X's monthly allowance is money → `usage_counters` | A bucket smooths a rate; a counter enforces a budget. |
| [0039](#adr-0039) | X runs at 01:00 UTC | The billing model picks the cron time. |
| [0040](#adr-0040) | Six dispositions projected onto an exit table | The taxonomy survives any process boundary. |
| [0041](#adr-0041) | STOP disarms the schedule and never retries | A retry loop turns a temporary block permanent. |
| [0042](#adr-0042) | WAIT honours server-supplied durations literally | Telegram tells you; Facebook does not. |
| [0043](#adr-0043) | Secrets: Keychain, loud failure, two file-path credentials | Never a silent fallback to a file. |
| [0044](#adr-0044) | Zalo's single-use refresh token gets crash-safe rotation | Write the replacement before spending the token. |
| [0045](#adr-0045) | Plugin discovery is a hardcoded dict of lazy import strings | It is what keeps `reparse` selenium-free. |
| [0046](#adr-0046) | One `uv` project, per-connector dependency groups | Five lockfiles is isolation nobody needs yet. |
| [0047](#adr-0047) | A capability exists only if core branches on it | Plus `capabilities_note()` for what no enum can say. |
| [0048](#adr-0048) | Provenance is two integers plus a bounded N:M keyed on `role` | Metric refreshes never write provenance rows — a schema property, not a convention. |
| [0049](#adr-0049) | `FixtureConnector` + `tick --fixture` + golden snapshots | The whole pipeline, offline. |
| [0050](#adr-0050) | `codec` column from day one; zlib at v1, zstd at connector two | Schema frozen now so the switch is a flag flip. |
| [0051](#adr-0051) | FTS5: one unconditional-trigger index per file | The file split makes a corruption class unrepresentable. |
| [0052](#adr-0052) | Python 3.13 and a SQLite ≥ 3.45 assertion | `uv` bundles its own SQLite. |
| [0053](#adr-0053) | Two SQLite one-shot decisions: `local_day` STORED, canonical query text | Neither can be retrofitted — and the engine only enforces the first once rows exist. |
| **E. Sources** | | |
| [0054](#adr-0054) | Reddit: `prawcore` + `httpx`, never PRAW in the ingest path | PRAW discards the bytes. |
| [0055](#adr-0055) | Reddit ships one backend at v1; the degraded path is specified, unbuilt | No three-way abstraction for a maybe. |
| [0056](#adr-0056) | Every scraping path for X is rejected permanently | ~$15/month against permanent suspension. |
| [0057](#adr-0057) | X DMs come exclusively from the archive ZIP | Free, complete, better provenance, and it *is* the payload. |
| [0058](#adr-0058) | Telegram: poll, do not listen | The update stream is explicitly gap-tolerant. |
| [0059](#adr-0059) | Telegram read-only is enforced by absence, not a flag | A toggle is a thing that can be set to true. |
| [0060](#adr-0060) | Zalo is deferred with its slot specified; Selenium rejected outright | The proof that transport is a per-connector decision. |
| [0061](#adr-0061) | Delivery order: Facebook → Telegram → Reddit → X | Test the design at connector two, not connector four. |
| [0062](#adr-0062) | The Facebook extraction spike is a parse-quality gate, not a project gate | Choosing wrong at a project gate costs everything before it. |
| [0063](#adr-0063) | Legal baseline: Law 91/2025 + Decree 356/2025, no exemption assumed | Decree 13/2023 was repealed on 2026-01-01. |
| **F. v2 — local web viewer** | | |
| [0064](#adr-0064) | The web viewer is read-only and loopback-bound by construction | The crawler is the single writer; snapshot HTML is untrusted. |

---

## A. The seam

<a id="adr-0001"></a>
### ADR-0001 — Capability-contract core with in-process, lazily-imported connectors

**Context.** Three architectures were prototyped on paper: *capability-registry*
(privacy on the container), *thin-core* (`store = "social" | "private"` as one bit per
connector), and *log-then-project* (append-only event log, projections rebuilt). The
judges split 1–1.

**Decision.** Capability-registry, grafted with thin-core's shipping discipline and
log-then-project's durability mechanics. Core owns scheduling, pacing, retry, secrets,
the raw store, cursors, gap accounting and governance. A connector owns its transport
**entirely**.

**Rejected.**
- *thin-core* — disqualified structurally, not stylistically. `store` is **one bit per
  connector**, but Telegram emits broadcast channels *and* private DMs from one
  `.session` file under `singleton = true`. Either the channel posts land in the
  encrypted store (where `search_index` does not exist, so they are unsearchable by
  construction) or the DMs land in the plain store (privacy boundary gone). Splitting
  into two connector programs collides on the flock and risks a permanent
  `AUTH_KEY_DUPLICATED`. Telegram is connector **two** (ADR-0061), so thin-core fails
  at connector two, not connector five.
- *log-then-project* — the most interesting design, and it loses on its own admission.
  An append-only log is the wrong posture for a corpus that Reddit's Data API terms,
  Zalo's `user_withdraw` webhook and Vietnam's PDPL all require to be **mutable**. It
  also demands a blob store, dictionary training, a projector registry and an erasure
  door before one row appears in `items`.
- *Out-of-process connectors* — the exit-code table (ADR-0040) is frozen now as the
  process-level projection, so this stays available at zero redesign cost. Trigger: a
  Zalo Node sidecar, or a connector that hangs past its timeout.

**Consequences.** The five transports (headful Chrome, REST/OAuth, MTProto, webhook,
file import) share exactly one thing worth abstracting: the bytes they produce.
Everything upstream of those bytes is genuinely incomparable and is not abstracted.
Judge 1's binding critique — that capability-registry as originally written ships
Facebook two to three weeks late — is answered by ADR-0061: **the shapes are fixed
today, the machinery arrives just in time.**

---

<a id="adr-0002"></a>
### ADR-0002 — `Envelope` is the only object that crosses the connector boundary

**Context.** Something has to be the seam. The candidates were a `Fetcher`, a
`Transport` base class, a normalized `SocialItem`, or the raw bytes.

**Decision.** `Envelope` — verbatim transport bytes, plus a cursor proposal, plus a
coverage claim. Nothing else crosses.

**Rejected.**
- `Fetcher.get(url) -> Response` — fits Reddit and X, actively fights Selenium (whose
  unit of work is "the viewport after N scrolls") and MTProto (whose 100-object batches
  never had a URL).
- A normalizing layer producing one canonical `SocialItem` upstream of storage — a
  universal item with thirty nullable columns is strictly worse than the drafts, and it
  would erase the broadcast/conversation distinction the storage design depends on.

**Consequences.** The seam is placed at the thing core must durably store anyway, so
raw-first costs nothing extra. **Litmus test, run at every review:** delete
`connectors/facebook/` — core must still compile and pass its tests.

---

<a id="adr-0003"></a>
### ADR-0003 — `Connector` is a `Protocol`; there is no base class

**Context.** Five connectors will share *some* code. The question is whether that
sharing is inheritance or import.

**Decision.** `typing.Protocol`, duck-typed. Shared **helpers** a connector may import
(`core.http`, `core.pace.Bucket`, `core.log`, `core.codec`).

**Rejected.** A `BaseConnector` — it would impose an HTTP-shaped lifecycle (open
session → request → close) on the browser connector, which has a completely different
one (launch profile → navigate → scroll → quit driver). Helpers can be *declined*
where they do not fit; an inherited lifecycle cannot.

**Consequences.** `FixtureConnector` satisfies the same Protocol with no inheritance
tax (ADR-0049). A connector's third-party client library (selenium, telethon,
prawcore) lives inside its directory and **never leaks a type into core** — the
generalization of the original plan's `stealth.py`-isolation argument (PLAN.md §5.5).

---

<a id="adr-0004"></a>
### ADR-0004 — The fetch/parse split is absolute

**Context.** Raw-first storage promises: *fix the parser in March, recover February
without re-fetching.*

**Decision.** `fetch()` is a lazy generator that may not parse and may not touch the
DB. `parse()` is **pure** — no network, no DB, no clock (use `env.captured_at`), no
self-mutation. Enforced by `test_parse_needs_no_secrets`, which constructs every
connector with `secrets={}` and parses every fixture.

**Rejected.** "Parse may make one small call" — that single concession makes replay
require a live session, which is the exact cost the bytes were stored to avoid, plus
the account risk.

**Consequences.** Replay runs on a machine with a locked Keychain and no Chrome
installed. A wall-clock read in `parse()` would resolve "3 hours ago" against *today*
instead of the capture day, silently corrupting every replayed relative timestamp.
Without the test, "parse is pure" is a comment that decays the first time someone needs
one more request — and that person will be you, in a hurry, six months in.

---

<a id="adr-0005"></a>
### ADR-0005 — The purity tax is paid, not negotiated

**Context.** Facebook will want to fetch inside `parse()`: a truncated "See more", a
comment tree needing a second navigation.

**Decision.** `fetch()` emits a **second envelope** (`fb.post_html`). `parse()` never
fetches. Deciding *which* posts need expansion is cheap inspection inside `fetch()`,
not parsing.

**Rejected.** A `parse(env, fetcher)` signature; a "supplementary fetch" callback; a
two-phase parse. All are the same concession wearing different names.

**Consequences.** It costs browser minutes, which is the most expensive currency in
this project. It buys six months of stored HTML that is re-parseable after a DOM
rotation. This is the exact concession that keeps the raw-first premise honest, and it
is the first thing that will be argued about under time pressure.

---

<a id="adr-0006"></a>
### ADR-0006 — Core commits the envelope, then the cursor, in one transaction

**Context.** "The cursor advanced past data we never stored" is the archetypal
silent-corruption bug in an incremental crawler.

**Decision.** `commit_envelope()` writes the envelope, the fetch event, the drafts, the
coverage check and the gap — and **only then** writes `cursor_after`. There is
deliberately **no `advance_cursor()` method** on the `Connector` Protocol; the cursor
rides on the envelope as a *proposal*.

**Rejected.** `connector.advance(cursor)` called by core after a batch; connector-owned
cursor state. Both put the durability guarantee in five places instead of one, and one
of the five would eventually get it wrong.

**Consequences.** `kill -9` mid-run costs at most **one envelope and zero correctness**,
uniformly across all five transports. This is the single correctness property worth
centralising, because core owns both tables. Cost: one field on a frozen dataclass.

---

<a id="adr-0007"></a>
### ADR-0007 — Cursors are rebuildable from stored envelopes

**Context.** ADR-0006 makes the cursor correct by construction. Corruption, manual
surgery and restored backups are still possible.

**Decision.** `crawler cursors --rebuild` re-derives every durable watermark:

```sql
SELECT target_id, cursor_json, max(seq)
  FROM envelopes WHERE cursor_json IS NOT NULL GROUP BY target_id;
```

**Rejected.** Trusting `cursors` as the sole record — it turns any corruption into a
manual repair with no defined procedure.

**Consequences.** Grafted from log-then-project. It converts a correctness *argument*
into an executable *recovery path*, at the cost of one nullable column and one query.
`envelopes.cursor_json` therefore stores the durable proposal even though `cursors`
holds the live value.

---

<a id="adr-0008"></a>
### ADR-0008 — Durable and ephemeral cursors are different storage locations

**Context.** "Cursor" means three incompatible things: an opaque page token (Reddit
`after`, X `next_token`), a durable monotonic watermark (Telegram message id, X
`since_id`, Reddit `created_utc`), and nothing at all (a Selenium scroll).

**Decision.** A durable watermark goes in `cursors`. An in-flight page token goes in
`runs.params` and is **never written to `cursors` at all**, with
`CHECK (kind <> 'page' OR expires_at IS NOT NULL)` as belt and braces.

**Rejected.** One generic `cursor_value TEXT` column — it invites exactly the bug where
a page token is persisted forever and silently breaks daily runs months later, long
after the change that caused it. A CHECK constraint alone was judged insufficient
because the *location* is the guarantee.

**Consequences.** Facebook, which has no addressable cursor, stores a content
watermark plus a behavioural stop rule and proposes no page cursor at all. Reddit's
`after` fullname is a hint the connector may discard at any time.

---

<a id="adr-0009"></a>
### ADR-0009 — Coverage claims are persisted on the envelope and drive a core-owned `gaps` table

**Context.** Reddit's ~1000-item listing wall, Telegram's `UpdatesTooLong`, X's ~3,200
-post timeline ceiling and Zalo's enrolment floor are four bespoke silent-data-loss
bugs. Written per connector, they would be four different bugs written four times.

**Decision.** Every envelope declares `exact | partial | opaque` over an axis interval,
plus a `withheld` count. Core maintains `gaps` from those claims. The connector
contributes **zero** gap-detection code.

**Rejected.**
- Per-connector gap tables — four implementations of one idea.
- Requiring an interval from every connector. **`opaque` is a first-class honest
  value, not a failure state.** Facebook will claim it forever; inventing a
  scroll-position interval would be *worse* than none, because core would then
  confidently record "no gap" over a feed that unmounted half its posts.

**Consequences.** Reddit's 1000-item wall becomes a `gaps` row with
`reason='listing_cap'` written **by core**. The claim is persisted on the envelope
(`cover_*` columns) so gap analysis is a query and the claimed-vs-found check is
auditable after the fact.

---

<a id="adr-0010"></a>
### ADR-0010 — SUSPECT is three rules, not one

**Context.** The worst failure mode in this class of tool is the one that *looks like
success*: the run returns 0 items, "succeeds", the cursor advances, and every future
run skips real content forever. PLAN.md §6 M7 identifies it for Facebook
(empty-feed-with-HTTP-200); it recurs on every source.

**Decision.** Three independent detectors, all owned by core:

- **(a)** an envelope claimed `COVER_EXACT` over a non-empty interval and
  `ParseResult.found == 0`.
- **(b)** zero items from a target that produced items in **each** of its last three
  runs.
- **(c)** a run whose `runs.stop_reason` is a watermark stop at **scroll ≤ 2** with
  `items_new == 0`.

Any of the three rolls back the cursor write and sets `runs.status='suspect'`. Two
consecutive escalate to `HUMAN`.

**Rejected.** Rule (b) alone (the obvious design) — it is blind on a brand-new target
and for three runs after a reparse resets the ledger. Rule (a) alone — it can never
fire for a connector that honestly claims `COVER_OPAQUE`, which Facebook always will.
**Rules (a) and (b) together, which is where this ADR originally stopped** — see below.

**Consequences.** Rule (a) fires on run **one**, catching Facebook's empty feed, a
Reddit auth-error page rendered with HTTP 200, and Telegram's silently-empty history
after removal from a group. Rule (b) catches what (a) structurally cannot see.

**Rule (c) was added because (b) disqualifies itself on the failure it is most needed
for.** If Facebook's pinned-badge selector rotates, the watermark stop rule trips on scroll
one, every run thereafter returns **zero new items** — and after three such runs (b) has no
"produced items in each of its last three runs" history left to test against. It goes blind
by its own definition, silently, on the exact failure mode the design fears most. Rule (c)
needs no history and no fill rate: a feed claiming it reached familiar ground before it has
scrolled twice, while adding nothing, is not a quiet day. It costs the connector one honest
`stop_reason` and `scroll` value in the last envelope's `meta`; the rule itself lives in
core and applies to any connector with a behavioural stop rule.

All three exist because each is blind exactly where the others fire.

---

## B. Data model

<a id="adr-0011"></a>
### ADR-0011 — One `items` table, hybrid leaning polymorphic

**Context.** Three options: per-source tables plus a unifying VIEW; one table with
everything in JSON; one core table with typed universal columns.

**Decision.** ONE `items` table with typed universal columns, a JSONB `extra` for the
source-specific long tail, and per-source detail tables documented as an escape hatch
but **empty at v1**.

**Rejected.**
- *Per-source tables + VIEW* — the decisive argument is FTS5, not query convenience.
  An external-content FTS5 index binds to exactly **one** content table via
  `content_rowid`, so five sources means five indexes, and `bm25()` is computed per
  index from *that index's* IDF and average document length. Scores are mathematically
  incomparable, so "search everything, best first" is **not expressible**. Secondly,
  adding a connector would become a schema migration, which contradicts the whole
  plugin premise. Thirdly, every satellite table (`metric_observations`, `media`,
  `item_envelopes`, `item_relations`) would need an unenforced polymorphic FK.
- *A pure JSON bag* — loses `CHECK`/`NOT NULL` on the columns that carry correctness,
  and the partial and covering indexes the three headline queries depend on.

**Consequences.** Adding a connector is never a schema migration. The trade is that
`items` carries columns most sources leave NULL; measured at ~320 B/item all-in
excluding raw payloads, which is not a cost worth optimising.

---

<a id="adr-0012"></a>
### ADR-0012 — The escape hatch is a generated column, not a detail table

**Context.** ADR-0011 only holds if promoting a hot source-specific field out of
`extra` is genuinely cheap.

**Decision.** One line, verified on SQLite 3.51.0:

```sql
ALTER TABLE items ADD COLUMN flair TEXT
  GENERATED ALWAYS AS (extra ->> '$.link_flair_text') VIRTUAL;
CREATE INDEX idx_flair ON items(flair) WHERE flair IS NOT NULL;
-- EXPLAIN QUERY PLAN: SEARCH items USING INDEX idx_flair (flair=?)
```

**Rejected.** Per-source detail tables at v1 — they buy a join for a promotion that is
one `ALTER TABLE` away. Trigger to reconsider: one source adds **more than eight**
fields queried *together* on a hot path. None of the five currently does.

**Consequences.** **Verified caveat:** `ALTER TABLE ... ADD COLUMN ... STORED` fails
with `cannot add a STORED column`; only `VIRTUAL` can be added later. That is why
`local_day` must be STORED in the initial DDL (ADR-0053).

---

<a id="adr-0013"></a>
### ADR-0013 — Feed item and chat message are one entity; the split lives on the container

**Context.** A Facebook page post and a Telegram DM look like different things.
Stripped to their bones they are the same tuple: author, container, time, text, media,
optional parent, drifting metrics, provenance.

**Decision.** One `items` table. The distinction lives on
`containers.shape IN ('feed','conversation')`.

**Rejected.** Separate `posts` and `messages` tables — it would force the Telegram
connector to maintain two persistence paths, duplicate every satellite table, split the
FTS index for no privacy benefit, and turn a channel's linked discussion group into a
cross-table join.

**Consequences.** The three things that actually differ — natural sort order
(`published_at DESC` vs per-container monotonic `source_seq ASC`), privacy class, and
child fan-out — are **all properties of the container**. This is precisely what lets
one Telegram connector write channel posts and DMs through one persistence path with
zero branching, and it is the decision that decided the bake-off (ADR-0001). A
channel's linked discussion group is `containers.parent_id`.

---

<a id="adr-0014"></a>
### ADR-0014 — `item_type` is provenance, not a discriminator

**Context.** `items.item_type` carries `fb.post`, `reddit.comment`, `tg.channel_post`,
`zalo.message`. A discriminator column invites branching.

**Decision.** It is parser-dispatch and provenance metadata only. **Nothing in core
branches on it.**

**Rejected.** Using it as a structural discriminator — that is how "one table" quietly
becomes "five tables in a trench coat".

**Consequences.** This is the standing test for whether a discriminator is being
abused, applied at every review: grep core for `item_type ==`. A hit is a bug.

---

<a id="adr-0015"></a>
### ADR-0015 — Adjacency is truth; the materialized path is a derived index

**Context.** Comment trees need a fast whole-subtree read. The three classical options
are adjacency list, materialized path, and closure table.

**Decision.** `parent_id` (resolved, nullable) is **truth**. `parent_ref` (the raw
platform string) is **always written even when unresolvable**.
`thread_path` / `root_id` / `depth` are derived and **may be NULL**.

**Rejected.**
- *Closure table* — a 20k-comment Reddit thread at depth 30 costs ~200k closure rows,
  and closure earns its keep only when subtrees are **reparented**. None of these five
  platforms ever reparents a comment. Paying O(n·depth) for a mutation that cannot
  occur is a straight loss.
- *Materialized path alone* — you cannot write the path until the whole ancestor chain
  is ingested, and it frequently is not: Reddit `more` children, Telegram replies
  outside the `offset_id` window, X replies to protected or deleted posts. Path-only
  makes those rows **unwritable**.
- *Adjacency alone* — a recursive CTE per tree read, and no depth-first order from a
  single index scan.

**Consequences.** Ingestion becomes **order-independent** — the single biggest source
of "my crawler dropped half the comment tree" — via a repair pass over
`idx_items_dangling`. **Correctness must never depend on `thread_path` being non-NULL,
only speed;** the recursive CTE is the permanent fallback. Honest caveat:
`ORDER BY thread_path` gives parents-before-children with siblings in *ingestion id*
order, which approximates chronological within one fetch but is not Reddit's "best"
sort — extract the subtree by range scan, then sort in Python.

---

<a id="adr-0016"></a>
### ADR-0016 — `thread_path` encoding is frozen

**Context.** A materialized path is only useful if a prefix range captures exactly one
subtree.

**Decision.** `/`-joined **10-char zero-padded hex** of each ancestor's `items.id`:
`0000000014/0000000015/0000000016`.

**Rejected.** Variable-width decimal — root 20 (`14`) would prefix-collide with root
200 (`200`... and `14` with `140`). A non-`/` separator — `/` is 0x2F, exactly **one
below** `'0'` (0x30), so a half-open range on the root prefix captures the root **and
its entire subtree and nothing else**.

**Consequences.** Q2 (full comment tree, depth-first) is one covering range scan on
`idx_items_tree` with no temp b-tree — verified on a 60k-row table. Changing the
encoding later would require rewriting every path, so it is frozen now.

---

<a id="adr-0017"></a>
### ADR-0017 — `envelopes` is split from `envelope_fetches`

**Context.** The original plan's `raw_payloads` (PLAN.md §5.5) carries both `sha256 UNIQUE` and
`captured_at`.

**Decision.** `envelopes` — one row per **distinct byte sequence**, `sha256` UNIQUE.
`envelope_fetches` — one row per **fetch event**, always appended, even when the bytes
are byte-identical.

**Rejected.**
- *One table with `sha256 UNIQUE`* — this is the bug. Monday, Tuesday and Wednesday
  fetches of an unchanged page silently collapse into **one row with Monday's
  timestamp**, destroying the exact input to soft-delete detection: *"when did I last
  confirm this still existed."* All three architecture proposals found it
  independently.
- *One table with no UNIQUE* — stores the same 400 KB HTML page 365 times a year.

**Consequences.** This is the single most important correction to PLAN.md. Dedupe on
the blob stays free; history on the event is complete. The absence sweep (ADR-0030)
depends on it entirely.

---

<a id="adr-0018"></a>
### ADR-0018 — `envelopes.seq` is `INTEGER PRIMARY KEY AUTOINCREMENT`

**Context.** Plain `INTEGER PRIMARY KEY` is an alias for `rowid`, and SQLite **reuses**
rowids after deletes.

**Decision.** `AUTOINCREMENT`.

**Rejected.** Plain rowid, for the usual "AUTOINCREMENT costs a sequence table" reason.

**Consequences.** Retention pruning, `purge_after` and per-subject redaction all
**delete** envelope rows. Rowid reuse would silently break every
`WHERE seq > :last_processed` reparse and backfill scan — a new envelope would be
handed an id a previous scan had already passed. One keyword, a real bug.

---

<a id="adr-0019"></a>
### ADR-0019 — Metrics are a change-log, not a sample-log

**Context.** Scores, reactions, views and comment counts drift after publication.
PLAN.md's `post_counts` appends one row per crawl per post.

**Decision.** `metric_observations(item_id, metric_id, observed_at) -> value`,
WITHOUT ROWID, written **only when the value moved**, plus a denormalized
`items.metrics_current` JSONB for the timeline query.

**Rejected.**
- *Per-source metric columns* — die on Telegram's **per-emoji reactions** alone; you
  cannot have a column per emoji.
- *Unconditional daily samples* — ~180M rows/year at 100k items, for **zero
  information** about year-old posts that stopped moving.
- *Overwriting in place* — destroys the time series, which is most of the analytical
  value.

**Consequences.** Interpolation is step-wise (a value is held until the next row),
which is correct "last observed" semantics but easy to misread as a flat line followed
by a cliff — any chart should shade periods with no observation by joining `runs`.
`approximate` is load-bearing, not decoration: Reddit deliberately vote-fuzzes and
Facebook rounds above 1k in the UI (`1.2K` → 1200), so a ±3 delta is **noise** and the
schema must say so. `source_kind` (`live` vs `archive`) is separate, because Arctic
Shift's ~36h metric lag must never be silently mixed into a live series.

---

<a id="adr-0020"></a>
### ADR-0020 — Edits get version history

**Context.** Telegram edits, Facebook edits and X edits all arrive as a changed body on
a known id.

**Decision.** `item_versions(item_id, content_hash, observed_at, text, entities_json)`,
written on the same conditional-insert rule as metrics. An edit is a **new row**; an
unchanged re-fetch is a **no-op**.

**Rejected.** Overwriting `text` in place — recoverable only by hand from raw
envelopes, which is exactly the "technically possible, practically never" outcome.

**Consequences.** Grafted from thin-core's `PRIMARY KEY (chat_id, msg_id,
content_hash)` insight. `item_versions` carries `entities_json` because ADR-0021 says
entities are stored raw and they belong with the text version they annotate.

---

<a id="adr-0021"></a>
### ADR-0021 — Telegram `entities` are stored raw and never flattened at ingest

**Context.** `MessageEntity` is a list of formatting spans (bold, code, url, mention,
custom emoji) expressed as **UTF-16 byte offsets** against the message text.

**Decision.** Store the raw entity list verbatim. Never flatten to markdown at ingest.

**Rejected.** Flattening to markdown on write, which is the convenient thing to do.

**Consequences.** Flattening is a **lossy parse you cannot undo** — precisely what
raw-first exists to prevent. It also gets the offsets wrong in Vietnamese, where
combining marks make UTF-16 offsets and Python string indices diverge. Rendering
happens at the edge, from stored text plus stored entities.

---

## C. Privacy and governance

<a id="adr-0022"></a>
### ADR-0022 — Three privacy classes, two physical files

**Context.** Pages and subreddits are broadcast. DMs and private group chats contain
other people's personal data, from people who never agreed to be in anyone's database.

**Decision.** Three classes — `broadcast`, `joined`, `conversation`. Two files:
`broadcast` + `joined` → `data/social.db` (plain); `conversation` →
`data/private.db` (SQLCipher).

**Rejected.**
- *One class* — the policy knobs (retention, media, export, delete-following, rescan
  window) genuinely differ across all three, so collapsing them means picking one set
  of wrong defaults.
- *Three files* — the encryption boundary only needs to be drawn once, and `joined`
  content is not conversation content.
- *One file with a `privacy` column* — the encryption boundary must be a **file path**,
  not a `WHERE` clause a query-builder bug can bypass.

**Consequences.** `rm data/private.db` is a complete, consistent operation. `crawler
search` opens `social.db` only; `crawler search --private` opens `private.db` only —
deliberately, so a global search is *structurally incapable* of surfacing a DM (and
bm25 scores are not comparable across two indexes anyway).

---

<a id="adr-0023"></a>
### ADR-0023 — Privacy is a property of the container, resolved by core

**Context.** A connector could classify its own targets. It must not.

**Decision.** The connector `propose_privacy()`s from **platform metadata** (Telegram
peer type, Facebook group badge, member count) — never from a config default. Core
resolves. Config may only **raise** sensitivity; lowering requires `--force-tier` and
stamps `privacy_source='forced'` in the DB **forever**. An unclassifiable target fails
closed to `conversation`.

**Rejected.** Connector-decided policy — that is how one sloppy transport quietly
becomes the exception that leaks. A config default as the source of classification —
a Telegram connector defaulting every container to `broadcast` would bypass the entire
privacy design silently.

**Consequences.** This is the decision thin-core could not express, and it is why
capability-registry won (ADR-0001). The resolved `GovernanceProfile` is handed to the
connector **and** to the persistence layer, so a connector that forgets to scrub still
cannot write a phone number into a conversation row — `db.upsert_*` re-applies it.
Defence in depth, one line of code. `containers` carries
`CHECK (privacy <> 'conversation' OR viewer_account_id IS NOT NULL)`: *"by what right
do I hold this?"* as a NOT NULL constraint.

**Standing rule this ADR now also owns: cross-platform identity linking is out of the
schema, and adding it requires a schema migration *and* an ADR.** That pairing is the
review checkpoint. An earlier draft bought it with two empty tables — `persons` and
`author_person_links`, the latter carrying `CHECK (linked_by IN ('manual','self'))` with
deliberately no `'inferred'` value. The reasoning was right; the mechanism was two tables,
a composite foreign key and a `CHECK` with **zero declared writers**, which is a lot of DDL
for a tripwire a sentence buys outright. Both tables are gone from the frozen DDL and this
rule replaces them. The reason is correctness rather than caution: Vietnamese given-name
distributions are concentrated enough that name-based cross-platform matching is near a coin
flip, and a wrong link silently poisons every query that reads it with no way to tell which
rows are affected. `authors.actor_hmac` stays the per-source join key and is sufficient for
everything the tool does, `purge --person` included. The deferred row and its trigger — *you
actually want to link two accounts, by hand* — are in [../PLAN.md](../PLAN.md) §11.

---

<a id="adr-0024"></a>
### ADR-0024 — Identical DDL in both files; one envelope → one target → one container

**Context.** Two files could diverge, and cross-file references could dangle.

**Decision.** The **identical** DDL is applied to both files. The resolved
`GovernanceProfile` routes each envelope and each item to a file **before any write**.
One envelope belongs to exactly one target, and a target maps to exactly one container
— a **frozen invariant**.

**Rejected.**
- *Different schemas per file* (e.g. no `items_fts` in the private file, as thin-core
  had) — that is what made conversations unsearchable by construction.
- *Cross-file foreign keys* — SQLite cannot enforce one under `ATTACH` anyway.
- *Allowing an envelope to span containers* — a Telegram forward from another channel
  produces an unresolved `to_ref` **text edge only**, never a write into another
  container.

**Consequences.** File routing is trivial and **total**. One Telegram run writes a
channel to `social.db` and a DM to `private.db` in the same tick with zero branching.
Because both files carry `items` and an unconditional-trigger `items_fts`, this
simultaneously kills capability-registry's FTS delete-trigger corruption trap
(ADR-0051) and thin-core's "conversations are unsearchable" flaw. **Conversation
*envelopes* go to `private.db` too, not just parsed rows** — this is the half everyone
forgets, and getting it wrong (raw TL slices in the plain file) defeats the entire
separation, because raw-first means the payload contains everything the parsed rows
contain and more.

**One fork, declared rather than assumed.** "Identical DDL" rests on an unverified fact:
whether the vendored SQLCipher build in `sqlcipher3` 0.6.2 has **FTS5** compiled in
(PLAN.md §12 B1). If it does not, `CREATE VIRTUAL TABLE items_fts` fails and the statement
list cannot be applied to `private.db` at all — which would break this invariant rather than
merely inconvenience it. So the fork is written down: same statement list **minus the five
FTS objects**, the divergence recorded in `schema_versions` as a declared state, `doctor`
reporting it as declared rather than as an error, and `crawler search --private` degrading to
a `LIKE` scan over `items.text` (milliseconds at personal DM volume). Conversations stay
searchable on both branches, which is the flaw this ADR rejected. The check is the **first**
thing M0 does. See [./DATA-MODEL.md](./DATA-MODEL.md) §14 item 1.

---

<a id="adr-0025"></a>
### ADR-0025 — FileVault is a checked prerequisite; SQLCipher covers `private.db` only

**Context.** The realistic threat is not a stolen laptop. It is a Time Machine
snapshot, an iCloud/Dropbox sync accident, or a `git add -A` — and FileVault does
nothing for any of those while the machine is running.

**Decision.** `doctor` runs `fdesetup status`, and `crawler targets add` **refuses** to
enrol a conversation target if FileVault is off. On top of that, SQLCipher via
`sqlcipher3` **0.6.2** (2026-01-07) for `private.db` only, key from the Keychain.

**Rejected.**
- *`pysqlcipher3`* — dead; last release 2023-01-29, sdist only, SQLCipher 3.x.
- *`sqlcipher3-binary`* — verified Linux-only wheels (`manylinux2014_x86_64`) on
  SQLCipher 3.x. Wrong platform.
- *SQLCipher for `social.db` too* — taxes the ~90% of rows that are public broadcast,
  breaks `sqlite3 data/social.db ".schema"` and every ad-hoc query in PLAN.md §9, and
  complicates the daily `.backup`, in exchange for protection against a threat
  FileVault already covers. Trigger to revisit: `social.db` needs to leave the machine.
- *Application-layer AES-GCM on the body column only* — same dependency cost, and it
  leaves who-talked-to-whom, when, how often and message lengths in the clear, which
  for a conversation corpus **is** most of the sensitive signal.
- *An encrypted sparsebundle as primary* — an operational control ("remember to
  unmount") that a daily LaunchAgent defeats by leaving it mounted. Keep it for the
  backup copy.

**Consequences.** The historical objection to SQLCipher on macOS is **stale**: 0.6.2
ships self-contained `macosx_11_0_arm64` wheels for cp310–cp314 with SQLCipher 4.x
vendored, so it is `uv add sqlcipher3` and `import sqlcipher3.dbapi2 as sqlite3`.
**Limits documented verbatim, not implied:** while a crawl runs the key is in process
memory and the database is decrypted for that connection. It covers backup and sync
exfiltration; it does **not** cover malware running as your own uid. A design that
implies otherwise creates a false sense of security that is worse than no encryption,
because it changes behaviour. **Unverified:** whether the vendored build has FTS5
compiled in — `SELECT * FROM pragma_compile_options()` at M0.

---

<a id="adr-0026"></a>
### ADR-0026 — Pseudonymize at export, not at storage; join on `actor_hmac`

**Context.** The governance recon recommended `identity_mode: pseudonymous` as the
storage default for conversations.

**Decision.** Overridden deliberately. Identity is stored **`clear`** in both files, and
there is **no `identity_mode` column at all** (see the last paragraph below). `authors`
joins on `actor_hmac` = HMAC-SHA256(source‖platform_uid, pepper), **always present**, with
`platform_uid` / `display_name` / `handle` / `profile_url` **nullable**. Masking happens at
**export**, in `v_items_masked`, keyed off `containers.privacy` and `items.is_from_self`
(ADR-0033).

**Rejected.** Pseudonymous storage — for two reasons. A private DM archive full of
`P-7f3a` labels is **useless to its owner**, which is the entire point of the tool.
And pseudonymizing the author column while storing full message text is **theatre**:
names appear in the text. The controls that actually work are scope (ADR-0032),
encryption (ADR-0025), retention, export gating (ADR-0033) and purge (ADR-0028).

**Consequences.** Redaction is one `UPDATE` that nulls the clear columns without
breaking a foreign key or requiring a cascade rewrite — which is only possible because
the join key is the HMAC. The pepper (32 random bytes) lives in the Keychain, never in
the DB; losing it makes pseudonymous rows permanently unjoinable. **Documented
honestly as pseudonymization, not anonymization:** platform ids are a low-entropy
enumerable space, and anyone holding both the DB and the pepper can rebuild the
mapping.

**And there is no per-target `identity_mode` column.** An earlier draft kept one, defaulting
to `clear`, "available for a conversation the user wants extra-hardened". Nothing anywhere
read it — `v_items_masked` keys its masking off `containers.privacy = 'conversation' AND
items.is_from_self = 0`, and export masking is gated on `--include-names`. A governance column
with a plausible name and no reader is worse than no column, because it reads like a control
that is running. If per-target masking is ever wanted it comes back as a nullable column and
a code path that reads it, in the same commit.

---

<a id="adr-0027"></a>
### ADR-0027 — There is no `phone` column anywhere in the schema

**Context.** Phone numbers, emails, coordinates, presence and Telegram `access_hash`
all appear in payloads.

**Decision.** No `phone` column exists anywhere. `scrub()` drops
`phone | email | latitude | longitude | geo | gps | venue | location | access_hash |
file_reference | online | status | read_outbox_max_id` **before persistence**, with
`assert_clean()` re-checking on every non-broadcast write.

**The same argument removed `'location'` from the `media.kind` CHECK.** The frozen enum
reserved a media kind for exactly the data class `scrub()` is required to drop and
`assert_clean()` raises `PIILeak` over — and location is *sensitive*-category data under
Vietnam's PDPL, so it is the last place to leave a hole. `MediaDraft.kind` never listed it
either, so the contract and the DDL disagreed. Both now read
`image|video|audio|voice|file|sticker|link_card|poll`.

**Rejected.** A retention policy or a code-review convention — both are documentation
properties, and this is a data-flow property.

**Consequences.** Structural, not a policy note: **there is nowhere to put a phone
number, so no connector can accidentally persist one.** The non-obvious entry is
Telegram's `access_hash`: it is an **account-scoped capability token**, so persisting
it means storing *the ability to look strangers up*, not merely a record that they
spoke. Same reasoning for `file_reference`. `assert_clean` costs one regex pass per
write, which at a few hundred messages a day is free.

---

<a id="adr-0028"></a>
### ADR-0028 — Redaction must not undo itself

**Context.** A purge deletes rows. Tomorrow's 08:05 run re-fetches the same
conversation.

**Decision.** Every purge writes a `redactions` row with `block_reingest = 1` that
**every ingest path consults before insert**, and the upsert guards
`visibility='redacted_locally'` so a reparse cannot resurrect purged content.

**Rejected.** Deleting rows and trusting the schedule not to notice. A redaction you
*believe* worked but did not is **worse than none**.

**Consequences.** Tested directly: redact a person, crawl a fixture that still contains
them, assert **zero rows**. This is invariant #3 in the test list. It is the item most
likely to be skipped as bookkeeping, and it is the one that makes the feature real.

---

<a id="adr-0029"></a>
### ADR-0029 — Purge tells the truth about SQLite and APFS

**Context.** `DELETE` does not erase bytes; freed pages go on the freelist with content
intact and are recoverable with `strings`.

**Decision.** `PRAGMA secure_delete=ON` **at creation** (it is a persistent header
flag — setting it later does not retroactively zero already-freed pages), `VACUUM`
after any conversation purge, then `PRAGMA wal_checkpoint(TRUNCATE)`. And the tool
**prints** that APFS local snapshots may still hold the pre-purge file.

**Rejected.** Printing a clean success line. Running `tmutil deletelocalsnapshots`
itself — that is a system-wide destructive act and the user's call.

**Consequences.**

```
purged 4,102 items and 118 envelopes (conversation, >365d)
vacuumed: private.db 512MB -> 361MB
note: APFS local snapshots may still contain the pre-purge file.
      `tmutil listlocalsnapshots /` to inspect. Not doing this for you.
```

Honest residual, documented rather than discovered: a purge deletes whole envelopes
that mention the subject, which loses unrelated content in the same payload. That is
the correct trade and it must be stated behaviour, not a surprise.

---

<a id="adr-0030"></a>
### ADR-0030 — Upstream deletes resolve per class; `follow` is locked for Reddit

**Context.** `--respect-upstream-deletes` needs a default, and one global default is
wrong for at least two of the three classes.

**Decision.** `auto`, resolving per class:

| Class | Policy | Why |
|---|---|---|
| `broadcast` | `tombstone` | For a public page, the **fact** of a deletion is often the datum. |
| `joined` | `follow` | Member-only content; the bounded-audience expectation extends to unsending. |
| `conversation` | `follow` | An unsend is a request expressed in the only vocabulary the platform gives the other person. |
| **Reddit (any class)** | **`follow`, forced, unoverridable** | Reddit's Data API terms require dropping deleted content **even when de-identified**. |

**Rejected.** A single global boolean; defaulting `off` (makes the feature decorative);
defaulting `follow` everywhere (destroys the deletion signal that makes a public-page
archive interesting).

**Consequences.** Detection is the hard half. It needs `absence_strikes` (3 broadcast,
2 conversation) inside a **positively covered** range, gated on
`containers.access_state='ok'` **AND** `runs.status='ok'` — otherwise one Facebook
checkpoint marks 4,000 posts deleted. It also costs a rolling re-scan window that the
incremental design does not otherwise need, which pulls directly against Facebook's
pacing budget; decide that window consciously. **Unverified:** the exact Reddit terms
wording, including the reported 48-hour guidance — the primary pages were unreachable.

---

<a id="adr-0031"></a>
### ADR-0031 — `Cap.DELETE_EVENTS` is deliberately **not** set for Telegram

**Context.** MTProto pushes `UpdateDeleteMessages` / `UpdateDeleteChannelMessages`, so
Telegram looks like the one source that reports deletions properly.

**Decision.** The flag stays unset, and the absence sweep stays **mandatory** for
Telegram.

**Rejected.** Setting it — which log-then-project did, contradicting its own body text.

**Consequences.** `MessageDeleted` is documented as *"isn't 100% reliable, since
Telegram doesn't always notify the clients that a message was deleted"*, and
`UpdatesTooLong` / `ChannelDifferenceTooLong` explicitly mean *"I will not enumerate
what you missed, go re-read history."* Under any design that gates the sweep on this
flag, **DM deletions would silently never be detected** — the worst possible default
for a personal DM archive, because it makes the tool a system that specifically defeats
other people's deletions. **Unverified:** whether delete events are replayed on
reconnect for a session that was offline; settle it during the Telegram spike.

---

<a id="adr-0032"></a>
### ADR-0032 — Forward-only enrolment

**Context.** Enrolling a conversation could ingest all available history by default.

**Decision.** Enrolling a conversation target sets `enrolled_at = now` and ingests
**only after it**. Backfill is explicit and per-conversation. There is **no bulk
enrolment command** — no `--all-dms`, no `--all-dialogs`, no `sync-everything`.
`iter_dialogs()` is confined to an interactive `telegram list-chats` that only
**prints** candidates.

**Rejected.** Full history on enrolment with a retention sweep afterwards — by then you
have already copied it, and a Time Machine snapshot has it forever. A bulk flag with a
confirmation prompt — its **existence** is the problem; a prompt does not fix it.

**Consequences.** **This is the highest-leverage control in the whole design and it
costs nothing.** Every other control here is damage limitation for data already in the
database; this one keeps it out and turns corpus size into a series of deliberate
decisions rather than one flag typed once. The floor must hold inside `fetch()`, not as
a post-filter — filtering afterwards means the bytes already crossed the wire.

---

<a id="adr-0033"></a>
### ADR-0033 — Export is default-deny by class; `--include-private` is refused off-TTY

**Context.** The realistic export accident is not malice. It is a scheduled job or a
copy-pasted command writing a file into a synced folder.

**Decision.** Default `broadcast` only, always printing what was withheld.
`--include-private` **requires a TTY** — with a non-TTY stdin it is **refused, not
prompted**. Output paths under iCloud / Dropbox / OneDrive / `~/Library/CloudStorage`,
or inside an untracked git work tree, are rejected. Third-party names are pseudonymized
unless `--include-names` is also passed; the user's own `is_from_self` rows never are.
Every export writes a sidecar `.manifest.json`. **Conversation envelopes are not
exportable at all — no flag exists.** `export` never `ATTACH`es `private.db` unless the
TTY gate has already passed.

**Rejected.** Warn-and-continue (trains the user to ignore the warning). A `--yes` flag
(ends up in a shell alias). Blocking private export entirely (the user has a legitimate
need to search their own DM history, and a tool that refuses gets bypassed with raw
SQL, defeating every other control).

**Consequences.** A scheduled job can **never** emit conversation data — structurally,
not by policy. Removing the conversation-envelope option removes the accident. The
manifest means a hook, a script, or you in six months can check one small file instead
of parsing a multi-GB dump; it also carries Telegram's no-ML-training constraint
(ADR-0063) so the restriction travels with the data.

---

<a id="adr-0034"></a>
### ADR-0034 — Media is a snapshot-at-ingest side-car with explicitly weaker guarantees

**Context.** The governance recon recommended `media_mode: download` for broadcast.

**Decision.** Overridden. `media_mode` defaults to **`link` for all classes at v1**,
with per-target `download` opt-in behind a MIME allowlist and a `max_bytes` ceiling.
Bytes go content-addressed to disk (`media/ab/cd/<sha256>`), **never as SQLite BLOBs**,
and are reachable from the redaction index so a purge removes them too.

**Rejected.** Download-by-default for broadcast — **media is the only unbounded cost in
the project.** A 200k-message Telegram channel is tens of MB of text and potentially
*hundreds of GB* with media, and each download also spends the flood budget. Anti-
trigger, stated plainly: **never enable it globally.**

**Consequences.** Stated honestly rather than discovered: a stored URL is a **dead
pointer** for Telegram (`file_reference` expires), Zalo (token-bearing `*.zdn.vn` CDN
paths) and Facebook. **The replay guarantee covers structured content only.** An
archive that stores URLs looks complete on write and is hollow on read. Trigger to
enable download: the user names a specific target whose images they want, with the MIME
allowlist and `max_bytes` set at the same time.

---

## D. Operations

<a id="adr-0035"></a>
### ADR-0035 — One LaunchAgent, exactly one SQLite writer, no home-grown supervisor

**Context.** Facebook needs an Aqua session and must end by quitting the driver. A
Telegram listener would want a persistent connection. Reddit and X finish in seconds.
Three lifetimes.

**Decision.** **One** LaunchAgent. `…crawlersocial.tick` =
`StartCalendarInterval` 08:05 + 20:35, `LimitLoadToSessionType=Aqua`,
`ProcessType=Interactive`, wrapped in `caffeinate -i`, **deliberately no `KeepAlive`**.
An optional second `StartInterval=900` batch-lane agent is deferred with a trigger.

**Amended 2026-09-01.** This ADR originally specified a second always-on agent,
`…crawlersocial.receivers` (`RunAtLoad` + `KeepAlive{SuccessfulExit=false}`,
`ThrottleInterval=60`), installed **empty at v1** so its restart semantics would be proven
against a real launchd before anything depended on them. It is **cut**, for three reasons
that compound: it had **zero producers** (every push transport is deferred or impossible —
ADR-0036); nothing tested it, because the milestone that installed it verified the *tick*
agent's calendar firing and lid-close coalescing, so the stated benefit was never obtained;
and an always-on process with no job is a failure surface with no benefit. `Cap.PUSH`,
`core/spool.py`, `core/receivers.py`, the `Receiver` Protocol, `run-receivers.sh` and the
spool quota/TTL `doctor` checks go with it.

**Rejected.** A supervisor daemon — launchd already is one, and a second supervisor is
a second thing that can be down. Five plists — four would be identical API-lane
invocations. `cron` — no GUI session, so headful Chrome cannot run. `KeepAlive` on the
tick agent — a batch job that exits nonzero must **stay** exited, not respawn straight
back into a Facebook block. `pmset repeat wake` — waking the Mac at 03:00 to scroll
Facebook is the opposite of the pacing story. And, as of the amendment above, **installing
an agent "empty so its semantics are proven"** — the semantics were not proven by anything,
and the honest version of that argument is "we will find out when we need it."

**Consequences (all verified locally against `man 5 launchd.plist`, macOS 26.5.2).**
launchd **defers a missed `StartCalendarInterval` until wake and coalesces multiple
misses into one invocation** — so a closed lid delays the job and a week away yields
**one** run, not seven. That makes it **non-negotiable that the daily job catches up by
cursor and never by "fetch yesterday"**. `KeepAlive.SuccessfulExit=false` is how the
error taxonomy reaches launchd: a crash exits nonzero and restarts; a `HUMAN` or `STOP`
verdict alerts and exits **zero**, and stays down until `crawler resume`.

**One launchd fact kept for the day it matters, verified locally:** the man page documents
`KeepAlive.NetworkState` as *"no longer implemented as it never acted how most users
expected."* Moot at v1 now that no agent sets `KeepAlive` at all — but if a push-transport
agent is ever written, do not reach for that key to stop an offline restart loop;
`ThrottleInterval` plus exiting **0** when the network is unreachable is the working control.
Also note the man page's own wording: *"If multiple keys are provided, launchd **ORs** them"*
— a `KeepAlive` dictionary is not a conjunction.

---

<a id="adr-0036"></a>
### ADR-0036 — If a push transport ever ships: it never opens SQLite — **NOT BUILT at v1**

> **Status: specified, not built, and nothing in the codebase references it.** No producer
> exists. Telegram listen mode is itself deferred with its own trigger (ADR-0058), and all
> three Zalo transports are deferred or impossible (ADR-0060). This entry is kept because
> the *argument* is right and worth not re-deriving; the trigger that unblocks it is
> "the first real push producer exists", in [../PLAN.md](../PLAN.md) §11.

**Context.** A persistent Telegram listener, an inbound Zalo webhook and a polled HTTP
endpoint look like three different scheduler lanes. Plus a fourth for file import.

**Decision.** A `Receiver` fsyncs signed bytes into `spool/<source>/` and the
**ordinary scheduled `fetch()` drains that directory**. A `Receiver` never opens
SQLite. The spool gets a **256 MB quota and a 72-hour TTL**, checked by `doctor`.

**Rejected.** Letting a listener write rows directly — it breaks the single-writer
invariant, puts a continuous listener in contention with a bursty batch job on the WAL,
and would need a second flock scope for `AUTH_KEY_DUPLICATED`. Leaving the spool
unbounded — its own author admitted it needed a bound.

**Consequences.** Persistent-stream, inbound-webhook and polled-HTTP collapse into
**one** shape, so the scheduler would see **two lanes, not four**. Crash-replay works by
atomic rename into `consumed/`.

**And this is where the collapse argument eats itself, which is why nothing is built.** The
spool and the archive importer are the same shape — `fetch()` over a local path — so the X
data-archive ZIP importer needs **only `Cap.FILE_IMPORT` and a path**: no daemon, no quota,
no TTL, no second agent, no enum flag. `Cap.FILE_IMPORT` is the half with a real user at
v1 (M14). The rest is the half with none. So v1 ships `Cap.FILE_IMPORT` and ships nothing
else from this entry.

**A precondition on ever building it, carried here so it is not rediscovered later.** The
quota and TTL are not bookkeeping: **a spool holds unencrypted third-party message bytes
outside `private.db` and outside its retention sweep.** That is exactly the category of data
the governance design works hardest to bound, sitting in the one place a naive design forgets
to bound it. If a push transport ever ships, the 256 MB quota and 72-hour TTL ship in the
same commit, enforced by `doctor` — not as a follow-up.

---

<a id="adr-0037"></a>
### ADR-0037 — One `TokenBucket` behind one `Budget`; `mode:` is a comment

**Context.** Facebook's "200 posts, 25-minute sessions, twice daily" and Reddit's
"100 QPM" look like different mechanisms.

**Decision.** One `TokenBucket` behind one `Budget` object for all five connectors,
configured either from a published quota (Reddit, header-corrected) or a paranoia
budget (Facebook, no feedback possible). `mode:` appears in the config **as a comment
for humans that no code branches on**.

**Rejected.** Per-connector bespoke pacing — guarantees the paranoia settings and the
quota settings drift apart. Two code paths keyed on `mode` — **if anyone ever writes
`if mode == 'paranoia'`, the abstraction has failed and must be split.**

**Consequences.** `Budget` unifies the three things that looked unrelated —
`max_items` (`--limit N`), `deadline_ts` (Facebook's 25-minute session cap),
`spend_units` (X's per-run billable ceiling), `max_requests`, and the bucket. ~20
lines, and it is the one piece of pacing that genuinely **is** shared, unlike the
bucket constants themselves. PLAN.md §8's pacing table becomes the `facebook` entry
with the numbers **unchanged**. Facebook publishes no rate headers, which is exactly
why its numbers must start conservative: the failure signal is a checkpoint, not a 429.

---

<a id="adr-0038"></a>
### ADR-0038 — X's monthly allowance is money, not rate → `usage_counters`

**Context.** X bills per resource returned. Every other source's limit is a rate.

**Decision.** A persisted `usage_counters(source, period, metric, value)` checked
**before the run starts**, in addition to the bucket.

**Rejected.** Expressing the monthly cap as a token bucket — it gives a refill rate of
0.004/s and **no way to answer "how much have I spent this month."** A bucket smooths a
rate; a counter enforces a budget. Two mechanisms because there are genuinely two
things.

**Consequences.** X is the only connector where a pagination bug costs **dollars**
rather than time or quota. The spend cap is set in the X console **as well as**
locally, so a unilateral repricing cannot produce a surprise bill. `runs.cost_micros`
records per-run spend so cost drift is visible in the data, not only on a statement.
Defaults are all spending decisions: `exclude=retweets`, replies **off** (thread
reconstruction bills every reply, so one viral post with 2,000 replies is real money
and the cost scales with *other people's* engagement), backfill capped at 200
posts/account, and `--limit N` bounds **posts fetched** because that is the billable
unit.

**On price, corrected.** This entry previously said "every price figure is unverified".
That is no longer true: two independent recon passes fetched
`docs.x.com/x-api/getting-started/pricing` on 2026-09-01 and agree on every figure, so the
published prices are `[verified]` (PLAN.md §12 C14). What survives is a different and
sharper obligation — **`console.x.com` is the billing authority and docs lag** — so the
`spend_cap` is typed as a **dollar ceiling** the user enters at enable time rather than
derived from a price this plan believes, and the dry run prints the price it read from the
console next to its projection.

**One metric, not two.** `usage_counters.metric` is `CHECK`-constrained to `'cost_micros'`
(DATA-MODEL §3). An earlier draft allowed `'reads'` as well, and two frozen configs promptly
disagreed about which one the cap used — a governor reading a metric the ingest path does not
write sees a month-to-date of **zero** and never fires. `cost_micros` is the survivor because
it survives a repricing; `reads` does not. A test asserts the governor reads the metric the
ingest path writes.

---

<a id="adr-0039"></a>
### ADR-0039 — X runs at 01:00 UTC, and all retries are bounded by the UTC day

**Context.** X deduplicates billing within a 24-hour UTC window: requesting a resource
already charged for in that window incurs no additional charge.

**Decision.** Schedule the X run at **01:00 UTC** (08:00 ICT) and bound every retry to
the same UTC day.

**Rejected.** Any convenient local time — a midnight-straddling run is **double-billed**
for the same resources.

**Consequences.** Same-day retries are free; 23 hours of retry headroom sit inside one
billing window; and 08:00 local is a natural morning-digest time for a UTC+7 user. This
is a case where the **billing model, not convenience, picks the cron time**, and the
config carries a comment saying so because it otherwise reads as arbitrary.

---

<a id="adr-0040"></a>
### ADR-0040 — Six dispositions projected onto an integer exit table

**Context.** Failures need to be handled uniformly, and some day some connector will
run in another process.

**Decision.** `OK / RETRY / WAIT / HUMAN / STOP / DROP`, plus `SUSPECT` which is never
raised by a connector but detected by core (ADR-0010). Projected onto
`0 / 69 EX_UNAVAILABLE / 75 EX_TEMPFAIL / 77 EX_NOPERM / 78 EX_CONFIG / 86 BLOCKED`.
`DISARMING = {77, 78, 86}`. `classify(exc) -> Verdict` is **pure** and
fixture-testable.

**Rejected.** Booleans and ad-hoc exception handling per connector — one place must
decide "never auto-retry a block"; five places would eventually get it wrong, and the
one that got it wrong would be the one that costs the account.

**Consequences.** The taxonomy survives any process boundary (launchd today, a Zalo
Node sidecar later) because a subprocess boundary can only carry an integer. **Verified
locally** against `$(xcrun --show-sdk-path)/usr/include/sysexits.h` on macOS 26.5.2:
`EX_OK 0`, `EX_UNAVAILABLE 69`, `EX_TEMPFAIL 75`, `EX_NOPERM 77`, `EX_CONFIG 78`.
**`86` is not a sysexits value** — `EX__MAX` is 78 — it is a project-local code chosen
deliberately outside the sysexits range so a hard block cannot be mistaken for a
conventional failure, and clear of the shell's reserved 126/127 and 128+n.

---

<a id="adr-0041"></a>
### ADR-0041 — STOP disarms the schedule and never retries

**Context.** A blocked source that keeps being retried on a schedule is how a temporary
problem becomes a permanent one.

**Decision.** `STOP` writes `sources.state='stopped'`, and every subsequent scheduled
run for that source becomes an **immediate no-op exit 0** until
`crawler resume --source X`. launchd keeps firing on time and nothing happens.

**Rejected.** Exponential backoff without a floor; a retry counter that eventually
gives up. Both keep touching a source that has already told you to stop.

**Consequences.** A retry loop is precisely how a temporary Facebook block becomes a
permanent one, and how a Telegram `PeerFloodError` — **account-wide, no defined
duration, cannot be waited out** — turns into an account loss.
`AuthKeyDuplicatedError` is classified `HUMAN` rather than `STOP` because the fix is
`crawler login`, but both are in `DISARMING`, so the operational effect is identical:
the source stays down until a human acts.

---

<a id="adr-0042"></a>
### ADR-0042 — WAIT honours server-supplied durations literally

**Context.** Some sources tell you how long to wait. Some do not.

**Decision.** Where a server supplies a duration, obey it **exactly**. Telegram's
`FloodWaitError.seconds` is authoritative; if it exceeds **300s** the cursor is
checkpointed and the run exits **75** for the next tick to resume. Facebook supplies
nothing, so it gets policy backoff **15m → 1h → 4h → stop**, per PLAN.md §8 unchanged.

**Rejected.** A single house backoff curve applied to everyone — it would either ignore
Telegram's authoritative number (and teach Telegram's heuristics that this account
behaves like a bot) or invent one for Facebook that pretends to knowledge it does not
have.

**Consequences.** The asymmetry in the config (`on_flood_wait: sleep_exact` vs
`on_soft_block: [900, 3600, 14400]`) is honest, not inconsistent: it records which
sources give feedback and which do not.

---

<a id="adr-0043"></a>
### ADR-0043 — Secrets: Keychain, loud failure, two credentials that live at file paths

**Context.** Six kinds of credential, one laptop, one scheduled job.

**Decision.** macOS Keychain via `security(1)`, resolved `env → Keychain → LOUD
failure that prints the literal `security add-generic-password` command to run`. Never
a silent fallback to a file. Two credentials go to 0600/0700 paths **outside the git
worktree**: the Telegram `.session` and the Chrome profile.

**Rejected.** A gitignored `.env` — one `git add -A` from a commit and one Time Machine
snapshot from a plaintext copy that outlives the mistake. A 0600 `secrets.yaml` in the
repo — same problem, and it *looks* committable.

**Consequences.** The `.session` file is a bearer credential **equivalent to password
plus 2FA**; `AUTH_KEY_DUPLICATED` permanently destroys it on any concurrent use from
two IPs, so it is `Cap.SINGLE_FLIGHT`, flocked, never in a synced directory, and never
copied to a second machine (get a second session by logging in again). Both live
outside the worktree so that **no `.gitignore` mistake can ever stage them**.
**Unverified:** whether `-T <binary>` ACLs meaningfully restrict a `uv run python`
caller. Test it; if they do not, say so rather than implying protection you do not have.

---

<a id="adr-0044"></a>
### ADR-0044 — Zalo's single-use refresh token gets crash-safe rotation

**Context.** Zalo's OA refresh token is **single-use**: each exchange returns a new one.

**Decision.** `flock`, write the **new** refresh token to the Keychain **before** using
the new access token, and keep the previous value under `refresh_token.prev` for one
cycle.

**Rejected.** The natural ordering — use the new access token, then persist its
refresh token — because a crash in between locks you out until a manual browser
re-authorization.

**Consequences.** A real and entirely avoidable outage becomes a recoverable one. The
lifetimes themselves (reported as 25h access / 3-month refresh by the docs mirror,
against a widely repeated 1-hour third-hand figure) are **unverified and load-bearing**
— confirm before building.

---

<a id="adr-0045"></a>
### ADR-0045 — Plugin discovery is a hardcoded dict of lazy `"module:factory"` strings

**Context.** Five connectors, one repo, one person.

**Decision.** `REGISTRY: dict[str, str]` resolved through `importlib`, and
`registry.py` is the **only** module permitted to import a connector.

**Rejected.** `importlib.metadata.entry_points` — it would require each connector to be
a distributable package (running `uv sync` to add a file to your own repo), it imports
eagerly (dragging selenium into `crawler reparse`), and it fails **silently** on a
typo. `pkgutil` naming-convention discovery — same silence, less explicitness.

**Consequences.** A typo yields `UnknownSourceError` **listing the valid names**. The
dict is greppable and statically analysable, so find-references and type-checking work.
Lazy **string** resolution is the one bit of indirection worth its ugliness: it is what
makes "parse needs no credentials and no browser" mechanically testable rather than
aspirational. Adding entry points later is a four-line `REGISTRY.update(...)`, preserved
by keeping `registry.py` the sole importer.

---

<a id="adr-0046"></a>
### ADR-0046 — One `uv` project with per-connector dependency groups

**Context.** thin-core proposed five separate `uv` projects with five lockfiles for
dependency isolation.

**Decision.** Overridden per Judge 1. One project, per-connector dependency groups.
`uv sync --all-groups` locally; the test lane runs `--group core --group reddit` and
asserts `'selenium' not in sys.modules` after a full reparse.

**Rejected.** Five projects — complexity spent on isolation the user does not yet need,
paid for on every single change.

**Consequences.** The invariant is proved **mechanically** rather than by convention.
Trigger to split: an actual dependency conflict between two connectors, or the tool
needing to run on a second machine.

---

<a id="adr-0047"></a>
### ADR-0047 — A capability may exist only if core branches on it

**Context.** Capability enums rot into decoration. In a year there are twenty flags and
three of them are lies.

**Decision.** Reviewed every time the `Cap` enum changes: if nothing in core has an
`if caps & X`, the flag is documentation and belongs in the README. Paired with
`capabilities_note() -> str` — free text that core **renders and never branches on**.

**Rejected.** A rich descriptive capability vocabulary — it looks like design and
behaves like comments.

**Consequences.** Every flag in [../ARCHITECTURE.md](../ARCHITECTURE.md) §6 names its
exact branch point. `capabilities_note()` is grafted from thin-core because
`BACKFILL_CAPPED` tells the user nothing, while *"backfill: groups only, via
`getGroupChatHistory`; no 1:1 DM history method exists"* is exactly what they need to
know about Zalo.

**Two corollaries added after the flags and the source docs disagreed in five places.**

1. **The `caps = (...)` block in each source doc is the source of truth**, because it is the
   thing that ships as code. ARCHITECTURE §6's per-connector matrix is *derived* from those
   blocks and says so; on any conflict the source doc wins and the matrix is the bug. This is
   not pedantry — the flags in that matrix are ones core *branches on*, so a wrong cell meant
   scheduling a comment-expansion phase for a connector with no such code, or billing the
   user for a metric refresh the connector exists to avoid.
2. **`Cap.PUSH` is deleted, not deferred-in-place.** The rule reads "a capability may exist
   only if core branches on it"; the honest extension is *and only if something can set it*.
   `PUSH` had no producer at v1 (ADR-0036) and gated a `Receiver`, a spool, a quota, a TTL,
   two `doctor` checks and a second LaunchAgent. It returns with the first push transport.
   `Cap.FILE_IMPORT`, which has a real user at M14, stays.

A third rule the same review produced: **`BACKFILL_CAPPED` implies `BACKFILL` for CLI
argument validation.** Otherwise Reddit — which correctly sets only the capped flag — has
`--since` rejected at argument parsing while its own document documents `--since` as working.

---

<a id="adr-0048"></a>
### ADR-0048 — Provenance is two integers plus a bounded N:M

**Context.** "Which bytes produced this row?" must be answerable, but a naive N:M table
grows one row per (item, metric-refresh) forever.

**Decision.** `items.first_seq` / `items.last_seq` point back into the envelope store
for the cheap `crawler explain <item>`. `item_envelopes` records **only
`role='primary'`** writes — the envelopes that produced or *updated content* — never
metric batches.

**`role` is a real column, and it has to be.** An earlier draft of this ADR asserted
`role='primary'` while the frozen `CREATE TABLE item_envelopes` had no such column: five
places across four documents wrote or filtered on a value the schema could not hold. That
made the boundedness guarantee an unwritten convention in the writer — nothing enforced it,
nothing could audit it afterwards, and nothing distinguished a content envelope from a metric
envelope in the table. It is now
`role TEXT NOT NULL DEFAULT 'primary' CHECK (role IN ('primary'))`, in the primary key. The
single-valued `CHECK` is the point: **adding a second role is a migration**, which is exactly
the review checkpoint this decision wants.

**Rejected.** Full N:M provenance on every write — one row per (item, metric refresh),
forever, whose entire information content is "still 220". *(The magnitude claim that used to
sit here — "over a million rows a year" — was carried into three other documents as a claim
about **envelope** rows, where it was wrong by about a hundredfold. It is correct only about
this rejected design, and DATA-MODEL §6 now does the arithmetic properly.)*
No N:M at all — two integers cannot express an item assembled from a feed snapshot plus
a post page.

**Consequences.** Full content provenance survives; metric churn does not inflate it; and
the bound is a schema property rather than a promise.

---

<a id="adr-0049"></a>
### ADR-0049 — `FixtureConnector`, `tick --fixture`, and golden-parse snapshots

**Context.** Most of the real logic is offline. Testing it must not require a browser
or a network.

**Decision.** `FixtureConnector` satisfies the same `Protocol` and replays recorded
envelopes in order respecting `budget`, **delegating `parse()` to the real connector**.
`crawler tick --fixture tests/fixtures/` runs the **entire production pipeline**.
Fixtures are captured by `crawler capture --redact`, never hand-copied. Golden
snapshots are `sha256(canonical_json(ParseResult))` per fixture per `parser_version`.

**Rejected.** A `FixtureConnector` with its own logic — the moment it reimplements
anything, the test stops testing production. Hand-copied fixtures — they drift from
what the transport actually returns.

**Consequences.** Budget accounting, commit ordering, coverage checks, gap recording,
upserts, sweeps and SUSPECT detection are all covered with no browser and no network —
cheap **only because `Envelope` is the seam** (ADR-0002). Bumping `parser_version` must
change the golden snapshot deliberately, which is how you notice that an "innocent"
selector tweak silently stopped populating a field. **No real DM or private-group
message enters the repo, redacted or otherwise.**

---

<a id="adr-0050"></a>
### ADR-0050 — `codec` column from day one; zlib at v1, zstd + dictionaries at connector two

**Context.** Compression choice is cheap to defer and expensive to migrate.

**Decision.** `codec` column (`raw|zlib|zstd`) and a `zdict` table for trained
dictionaries exist in the **frozen DDL**. zlib is used at v1; zstd with trained
dictionaries arrives with connector two.

**Rejected.** zstd from day one — the measured 5.5x-vs-1.8x win is specifically for
**small** JSON/msgpack records (Telegram's 100-message slices, Reddit listings) where
there is no window to find repetition in 754 bytes. Facebook's large HTML slices
already compress ~8:1 under zlib, so v1 gains nothing and pays a dependency.

**Consequences.** The switch is a flag flip, not a migration. Dictionaries are keyed on
`(source, kind)`, retrained on drift, and **never deleted**, because `dict_id` is on the
blob. Trigger to move to Python 3.14 (which makes `compression.zstd` stdlib and drops
the `zstandard` dependency): Telethon 1.44 confirmed green on 3.14 beyond CI.

---

<a id="adr-0051"></a>
### ADR-0051 — FTS5: one unconditional-trigger external-content index per file

**Context.** A single database with two privacy classes needs the FTS insert trigger to
be **conditional** on the container's privacy, while the idiomatic delete trigger from
the SQLite docs is **unconditional**.

**Decision.** One index per **file**, `unicode61 remove_diacritics 2`, `detail=full`,
no prefix index. Because each file holds exactly one privacy half, both triggers are
**unconditional**.

**Rejected.** One database with conditional triggers — this is a **verified corruption
trap**, reproduced A/B on clean databases on SQLite 3.51.0. A conditional insert paired
with an unconditional `'delete'` issues an FTS5 delete for rowids never in that index.
Searches keep returning correct results, `INSERT INTO fts(fts) VALUES('integrity-check')`
returns **OK**, and `PRAGMA integrity_check` returns **ok** — then a later, unrelated
write fails with `database disk image is malformed (11)` and every subsequent write to
`items` fails. `detail=column` (saves ~46%) — it **disables phrase queries**, and
Vietnamese is written with spaces between **syllables**, so phrase queries are the
primary query form. `remove_diacritics 1` — cannot handle characters composed with
multiple combining marks, which is exactly Vietnamese (`ế` = e + circumflex + acute).

**Consequences.** The file split makes that corruption class **unrepresentable**. The
tokenizer is recall-first: `ma/má/mà/mã/mạ` are five different words that fold to one
token, and precision is recovered with a `LIKE` second stage over the tiny candidate
set — which matches how people actually type Vietnamese into a search box. **Never
audit the privacy split with `count(*)` on an FTS table** — an external-content FTS5
table reads the *content* table's row count and will falsely pass; use `MATCH`.
`doctor --repair` offers `INSERT INTO items_fts(items_fts) VALUES('rebuild')`.

---

<a id="adr-0052"></a>
### ADR-0052 — Python 3.13 and a SQLite ≥ 3.45 assertion in `db.connect()` and `doctor`

**Context.** JSONB, `->>`, STRICT tables and FTS5 `contentless_delete` all have SQLite
version floors.

**Decision.** Python 3.13, and an explicit assertion:

```python
if sqlite3.sqlite_version_info < (3, 45, 0):
    raise SystemExit(f"need SQLite >= 3.45 (JSONB); got {sqlite3.sqlite_version}")
```

**Rejected.** Assuming the system SQLite. **A `uv`-managed interpreter bundles its
own SQLite**, which is not necessarily the 3.51.0 the system CLI links. Using
`sqlite3.version` — it was **removed in Python 3.14**; use `sqlite_version_info` only.

**Consequences.** Verified on this machine: pyenv 3.13.13 links SQLite 3.51.0 — which
says nothing about what `uv` will provision, hence the assertion in two places rather
than a comment. Trigger to move to 3.14: Telethon 1.44 confirmed green on 3.14 beyond
its CI commit, on the actual project interpreter.

---

<a id="adr-0053"></a>
### ADR-0053 — Two SQLite one-shot decisions: `local_day` STORED, and canonical query text

**Context.** Two SQLite behaviours cannot be retrofitted after the fact.

**Decision.**
1. `local_day TEXT GENERATED ALWAYS AS (date(published_at,'unixepoch','+7 hours'))
   **STORED**` is in the **initial DDL**.
2. The canonical timeline query text lives in a **named module constant** and is never
   retyped. `ANALYZE` runs after the first substantial crawl and in the daily job.

**Rejected.** Adding `local_day` later. The restriction is documented
(`sqlite.org/gencol.html`) and re-executed here, **but the engine is not the guard**: on
3.51.0 `ALTER TABLE … ADD COLUMN … STORED` **succeeds on an empty table** and fails with
`cannot add a STORED column` only once the table has rows. So a migration tested against an
empty dev database passes and then fails on your real one, which is the worst possible place
to find out. Put it in the initial DDL because it costs nothing there and is portable across
engines that *do* enforce the rule — not because SQLite will stop you.
Letting the timeline query be retyped at each call site — **partial indexes apply only
when the query repeats their predicate verbatim.** Drop `AND visibility='visible'` and
the planner silently falls back to a different index and **returns deleted rows**; at
low row counts before `ANALYZE` it chose `idx_items_parent` plus a
`TEMP B-TREE FOR ORDER BY`.

**Consequences.** Vietnam is UTC+7 **year-round with no DST**, so the `+7 hours`
constant is safe to freeze — this is one of the few places a hardcoded offset is
correct rather than a latent bug. "How much did I collect per day, in my timezone"
becomes an index scan instead of a full-table `date()` computation.

---

## E. Sources

<a id="adr-0054"></a>
### ADR-0054 — Reddit uses `prawcore` for auth and `httpx` for the wire; never PRAW in the ingest path

**Context.** PRAW 8.0.3 and prawcore 4.0.0 are actively maintained (verified from PyPI,
mid-2026 releases). PRAW is the obvious client.

**Decision.** `prawcore` mints and refreshes the bearer token; `httpx` issues the
request so the **response bytes survive into the raw store**. Always `raw_json=1`.
Auth is the **code-grant refresh-token** flow. PRAW stays a dev-only convenience for
interactive spelunking, out of the ingest path.

**Rejected.** PRAW end-to-end — it parses responses into model objects and **discards
the bytes**, which breaks raw-first outright; you would be storing a re-serialized
model dict and calling it "raw". Password grant — it stores the account password, and
PRAW's own docs note that storing the 2FA secret alongside the credentials defeats the
point, so password grant and 2FA do not coexist cleanly. Application-only read-only —
cannot see private subreddits, subscriptions or saved items. Hand-rolled OAuth —
prawcore already handles refresh and 429 correctly.

**Consequences.** Two added dependencies. `raw_json=1` stops Reddit HTML-escaping
`&<>` in body text, so every re-parse does not have to un-escape. **Browser automation
of Reddit is never justified:** unauthenticated `.json` has returned 403 since ~28 May
2026, `old.reddit.com` requires login since ~30 June 2026, and Rule 8 now names
unauthorized scraping explicitly — so a logged-in browser crawl would carry the same
account risk as the Facebook connector while returning **strictly less** than a free
API call. Reddit is the concrete proof that transport is a per-connector decision.

---

<a id="adr-0055"></a>
### ADR-0055 — Reddit ships one backend at v1; the degraded path is specified but unbuilt

**Context.** capability-registry proposed three interchangeable Reddit backends
(`oauth` / `feeds` / `archive`) behind one contract. Judge 1 called it speculative
generality for a source that may never authenticate.

**Decision.** One backend (`oauth`) at v1. If R0 is declined, ship the **named**
fallback instead — authenticated Atom feeds (`user=&feed=` tokens from `/prefs/feeds`)
for live-edge discovery, plus Arctic Shift for bodies, comments and metrics at a ~36h
metric lag. `sources.transport` records which is in use. **No three-way abstraction is
built.**

**Rejected.** Building all three backends up front. Making OAuth a hard prerequisite
with no fallback — that leaves the user with nothing if the application is declined.
Falling back to **scraping** — the point of the capability contract is that the
fallback is a *different legitimate transport*, not a less legitimate one.

**Consequences.** It is one backend or the other, chosen once. **Phase 0 / R0** is a
credential spike, not code: apply on day one, because self-service registration ended
in November 2025 and small personal read-only projects are widely reported to be
declined or ghosted. That is the **highest-variance assumption in the whole plan and it
is free to test**. Arctic Shift is a single-maintainer, donation-funded service
carrying a load-bearing backfill role — mitigate by pulling the monthly `.zst` dumps to
local disk; the dumps are the artifact, the API is convenience.

---

<a id="adr-0056"></a>
### ADR-0056 — Every scraping path for X is rejected permanently, with dates

**Context.** The scraping option saves roughly $15/month.

**Decision.** Rejected permanently, and the rejection is written into the plan **with
dates** so it cannot be relitigated.

**Rejected.**
- **twscrape** — alive at 0.20.1 (2026-08-25), and *that is what makes it a trap*: its
  rate-limit strategy is account **rotation**, which presumes disposable accounts.
- **RSSHub / RSS-Bridge with session cookies** — scraping wearing an RSS costume, with
  your own account's cookie, at identical suspension risk and less control.
- **Nitter self-hosting** — X Corp's 2026-08-24 cease-and-desist demanded takedown of
  self-hosted instances **and the repository**. This closed the last "run it yourself
  quietly" option one week before this plan was written.
- **Third-party resellers** — ~33× cheaper on paper, unlicensed scraping operations
  with a Stripe checkout, and they route your reading interests through an
  unaccountable third party.
- **"Use twscrape only when the API budget is exhausted"** — the tempting middle path
  and the worst option available: the scraper runs precisely when you are least
  watching.

**Consequences.** The saving is ~$15/month against **permanent suspension of the
account holding the user's real identity**. That asymmetry is the easiest trade in the
project. X also rotates guest tokens, GraphQL doc_ids and detection heuristics every
2–4 weeks, so the scraping path carries a permanent maintenance tax on top.

---

<a id="adr-0057"></a>
### ADR-0057 — X DMs come exclusively from the free data-archive ZIP

**Context.** The X API prices DM Events, so DM ingestion is billable-in-principle.

**Decision.** All X DM ingestion goes through the user's own **data-archive ZIP**. The
DM Events API is never called.

**Rejected.** API-based DM ingestion — more expensive, more OAuth scopes, less complete
(the archive has no ~3,200-post ceiling), and **worse provenance**: an export the user
personally requested for their own account is a far more defensible story than a scope
that could be pointed anywhere. Skipping the importer as "not automatable" — it runs a
few times a year, which is exactly what the one-time run mode is for.

**Consequences.** It lands **perfectly** on raw-first — **the ZIP *is* the payload**.
It also proves the `Cap.FILE_IMPORT` shape — the half of ADR-0036 that has a real user —
which Facebook and Zalo will both want later. Note the governance trap:
X reads like a broadcast source right up until the DM folder lands in the same store,
so the archive importer gets the full conversation treatment — `private.db`,
forward-only, export-gated.

---

<a id="adr-0058"></a>
### ADR-0058 — Telegram: poll, do not listen

**Context.** MTProto offers a real-time update stream. A listener looks strictly better
than polling.

**Decision.** Poll on the schedule. A listener is a deferred, optional second mode.

**Rejected.** A persistent listener as the default. **Telegram's update stream is
explicitly gap-tolerant:** `UpdatesTooLong`, `UpdateChannelTooLong`,
`DifferenceTooLong` and `ChannelDifferenceTooLong` all mean *"I will not enumerate what
you missed, go re-read history."* `MessageDeleted` is documented as unreliable. So a
listener **never removes the id-cursor reconciler — it only makes it find less**, while
adding an always-on process and real `AUTH_KEY_DUPLICATED` exposure the moment a stray
manual run touches the same session file.

**Consequences.** Telegram keeps the same operational shape as every other source: one
process, one flock, one exit code. The cursor is
`iter_messages(entity, reverse=True, offset_id=last_id, wait_time=2.0)` at the
library's fixed 100-per-request chunk, plus a bounded ids-refresh window (500 ids
broadcast, 2,000 or 30 days conversation) because edits, views and reactions mutate and
**Telegram offers no "changed since" query**. Listen mode is a strict superset — same
envelopes, same store, reconciler still on a timer — so switching later costs no
migration. **Never run it alongside a pull run against the same `.session`.**

---

<a id="adr-0059"></a>
### ADR-0059 — Telegram read-only is enforced by absence, and the client is pinned

**Context.** `PeerFloodError` is account-wide, has no defined duration, and cannot be
waited out. Sending is what earns it.

**Decision.** The Telegram connector **imports no send / forward / join method**, and a
unit test asserts it. Likewise there is no `--all-dms` / `--all-dialogs` /
`sync-everything` command anywhere. Telethon is pinned `==1.44.0` with
`cryptg>=0.6.0`, installed **from PyPI, never a GitHub git URL**, and the wheel is
vendored.

**Rejected.** A `read_only: true` config flag — **a toggle is a thing that can be set to
true.** Absence of capability is the only setting that holds. Pyrogram — dead (repo
archived 2024-12-23, last release 2023-04-30). Telethon v2 — alpha only, **zero PyPI
releases**. Kurigram 2.2.25 is genuinely alive and is the named escape hatch, but it is
a single-maintainer fork of an abandoned upstream and this project needs one connector
to be boring.

**Consequences.** The GitHub repo was **archived 2026-02-21** and development moved to
`codeberg.org/Lonami/Telethon`; the README says GitHub *"may be deleted in the
future."* That is a relocation, not abandonment — but it means every vendored-source,
issue and CONTRIBUTING link must point at Codeberg, and the wheel must be vendored.
Every Telethon symbol stays inside `connectors/telegram/` so a Kurigram swap is a
one-directory change (ADR-0003).

---

<a id="adr-0060"></a>
### ADR-0060 — Zalo is deferred with its slot specified; Selenium against `chat.zalo.me` is rejected outright

**Context.** Zalo is the dominant Vietnamese messaging app and matters most to this
user. Every available transport fails the actual goal.

**Decision.** Defer all three Zalo transports. Ship now: the three transports declared
as **separate entries under one source** (`zalo.oa` / `zalo.bot` / `zalo.user`) with
honest `capabilities_note()` text, the `user_withdraw`-shaped purge path built
generically for **every** source, and the `containers.content_unavailable` flag.

**Rejected.**
- **`zalo.oa`** — genuinely reads conversation history, but requires a business-verified
  Official Account (Vietnamese business licence plus the legal representative's ID) and
  a paid package. A business capability the user could unlock by registering a *hộ kinh
  doanh*, not something to assume. **Trigger:** they do.
- **`zalo.bot`** — free to individuals, and its Python client is a `python-telegram-bot`
  fork so the connector would be close to a copy of a path already built. But it is
  strictly **forward-only with zero history read**: it creates a new inbox, it does not
  recover an old one. **Trigger:** the user wants a new Zalo inbox archived going
  forward and accepts that no history comes with it.
- **`zalo.user`** (zca-js) — the only living unofficial personal client (v2.1.2,
  2026-03-17, dependabot-only since April 2026). It has `getGroupChatHistory` but **no
  1:1 DM history method at all**, is Node-only (the Python `zlapi` was archived
  2024-11-24), and its tracker carries a March-2026 report of Zalo **wiping a user's
  entire friend list**. **Trigger:** explicit request **and** a secondary account they
  can afford to lose, behind a hand-edited flag and `--i-accept-ban-risk`, never
  started by the scheduler.
- **Selenium against `chat.zalo.me`** — **no trigger; rejected permanently.**

**Consequences.** The Selenium rejection is the concrete proof that transport is a
per-connector decision: Zalo Web's request params are **AES-encrypted with a
per-session `zpw_enk` key**, so a driver gets no usable network JSON and is reduced to
scraping an obfuscated virtualised DOM with **no stable message ids for deduplication**
— at the same ban risk as zca-js, an order of magnitude slower, over history the
desktop client may not even have synced. **Browser automation is right for Facebook
precisely because Facebook renders the content you want; Zalo does not clear that bar.**
Opt-in E2EE means Zalo coverage is silently incomplete over time, which is why
`content_unavailable` exists: the archive must be able to say *"thread exists, content
not retrievable"* instead of looking complete.

---

<a id="adr-0061"></a>
### ADR-0061 — Delivery order: Facebook → Telegram → Reddit → X

**Context.** The winning proposal put Reddit second. Overridden on the recon evidence.

**Decision.** Phase 0 fires **four** day-one external asks in parallel, because each has a
multi-day latency nobody controls: **R0** the Reddit credential application, **T0** the
Telegram `api_id`, **X0** the X data-archive request, **P0** the Facebook profile clock.
Plus FileVault on, both Keychain items generated, and the FTS5-in-SQLCipher answer settled.
Then **Facebook → Telegram → Reddit → X (archive first, REST second, behind
`enabled: false`)**.

**Rejected.** Reddit second — for four reasons in order of weight. **(1) Sequencing
risk:** Reddit's credentials may simply not exist; building the contract's first real
test on a source that may never authenticate is unacceptable, while Telegram's
`api_id`/`api_hash` are **self-issued with no review queue**.

> **Reason 1's counterpart premise is checked, not assumed — this is T0.** "Self-issued
> with no gate" is overstated as usually written. `my.telegram.org`'s *Create application*
> form is reported to return a bare `ERROR` with no diagnostic for a substantial number of
> accounts (Telethon issue #4661, opened 2025-07-16, never resolved and now frozen because
> the GitHub repo was archived 2026-02-21), and every reported workaround is folklore —
> different browser, incognito, different network, wait days. The accurate statement is
> **"no review queue, but the self-service form is known to fail opaquely for some
> accounts."** Reddit's credential risk gets a free day-one spike for exactly this reason;
> its counterpart premise deserved one too and now has it. **If T0 fails, Telegram is not
> connector #2** — the slot goes to whichever of Reddit (if R0 came back approved) or the X
> archive importer is available. That call is pre-made in PLAN §6 M0 rather than discovered
> at M11.

**(2) It tests the architecture
where it was chosen:** Telegram is the exact case that eliminated thin-core — one
connector, one session file, emitting broadcast channels **and** private conversations.
Building it second proves `containers.shape`/`privacy`, the two-file routing, the
`enrolled_at` floor, the `viewer_account_id` CHECK and one `items` writer serving both
shapes **at the earliest possible moment**. If the design is wrong you find out at
connector two, not connector four. **(3) Maximally different from Facebook** on every
axis that matters; Reddit differs on transport but sits in the same broadcast half.
**(4) Free**, and it is the source where browser automation would be most obviously
wrong — the architecture's central thesis made concrete. Building all five connectors
before running any — the contract is a hypothesis until two genuinely different
transports have used it.

**Consequences.** Facebook ships against PLAN.md **M1–M10** (Phase 1); only the
machinery Facebook needs gets built (envelopes, cursors, coverage — always `opaque` —
budget, verdicts, the SUSPECT trio, the run ledger, the canary). X goes last because it
is the only connector where a bug costs **money** rather than time, and because its
pricing model is seven months old and has changed three times in three years.

---

<a id="adr-0062"></a>
### ADR-0062 — The Facebook extraction spike is a parse-quality gate, not a project gate

**Context.** The original plan made the extraction spike — *can we read GraphQL **response
bodies**?* — a project-wide go/no-go that every later milestone hung off. In the current
plan the spike is a half-day timebox inside **M2**, the walking skeleton.

**Decision.** Demoted. The Facebook connector captures `fb.feed_html` viewport snapshots
**from day one, unconditionally, whatever the spike says**. If response-body capture works,
capture `fb.graphql.feed` **alongside** it and have the parser prefer the richer kind, with
the HTML history still replayable either way.

**Rejected.** Keeping it as a project gate — choosing wrong there costs **every post
collected before you notice**, and the choice is not reversible under the original
design because you would have stored only one kind.

**Two facts settled the spike's own shape before it runs.** Selenium's **BiDi network module
documents no way to read a real server response body** — the method set is `addIntercept`,
`removeIntercept`, `continueWithAuth`, `continueWithAuthNoCredentials`, `cancelAuth`,
`failRequest`, and the `continueResponse` family *provides* a response rather than reading
one *(verified from selenium.dev, 2026-09-01)*. **SeleniumBase CDP Mode does** —
`add_handler(mycdp.network.ResponseReceived, …)` then
`page.send(mycdp.network.get_response_body(request_id))` returning `(body, is_base64)`
*(verified from the SeleniumBase repo's own `examples/cdp_mode/raw_xhr_sb.py`)*. So the
candidate the original plan led with is out on documentation alone, in ten minutes.

**Consequences.** Endorsed by Judge 1 as one of the top three grafts (from
log-then-project). It is only possible because `Envelope.kind` is free-form and
`parse()` dispatches on it, so two kinds can coexist for the same target without a
schema change. Facebook's coverage claim stays `COVER_OPAQUE` either way (ADR-0009).

---

<a id="adr-0063"></a>
### ADR-0063 — Legal baseline: Law 91/2025 + Decree 356/2025; no household exemption assumed

**Context.** The originating brief assumed Decree 13/2023/NĐ-CP.

**Decision.** **Decree 13/2023 was repealed on 2026-01-01** and replaced by **Law
No. 91/2025/QH15** (Vietnam's first statute-level data protection law, effective
2026-01-01) plus **Decree No. 356/2025/NĐ-CP**. Design as though **no
purely-personal/household exemption applies**.

**Rejected.** Relying on a GDPR-style Art. 2(2)(c) household carve-out — it is
**unverified at article level**: one compliance-vendor summary suggests private or
household processing may be exempt "from some or all" requirements; DLA Piper's guide
does not list one; the primary text could not be extracted.

**Consequences.** **Any document in this set citing Decree 13 is citing a repealed
rule.** The design does not depend on the exemption anyway — the class model
(ADR-0022), forward-only enrolment (ADR-0032), the purge path (ADR-0028) and export
gating (ADR-0033) are the technical expression of the obligations either way, and the
tool stores third parties' messages regardless.

One further constraint travels with the data rather than sitting in a footnote:
**Telegram's API terms prohibit using or aggregating Telegram data to train or
fine-tune ML models** (*likely* — from search extracts, not a direct fetch). If any
downstream use involves an LLM — summarization, embeddings, semantic search over chat
history — that is a hard architectural boundary. It is surfaced in the export manifest
so the restriction cannot be lost when the data moves.

---

## F. v2 — local web viewer

*Added 2026-09-05 for the v2 server and UI (`plans/v2/`). The v1 rulings above stay
frozen; these follow the same format.*

<a id="adr-0064"></a>
### ADR-0064 — The web viewer is read-only and loopback-bound by construction

**Context.** v2 adds a local web face over `data/social.db`: an HTTP server, a UI, and a
data viewer for posts, snapshots, and runs. The same SQLite file is written by the
crawler, and `snapshots.html` is unmodified third-party markup captured from Facebook.

**Decision.** The server opens SQLite **read-only through a URI**
(`file:...?mode=ro`) via its own `queries.py` module — `db.connect()`'s
`executescript(SCHEMA_SQL)` is never on the read path, so the server cannot create or
migrate a table even by accident. Any crawl trigger spawns the existing CLI as a
subprocess, so the **crawler stays the single writer**. The server binds to
`127.0.0.1` by default; a non-loopback bind requires an explicit `--allow-remote` flag
plus a token. Snapshot HTML is never rendered into the app's own pages — it is served
sandboxed with a `default-src 'none'` CSP.

**Rejected.** Sharing `db.connect()` with the viewer ("we'll just not write") — a
capability you hold by discipline is a capability you eventually use. Binding
`0.0.0.0` for convenience — the database contains third-party personal data and the
pages carry no auth by default.

**Consequences.** Concurrent reads ride WAL while a crawl writes, with no locking
coordination in the server at all. The one writer rule (v1's `FileLock`) also covers
UI-triggered crawls, because they are ordinary `crawler crawl` processes competing for
the same lock. The untrusted-HTML rule costs a sandboxed iframe and a strict CSP on the
raw route, and pays for itself the first time a captured page tries to phone home.

---

<a id="adr-0065"></a>
### ADR-0065 — FastAPI over a bare stdlib server

**Context.** v2 needs an HTTP server over SQLite with a JSON API, server-rendered
pages, query validation, and a test story — on both macOS and Linux, with nothing
added to the toolchain beyond Python packages.

**Decision.** Build on FastAPI, uvicorn, Jinja2, and python-multipart. Routes declare
query parameters once; the per-request read-only connection arrives as a dependency
(`get_conn`), so no route can forget to open or close it; `TestClient` exercises every
route without a socket. The JSON API validates strictly (422 on bad input) while HTML
routes deliberately fall back to defaults — two audiences, one parameter vocabulary.

**Rejected.** `http.server` plus hand-rolled dispatch — every parameter, content type,
and error shape becomes bespoke code with no test harness, and the JSON API alone
would reintroduce a mini-framework. Flask — equivalent for this job, but FastAPI's
dependency injection maps exactly onto the per-request read-only connection that the
read-only-by-construction rule needs.

**Consequences.** Three direct packages and their transitives enter the venv
(audited in `plans/v2/09`: only fastapi, uvicorn, jinja2, pydantic, python-multipart
and their requirements). Starlette version coupling is accepted; nothing in v2
imports Starlette directly except the hardening middlewares.

---

<a id="adr-0066"></a>
### ADR-0066 — Server-rendered HTML; no JavaScript framework, no build step

**Context.** The UI is one person browsing their own crawl data on localhost, and the
v1 ground rules already ban Node/npm/bundlers as an unjustified supply chain.

**Decision.** Jinja2 templates rendered on the server, with exactly two static files
(`app.css`, `app.js`) served as real files — which the Step 09 CSP requires anyway,
since it forbids inline script and style. Interactivity is deliberately modest:
form-driven filters with full page loads, a 500 ms debounced auto-submit on text
input, a two-second status poll on `/crawl`, and a clipboard copy on the post page.
The home-page chart is inline SVG generated from a `GROUP BY date(first_seen)` —
no chart library, no CDN.

**Rejected.** Any JS framework or bundler — a build step, a `node_modules` tree, and
a second dependency audit for a localhost tool. Fetch-and-patch rendering — two
rendering paths to keep in sync for no capability the tool actually needs.

**Consequences.** Every action costs a page load, and live crawl output is a poll,
not a stream. The win is that the rendered page is the whole contract: what the
template renders is what the browser runs, and the CSP test greps prove it.

---

<a id="adr-0067"></a>
### ADR-0067 — A UI crawl is a subprocess, not a thread or background task

**Context.** "Crawl now" must run `pipeline.run_crawl`, which installs
`SIGINT`/`SIGTERM` handlers with `signal.signal` — legal only in the main thread of
a process. Server handlers run in worker threads, and asyncio background tasks are
not the main thread either.

**Decision.** The server spawns
`[sys.executable, "-m", "crawler_social.cli", "crawl", page_url, "--limit", str(n)]`
— list argv, `shell=False` — and tails its output with a reader thread into a
500-line ring buffer. Stop sends `SIGTERM`, which the pipeline already treats as a
graceful "interrupted" stop, escalating to `SIGKILL` after a 10-second grace. The
in-process one-job flag handles the common double-click; the existing `FileLock`
profile lock remains the real mutual exclusion against a crawl started in a
terminal, and its "holds the lock" message is surfaced readably.

**Rejected.** Running the pipeline in a thread or FastAPI background task —
`signal.signal` raises `ValueError` outside the main thread, so the pipeline would
need a rewrite that weakens v1's graceful-stop guarantees. Import-and-call in the
server process — a crashed crawl would take the server down with it.

**Consequences.** The page URL becomes a process argument and a browser navigation,
so it is validated against an https + facebook.com allowlist before spawn
(`plans/v2/08`). Output reaches the UI by polling the captured lines, never shared
memory.

---

<a id="adr-0068"></a>
### ADR-0068 — The read path is read-only by construction, and a route sweep proves it

**Context.** ADR-0064 froze "the server opens SQLite read-only through a URI". The
remaining question is how that property is *enforced* as routes accumulate across
steps.

**Decision.** One dependency (`get_conn`) is the only way a route obtains a
connection: `file:...?mode=ro`, `PRAGMA query_only = ON`, a five-second busy
timeout, closed in a `finally`. Pages that must work without a database use a
separate opener that returns `None` and render an empty state. The property is then
tested, not promised: the full HTML and API route table is walked against a
database file with permissions `0o444`, and every response must be non-5xx
(`tests/test_server_security.py`). Any route that ever needs a write fails loudly
there.

**Rejected.** A per-route promise ("this handler only SELECTs") — code review is
not a memory. A read-only database user or ACL — the same file must stay writable
for the crawler, so the guarantee has to live in the connection itself.

**Consequences.** The reparse view can run the parser in memory with no write path
at all. Anything genuinely state-changing goes out through the subprocess of
ADR-0067 and the crawler's own writer, which also keeps `PRAGMA integrity_check`
meaningful for the UI-triggered flow.

---

*Frozen 2026-09-01 for sections A–E. To reopen an entry, change the "Rejected" column —
that is, produce the fact or the trigger that was missing when it was closed. Do not
reopen one by re-arguing the same trade-off.*
