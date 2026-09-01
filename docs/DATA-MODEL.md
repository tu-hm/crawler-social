# Data model

The complete storage design for `crawler-social`: one schema, applied identically to two
database files, carrying five sources whose content comes in three shapes.

Everything in this document was executed against **SQLite 3.51.0** (the version both the
system `sqlite3` CLI and pyenv's Python 3.13 link on this machine) before it was written
down. Query plans are real `EXPLAIN QUERY PLAN` output, not predictions. Two defects in
the frozen DDL were found that way and are documented in
[§13 Defects found while validating the frozen DDL](#13-defects-found-while-validating-the-frozen-ddl).

Siblings: [../ARCHITECTURE.md](../ARCHITECTURE.md) — the connector contract and core
seam · [./GOVERNANCE.md](./GOVERNANCE.md) — privacy classes, retention, export policy ·
[./DECISIONS.md](./DECISIONS.md) — why each of these was chosen, and the delivery order ·
per-source detail in [./sources/facebook.md](./sources/facebook.md),
[./sources/telegram.md](./sources/telegram.md), [./sources/reddit.md](./sources/reddit.md),
[./sources/x.md](./sources/x.md), [./sources/zalo.md](./sources/zalo.md).

---

## 1. The modelling problem

Five sources. Three content shapes. One place to put them.

| Source | Container examples | Shape | Threading | Ordinal | Metrics | Privacy span |
|---|---|---|---|---|---|---|
| Facebook | page, closed group | feed | 2 levels, ids sometimes synthetic | none | rounded reaction counts | broadcast + joined |
| Reddit | subreddit | feed | arbitrary depth, `more` placeholders | none | vote-fuzzed score, mutable | broadcast |
| X | timeline, own archive | feed | reply chain + quote/repost edges | none (Snowflake id is time-ordered) | like/repost/quote/bookmark | broadcast + conversation (archive DMs) |
| Telegram | channel, supergroup, DM | **both** | reply-to, forum topics | `message_id`, per-peer monotonic | views, forwards, **per-emoji reactions** | broadcast + joined + conversation |
| Zalo | OA thread, group, DM | conversation | flat | client timestamp (deferred) | none reachable | conversation |

Four facts do the deciding.

**1. Telegram spans the privacy boundary from one credential.** One `.session` file emits
public channel posts and private DMs. Any design where privacy is a property of the
*connector* forces you to choose which half is wrong. Privacy is therefore a property of
the **container**.

**2. `bm25()` is per-index.** An FTS5 external-content index binds to exactly one content
table via `content_rowid`. Split items across five tables and you get five indexes whose
relevance scores are computed from *that index's* IDF and average document length. "Search
everything, best first" stops being expressible. This alone rules out per-source tables.

**3. Every one of these platforms delivers children before parents somewhere.** Reddit
`more` children, Telegram replies pointing outside the `offset_id` window, X replies to
protected posts. A row whose parent is not yet in the database must still be writable.

**4. Metrics do not fit columns.** Telegram reports reactions per emoji. There is no
column set that survives that, and there is no schema migration that should be required
when someone reacts with a new emoji.

### The chosen approach

**One `items` table**, hybrid leaning polymorphic:

- Universal facts are real, typed, indexed, `CHECK`-constrained columns.
- The source-specific long tail goes in a JSONB `extra` blob.
- Per-source detail tables are documented as an escape hatch and are **empty at v1**.
- `containers.shape IN ('feed','conversation')` carries the feed/chat distinction.
- `containers.privacy` carries the sensitivity class and decides **which file** a row lands in.
- Threading is adjacency-as-truth plus a derived materialized path.
- The same DDL is applied to `data/social.db` (plain) and `data/private.db` (SQLCipher).

`item_type` exists, but it is **provenance, not a discriminator**. Nothing in core branches
on it. That is the standing test at every review: if you find `if item_type == ...` in
core, the discriminator is being abused.

### Rejected: per-source tables plus a unifying VIEW

| Cost | Detail |
|---|---|
| Cross-source ranked search becomes inexpressible | Five FTS indexes, five `MATCH` queries `UNION`ed, and `bm25()` scores that are not on a comparable scale. There is no correct `ORDER BY` across them. |
| Adding a connector becomes a schema migration | The whole point of the capability contract is that a connector is a plugin. If shipping Zalo means `ALTER`ing a view that the Facebook queries read, the transport has leaked into storage. |
| Every satellite table needs a polymorphic FK | `metric_observations`, `media`, `item_envelopes`, `item_relations` all point at "an item". With five tables that becomes a `(source, id)` pair with no foreign key enforcement — you get the polymorphism anyway, unenforced, in five places instead of one. |
| The timeline query pays forever | A `UNION ALL` compound with `ORDER BY published_at DESC LIMIT 200` needs a per-branch index and a merge. Five branch opens on the single most common query, to avoid one integer column. |

### Rejected: one table with everything in JSON

You lose `NOT NULL` and `CHECK` on the columns that carry correctness — `published_at`,
`visibility`, `container_id`. You cannot build the partial and covering indexes the three
headline queries depend on. And `json_extract` in a `WHERE` clause is a full scan until you
promote it to a generated column, at which point you have reinvented real columns badly.

### Why per-source detail tables are not needed yet

Because promotion is one line, and it is verified:

```sql
UPDATE items SET extra = jsonb('{"link_flair_text":"Food"}') WHERE id = 4;

ALTER TABLE items ADD COLUMN flair TEXT
  GENERATED ALWAYS AS (extra ->> '$.link_flair_text') VIRTUAL;
CREATE INDEX idx_items_flair ON items(flair) WHERE flair IS NOT NULL;

EXPLAIN QUERY PLAN SELECT id FROM items WHERE flair = 'Food';
-- SEARCH items USING INDEX idx_items_flair (flair=?)          [executed, 3.51.0]
```

A hot source-specific field becomes an indexed column with no new table, no join and no
connector change.

**One-shot caveat, verified by failure:**

```
sqlite> ALTER TABLE items ADD COLUMN lday2 TEXT
   ...>   GENERATED ALWAYS AS (date(published_at,'unixepoch','+7 hours')) STORED;
Error: stepping, cannot add a STORED column
```

Read that error carefully, because the usual one-line summary of it is wrong. On 3.51.0
`ALTER TABLE … ADD COLUMN … STORED` **succeeds on an empty table** and fails only once the
table has rows — the message above came from a *populated* `items`. Both were re-executed
for this document. So the restriction is real but **the engine only enforces it after the
fact**: a migration tested against an empty dev database passes and then fails on your real
one. That is why `local_day` must be in the initial DDL, why the DDL is frozen now rather
than grown, and why "only `VIRTUAL` can be added later" is a rule you follow rather than a
guard you lean on.

A detail table would earn its place when one source adds **more than eight fields that are
queried together on a hot path**. None of the five currently does.

---

## 2. Two files, one schema

```
data/
  social.db     plain sqlite3     containers with privacy IN ('broadcast','joined')
  private.db    sqlcipher3        containers with privacy = 'conversation'
  social.db-wal  private.db-wal   ...
media/ab/cd/<sha256>              content-addressed bytes, never SQLite BLOBs
```

Identical DDL in both. The `GovernanceProfile` resolved by core routes each envelope and
each item to a file **before any write**. One Telegram run writes a channel post to
`social.db` and a DM to `private.db` in the same tick, through one persistence path, with
zero branching in the writer.

Three properties fall out, and all three were verified by building both files:

**a. `rm data/private.db` is complete and consistent.**

```
$ sqlite3 social.db < full.sql && sqlite3 private.db < full.sql
$ sqlite3 social.db  "SELECT count(*) FROM sqlite_master;"   ->  77
$ sqlite3 private.db "SELECT count(*) FROM sqlite_master;"   ->  77
$ rm private.db private.db-wal private.db-shm
$ sqlite3 social.db "PRAGMA integrity_check; SELECT count(*) FROM items;"
ok
1
```

**Caveat on that verification, stated because it is easy to misread.** Both files above
were built with plain `sqlite3`. It therefore proves the DDL is self-consistent; it does
**not** prove the DDL applies to a SQLCipher connection, because the `sqlcipher3` wheel
vendors its own SQLite build and **whether that build has FTS5 compiled in is unverified**
(§14 item 1). That is a gate with two written branches at M0, not a footnote — see §14.
Re-run this count with `sqlcipher3` for `private.db` at M11 so the 77/77 claim means what
it says.

No foreign key crosses the file boundary — SQLite cannot enforce one under `ATTACH`
anyway. A cross-boundary edge (a Telegram forward from another channel) degrades to an
unresolved `item_relations.to_ref` text value, never a write into the other file.

**b. The FTS triggers are unconditional in both files.** Each file holds exactly one
privacy half, so `items_ai` / `items_ad` / `items_au` fire on every row with no `WHERE`.
That structurally eliminates the corruption trap described in §9.

**c. Search cannot leak across the boundary by accident.**

```
$ sqlite3 social.db  "SELECT count(*) FROM items_fts WHERE items_fts MATCH '\"ha noi\"';"  -> 0
$ sqlite3 private.db "SELECT count(*) FROM items_fts WHERE items_fts MATCH '\"ha noi\"';"  -> 1
```

`crawler search` opens `social.db` only; `crawler search --private` opens `private.db`
only. There is no federated search, deliberately: a global search must be structurally
incapable of surfacing a DM, and `bm25` scores are not comparable across two indexes
anyway.

**Registry tables** (`sources`, `accounts`, `metrics`, `schema_versions`) are mirrored to
both files by the migration runner and written only by CLI commands, never by a connector.

**Never audit the split with `count(*)` on an FTS table.** External-content FTS5 reads the
*content table's* row count, so it reports the number of rows in `items`, not the number of
rows in the index. It will falsely pass. Audit with `MATCH` assertions, as above.

---

## 3. The full DDL

Applied identically to both files. Requires SQLite ≥ 3.45 (JSONB, `->>`); FTS5,
`STRICT` and generated columns have lower floors but 3.45 covers them all.

> **Read §13 before running this.** One `CHECK` constraint in the frozen text is rejected
> by SQLite and has a verified trigger replacement.

### Pragmas

```sql
PRAGMA journal_mode = WAL;        -- persistent; written once at creation
PRAGMA secure_delete = ON;        -- PERSISTENT HEADER FLAG. Must be set at creation:
                                  -- setting it later does not retroactively zero pages
                                  -- that are already on the freelist.
-- applied per connection:
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

Assert the version floor in `db.connect()` and in `doctor`. A uv-managed interpreter
bundles its **own** SQLite, which is not necessarily the one the system CLI links:

```python
import sqlite3
if sqlite3.sqlite_version_info < (3, 45, 0):
    raise SystemExit(f"need SQLite >= 3.45 (JSONB, ->>); got {sqlite3.sqlite_version}")
```

`sqlite3.version` is deprecated and removed in Python 3.14 — use `sqlite_version_info`
only. (Verified: on this machine `sqlite3.version` still exists but emits
`DeprecationWarning: version is deprecated and will be removed in Python 3.14`.)

### Migrations

```sql
-- Per-source counters, not one global train: Facebook's inevitable DOM-rotation
-- migrations must not drag Telegram through a version bump.
CREATE TABLE schema_versions (
  source     TEXT PRIMARY KEY,          -- '_core' | 'facebook' | 'telegram' | ...
  version    INTEGER NOT NULL,
  applied_at INTEGER NOT NULL
) STRICT, WITHOUT ROWID;
```

### Registry

```sql
CREATE TABLE sources (
  id           INTEGER PRIMARY KEY,
  key          TEXT NOT NULL UNIQUE,    -- facebook|reddit|x|telegram|zalo
  display_name TEXT NOT NULL,
  transport    TEXT NOT NULL CHECK (transport IN
                 ('browser','rest','mtproto','webhook','archive','feeds')),
  tos_class    TEXT NOT NULL CHECK (tos_class IN
                 ('official_api','tolerated','prohibited','unknown')),
  state        TEXT NOT NULL DEFAULT 'ok' CHECK (state IN ('ok','stopped','backoff')),
  state_reason TEXT,
  state_until  INTEGER,                 -- backoff not-before
  notes        TEXT                     -- rendered by `crawler capabilities`
) STRICT;

CREATE TABLE accounts (                 -- MY credentials. NEVER the secret itself.
  id             INTEGER PRIMARY KEY,
  source_id      INTEGER NOT NULL REFERENCES sources(id),
  label          TEXT NOT NULL,
  auth_kind      TEXT NOT NULL CHECK (auth_kind IN
                   ('browser_profile','oauth2','api_key','mtproto_session',
                    'cookie_jar','archive_file','none')),
  credential_ref TEXT,                  -- Keychain item name or 0600 path, NOT the token
  platform_uid   TEXT,
  status         TEXT NOT NULL DEFAULT 'unknown'
                   CHECK (status IN ('ok','expired','blocked','revoked','unknown')),
  last_ok_at     INTEGER,
  added_at       INTEGER NOT NULL,
  UNIQUE (source_id, label)
) STRICT;
```

`sources.transport` is data, not a class hierarchy — it is how `crawler status` can say
"reddit is running on `feeds`, not `oauth`" without core knowing what either means.
`tos_class` makes "this connector is contractually prohibited" a queryable fact rather
than a comment in a README.

### Identity

```sql
-- The join key is the HMAC, ALWAYS present. The clear columns are nullable so
-- `purge --person` is one UPDATE that breaks no foreign key and needs no cascade
-- rewrite. Pepper (32 random bytes) lives in the macOS Keychain, never in the DB.
--
-- This is PSEUDONYMIZATION, not anonymization: platform ids are a low-entropy
-- enumerable space, and anyone holding both the DB and the pepper can rebuild the
-- mapping by brute force. Say so in the docs; do not let a reader think it is anonymity.
CREATE TABLE authors (
  id            INTEGER PRIMARY KEY,
  source        TEXT NOT NULL,
  actor_hmac    TEXT NOT NULL,          -- HMAC-SHA256(source||platform_uid, pepper)
  platform_uid  TEXT,                   -- clear id; NULLed by redaction
  handle        TEXT,
  display_name  TEXT,
  profile_url   TEXT,
  label         TEXT NOT NULL,          -- 'P-'||substr(actor_hmac,1,4); always safe to print
  is_self       INTEGER NOT NULL DEFAULT 0 CHECK (is_self IN (0,1)),
  is_bot        INTEGER NOT NULL DEFAULT 0 CHECK (is_bot IN (0,1)),
  redacted_at   INTEGER,
  first_seen    INTEGER NOT NULL,
  last_seen     INTEGER NOT NULL,
  extra         BLOB,                   -- jsonb. NO phone column exists anywhere, and
                                        -- scrub() drops phone/email/access_hash/
                                        -- file_reference/location/presence before write.
  UNIQUE (source, actor_hmac)
) STRICT;
CREATE INDEX idx_authors_handle ON authors(source, handle);
  -- serves: "everything by @handle on this source" without a scan
CREATE INDEX idx_authors_uid    ON authors(source, platform_uid)
  WHERE platform_uid IS NOT NULL;
  -- serves: ingest-time lookup by clear id. Partial, so redacted rows drop out of the
  -- index entirely rather than sitting in it as NULLs.

```

**There are no `persons` / `author_person_links` tables.** Cross-platform identity linking
is out of the schema at v1. An earlier draft carried both, empty, with
`CHECK (linked_by IN ('manual','self'))` as a tripwire: adding automated matching would
have required a migration, and a migration is a review checkpoint. The tripwire is worth
keeping; two tables, a composite foreign key and a `CHECK` with **zero declared writers**
are not the cheapest way to buy it. The rule now lives in
[./DECISIONS.md](./DECISIONS.md) ADR-0023 as a written standing decision, and the deferred
row in [../PLAN.md](../PLAN.md) §11 carries the trigger. The underlying reason is unchanged
and is a correctness reason rather than a cautious one: Vietnamese given-name distributions
are concentrated enough that name-based cross-platform matching is near a coin flip, and a
wrong link silently poisons every query that reads it with no way to tell which rows are
affected.

### Containers and targets

```sql
-- The feed/conversation distinction lives HERE, not on items. That is what lets one
-- Telegram connector write channel posts and DMs through one persistence path with zero
-- branching, and it is the decision that made this architecture win the bake-off.
CREATE TABLE containers (
  id                    INTEGER PRIMARY KEY,
  source                TEXT NOT NULL,
  platform_container_id TEXT NOT NULL,
  kind    TEXT NOT NULL CHECK (kind IN
            ('page','group','subreddit','channel','chat','dm','timeline',
             'query','profile','forum_topic','oa')),
  shape   TEXT NOT NULL CHECK (shape IN ('feed','conversation')),
  privacy TEXT NOT NULL CHECK (privacy IN ('broadcast','joined','conversation')),
  privacy_source TEXT NOT NULL CHECK (privacy_source IN ('connector','config','forced')),
  viewer_account_id INTEGER REFERENCES accounts(id),
  parent_id         INTEGER REFERENCES containers(id),  -- TG channel -> discussion group
  title TEXT, handle TEXT, url TEXT,
  member_count  INTEGER,
  access_state  TEXT NOT NULL DEFAULT 'ok'
                  CHECK (access_state IN ('ok','no_access','left','banned','gone','unknown')),
  access_checked_at INTEGER,
  content_unavailable INTEGER NOT NULL DEFAULT 0 CHECK (content_unavailable IN (0,1)),
                  -- e.g. a Zalo thread the user upgraded to E2EE: it exists and its
                  -- content is not retrievable. The archive must be able to SAY that
                  -- rather than silently looking complete.
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL,
  extra      BLOB,
  UNIQUE (source, platform_container_id),
  -- "by what right do I hold this?" as a NOT NULL constraint
  CHECK (privacy <> 'conversation' OR viewer_account_id IS NOT NULL)
) STRICT;
CREATE INDEX idx_containers_privacy ON containers(privacy, source);
  -- serves: the governance audit "what am I holding about other people, by source"
CREATE INDEX idx_containers_parent  ON containers(parent_id) WHERE parent_id IS NOT NULL;
  -- serves: a TG channel post joined to its linked discussion group's comments

-- config: what to sync. Distinct from containers: what exists.
-- One target -> exactly one container -> exactly one file. Frozen invariant; it makes
-- file routing total.
CREATE TABLE targets (
  id           INTEGER PRIMARY KEY,
  container_id INTEGER NOT NULL REFERENCES containers(id),
  source       TEXT NOT NULL,
  account_id   INTEGER REFERENCES accounts(id),
  enabled      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  priority     INTEGER NOT NULL DEFAULT 100,
  -- governance, resolved into a GovernanceProfile at tick time
  enrolled_at    INTEGER NOT NULL,      -- conversation ingest floor. FORWARD-ONLY.
  backfill_from  INTEGER,               -- NULL = no backfill (the conversation default)
  ack_third_party INTEGER NOT NULL DEFAULT 0 CHECK (ack_third_party IN (0,1)),
  retention_days INTEGER,               -- NULL = inherit the privacy class default
  raw_mode       TEXT NOT NULL DEFAULT 'inherit',
  media_mode     TEXT NOT NULL DEFAULT 'inherit',
  media_max_bytes INTEGER,
  -- NOTE: there is deliberately no `identity_mode` column. ADR-0026 stores identity
  -- `clear` in both files and pseudonymises at EXPORT; a per-target override would have
  -- had no reader anywhere in the design.
  on_upstream_delete TEXT NOT NULL DEFAULT 'auto',
  include_replies INTEGER NOT NULL DEFAULT 1 CHECK (include_replies IN (0,1)),
  max_depth      INTEGER,
  item_limit     INTEGER,
  export_ok      INTEGER NOT NULL DEFAULT 0 CHECK (export_ok IN (0,1)),
  added_at       INTEGER NOT NULL,
  UNIQUE (container_id)
) STRICT;
CREATE INDEX idx_targets_due ON targets(enabled, priority, id);
  -- serves: "which targets does this tick run, in what order"

-- A conversation target may not be enrolled without an explicit human ack.
-- This is a TRIGGER, not a CHECK: see §13. Verified to fire correctly.
CREATE TRIGGER targets_ack_ins BEFORE INSERT ON targets
WHEN NEW.ack_third_party = 0
 AND (SELECT privacy FROM containers WHERE id = NEW.container_id) = 'conversation'
BEGIN
  SELECT RAISE(ABORT, 'conversation target requires ack_third_party=1');
END;

CREATE TRIGGER targets_ack_upd BEFORE UPDATE ON targets
WHEN NEW.ack_third_party = 0
 AND (SELECT privacy FROM containers WHERE id = NEW.container_id) = 'conversation'
BEGIN
  SELECT RAISE(ABORT, 'conversation target requires ack_third_party=1');
END;
```

### Run bookkeeping

```sql
-- Run granularity is (target, tick). Since a target lives in exactly one file, a run row
-- lands in exactly one file; `crawler status` reads both.
CREATE TABLE runs (
  id         INTEGER PRIMARY KEY,
  source     TEXT NOT NULL,
  target_id  INTEGER REFERENCES targets(id),
  account_id INTEGER REFERENCES accounts(id),
  mode       TEXT NOT NULL CHECK (mode IN
               ('once','limit','daily','backfill','reparse','repair','import')),
  params     BLOB,                      -- jsonb: --limit/--since AND ephemeral page tokens
                                        -- (a page token NEVER goes in `cursors`)
  started_at  INTEGER NOT NULL,
  finished_at INTEGER,
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN
    ('running','ok','empty','suspect','partial','rate_limited',
     'needs_human','blocked','error','aborted')),
  verdict     TEXT,                     -- ok|retry|wait|human|stop|drop|suspect
  exit_code   INTEGER,
  envelopes   INTEGER NOT NULL DEFAULT 0,
  items_new   INTEGER NOT NULL DEFAULT 0,
  items_updated INTEGER NOT NULL DEFAULT 0,
  requests    INTEGER NOT NULL DEFAULT 0,
  cost_micros INTEGER NOT NULL DEFAULT 0,   -- X spends money; everyone else writes 0
  error_kind TEXT, note TEXT
) STRICT;
CREATE INDEX idx_runs_started ON runs(started_at DESC);
  -- serves: `crawler status` — "did the 08:05 job actually run?"
CREATE INDEX idx_runs_target  ON runs(target_id, started_at DESC);
  -- serves: SUSPECT rule (b) — "did this target produce items in each of its last 3 runs?"

-- DURABLE watermarks only. A page token with no TTL is the bug that silently breaks
-- daily runs months later.
CREATE TABLE cursors (
  target_id  INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  name       TEXT NOT NULL DEFAULT 'main',
  kind       TEXT NOT NULL CHECK (kind IN ('watermark','page')),
  axis       TEXT NOT NULL DEFAULT 'published_at',
  value      TEXT NOT NULL,             -- OPAQUE JSON. Core NEVER looks inside.
  axis_value INTEGER,                   -- the ordinal core compares for gap detection
  expires_at INTEGER,
  run_id     INTEGER REFERENCES runs(id),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (target_id, name),
  CHECK (kind <> 'page' OR expires_at IS NOT NULL)
) STRICT, WITHOUT ROWID;

-- Honesty, maintained by CORE from the coverage claims on envelopes. Reddit's 1000-item
-- listing wall, Telegram's UpdatesTooLong, X's ~3,200-post ceiling and Zalo's enrolment
-- floor are one table, not four connector-local bugs.
CREATE TABLE gaps (
  target_id  INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  axis       TEXT NOT NULL,
  lo         INTEGER NOT NULL,
  hi         INTEGER NOT NULL,
  reason     TEXT NOT NULL,             -- listing_cap|outage|blocked|enrolled_floor|
                                        -- timeline_ceiling|withheld
  withheld   INTEGER NOT NULL DEFAULT 0,   -- sum(Reddit more.count), TG unexpanded
  opened_at  INTEGER NOT NULL,
  opened_by_envelope INTEGER,           -- provenance: which claim revealed it
  filled_by  TEXT,                      -- 'arctic_shift' | 'backfill' | NULL
  filled_at  INTEGER,
  PRIMARY KEY (target_id, axis, lo)
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_gaps_open ON gaps(target_id) WHERE filled_at IS NULL;
  -- serves: the gap-drain job, and `crawler status` — "what do I know I am missing?"

-- The M10 canary, generalised across connectors. A collapse in published_at fill rate is
-- the early warning that Facebook rotated the DOM — and it works identically for a Reddit
-- JSON field disappearing. Core writes these rows from `ParseResult.field_stats`
-- (dict[str, tuple[int,int]] -> field -> (seen, filled)), NOT from `diagnostics`, which is
-- free text and cannot carry a triple. See ARCHITECTURE.md §5.
--
-- `filled` is only well defined if a parser can distinguish "absent" from "false". That is
-- why is_pinned / is_sponsored / more_remaining are TRI-STATE in ItemDraft: a `bool = False`
-- default has a structurally 100% fill rate and the alarm can never sound on it.
CREATE TABLE field_stats (
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  field  TEXT NOT NULL,
  seen   INTEGER NOT NULL,
  filled INTEGER NOT NULL,
  PRIMARY KEY (run_id, field)
) STRICT, WITHOUT ROWID;

-- X's monthly allowance is MONEY, not rate. A bucket smooths a rate; a counter enforces a
-- budget. Checked BEFORE the run starts.
CREATE TABLE usage_counters (
  source TEXT NOT NULL,
  period TEXT NOT NULL,                 -- '2026-09' (UTC billing month)
  -- SINGLE-VALUED ON PURPOSE. An earlier draft allowed 'reads' as well, and the frozen
  -- config in two documents then disagreed about which one the cap used. A governor
  -- reading a metric the ingest path does not write sees a month-to-date of ZERO and
  -- never fires. `cost_micros` is the survivor because it survives a repricing.
  metric TEXT NOT NULL CHECK (metric = 'cost_micros'),
  value  INTEGER NOT NULL DEFAULT 0,    -- micro-dollars spent this period
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (source, period, metric)
) STRICT, WITHOUT ROWID;
```

### Raw-first storage

```sql
-- Trained zstd dictionaries, per (source, kind) generation. Old dictionaries are kept
-- FOREVER and never deleted, because dict_id is on the blob.
CREATE TABLE zdict (
  dict_id    INTEGER PRIMARY KEY,
  source     TEXT NOT NULL,
  kind       TEXT NOT NULL,
  trained_at INTEGER NOT NULL,
  n_samples  INTEGER NOT NULL,
  bytes      BLOB NOT NULL
) STRICT;

-- ONE ROW PER DISTINCT BYTE SEQUENCE.
-- AUTOINCREMENT is load-bearing, not stylistic: retention pruning, purge_after and
-- per-subject redaction all DELETE rows here, and plain rowid reuse would silently break
-- every `WHERE seq > :last_processed` reparse/backfill scan.
CREATE TABLE envelopes (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  source         TEXT NOT NULL,
  target_id      INTEGER REFERENCES targets(id),
  kind           TEXT NOT NULL,         -- fb.feed_html|fb.graphql.feed|reddit.listing|
                                        -- reddit.comments|x.timeline|x.archive.dm|
                                        -- tg.slice|zalo.event
  content_type   TEXT NOT NULL,         -- text/html|application/json|application/x-msgpack
  sha256         TEXT NOT NULL UNIQUE,  -- over UNCOMPRESSED bytes
  body           BLOB NOT NULL,
  codec          TEXT NOT NULL DEFAULT 'zlib' CHECK (codec IN ('raw','zlib','zstd')),
  dict_id        INTEGER REFERENCES zdict(dict_id),
  raw_bytes      INTEGER NOT NULL,
  stored_bytes   INTEGER NOT NULL,
  -- the coverage claim, persisted so gap analysis is a query and the claimed-vs-found
  -- SUSPECT check is auditable after the fact
  cover_kind     TEXT NOT NULL CHECK (cover_kind IN ('exact','partial','opaque')),
  cover_axis     TEXT,
  cover_lo       INTEGER,
  cover_hi       INTEGER,
  cover_withheld INTEGER NOT NULL DEFAULT 0,
  cover_note     TEXT,
  cursor_json    TEXT,                  -- the durable proposal, for `cursors --rebuild`
  privacy        TEXT NOT NULL DEFAULT 'broadcast'
                   CHECK (privacy IN ('broadcast','joined','conversation')),
  parser_version INTEGER NOT NULL DEFAULT 0,
  parse_status   TEXT NOT NULL DEFAULT 'pending'
                   CHECK (parse_status IN ('pending','ok','partial','failed','skipped')),
  parse_error    TEXT,
  first_captured_at INTEGER NOT NULL,
  purge_after    INTEGER,               -- retention TTL; conversation payloads get a short one
  meta           BLOB                   -- jsonb: url, http status, dc_id, tl_layer, lib, sig
) STRICT;
CREATE INDEX idx_env_reparse ON envelopes(source, kind, parser_version);
  -- serves: the reparse queue — "which stored bytes are behind the current parser?"
CREATE INDEX idx_env_target  ON envelopes(target_id, first_captured_at);
  -- serves: `crawler explain <item>` and per-target raw-storage accounting
CREATE INDEX idx_env_purge   ON envelopes(purge_after) WHERE purge_after IS NOT NULL;
  -- serves: the retention sweep. Partial, so it only touches rows that HAVE a TTL.

-- ONE ROW PER FETCH EVENT.
-- This split is the single most important correction to the original plan's `raw_payloads`
-- (see PLAN.md §5.5), whose `sha256 UNIQUE`
-- on a table also carrying captured_at collapses Monday, Tuesday and Wednesday fetches of
-- an unchanged page into ONE row with Monday's timestamp — destroying the exact input to
-- soft-delete detection: "when did I last confirm this still existed".
CREATE TABLE envelope_fetches (
  id          INTEGER PRIMARY KEY,
  envelope_seq INTEGER NOT NULL REFERENCES envelopes(seq) ON DELETE CASCADE,
  run_id      INTEGER REFERENCES runs(id),
  fetched_at  INTEGER NOT NULL,
  request_ref TEXT,
  latency_ms  INTEGER
) STRICT;
CREATE INDEX idx_fetches_env ON envelope_fetches(envelope_seq, fetched_at);
  -- serves: "when did I last confirm this page still existed" (query A1 in §11)
CREATE INDEX idx_fetches_run ON envelope_fetches(run_id);
  -- serves: the per-run report

CREATE TABLE parsers (                  -- current parser version per (source, kind)
  source TEXT NOT NULL, kind TEXT NOT NULL,
  parser_version INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  PRIMARY KEY (source, kind)
) STRICT, WITHOUT ROWID;
```

### The core table

```sql
CREATE TABLE items (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,
  container_id   INTEGER NOT NULL REFERENCES containers(id),
  platform_item_id TEXT NOT NULL,
  item_type      TEXT NOT NULL,         -- provenance ONLY; nothing branches on it
                                        -- fb.post|fb.comment|reddit.submission|
                                        -- reddit.comment|x.post|tg.channel_post|
                                        -- tg.message|zalo.message
  author_id      INTEGER REFERENCES authors(id),

  -- threading: adjacency is TRUTH, path is a derived index that may be NULL
  parent_id      INTEGER REFERENCES items(id),
  parent_ref     TEXT,                  -- ALWAYS written; raw platform parent id
                                        -- (Reddit 't1_jn24wv6', TG '73', X '1900')
  root_id        INTEGER REFERENCES items(id),
  root_ref       TEXT,
  depth          INTEGER NOT NULL DEFAULT 0,
  thread_path    TEXT,                  -- '/'-joined 10-char zero-padded hex of ancestor
                                        -- ids. NULL is LEGAL: correctness falls back to
                                        -- the recursive CTE, never to this column.
  more_remaining INTEGER NOT NULL DEFAULT 0,  -- unexpanded Reddit `more` count. A partial
                                              -- tree that SAYS it is partial is a correct
                                              -- result; a silently truncated one is a bug.
  source_seq     INTEGER,               -- TG message id (per-peer monotonic); NULL elsewhere

  published_at   INTEGER,
  published_prec TEXT NOT NULL DEFAULT 'unknown' CHECK (published_prec IN
                   ('exact','minute','hour','day','relative','unknown')),
  edited_at      INTEGER,

  title TEXT, text TEXT, lang TEXT, url TEXT, permalink TEXT,
  is_pinned    INTEGER NOT NULL DEFAULT 0 CHECK (is_pinned IN (0,1)),
  is_sponsored INTEGER NOT NULL DEFAULT 0 CHECK (is_sponsored IN (0,1)),
  is_from_self INTEGER NOT NULL DEFAULT 0 CHECK (is_from_self IN (0,1)),

  visibility TEXT NOT NULL DEFAULT 'visible' CHECK (visibility IN
    ('visible','deleted_upstream','removed_by_mod','hidden_from_us',
     'redacted_locally','unknown')),
  absence_streak      INTEGER NOT NULL DEFAULT 0,
  deleted_upstream_at INTEGER,

  metrics_current BLOB,                 -- jsonb, latest values, denormalised so the
                                        -- timeline query needs no correlated MAX
  extra           BLOB,                 -- jsonb, the source-specific long tail
  parser_version INTEGER NOT NULL DEFAULT 0,
  first_seq  INTEGER,                   -- provenance INTO the envelope store; powers
  last_seq   INTEGER,                   -- `crawler explain <item>` in two integers
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL,

  -- Vietnam is UTC+7 year-round with NO DST, so this constant is safe to freeze.
  -- MUST be in the initial DDL: ALTER TABLE cannot add a STORED column.
  local_day TEXT GENERATED ALWAYS AS (date(published_at,'unixepoch','+7 hours')) STORED,

  UNIQUE (container_id, platform_item_id)      -- the upsert conflict target
) STRICT;
```

One index per real query. Every one below was confirmed to be *chosen by the planner* for
the query named, on a populated database, after `ANALYZE`.

| Index | Serves | Verified plan |
|---|---|---|
| `idx_items_recent(published_at DESC) WHERE parent_id IS NULL AND visibility='visible'` | Q1 cross-source timeline | `SEARCH i USING INDEX idx_items_recent (published_at>?)` |
| `idx_items_published(published_at DESC)` | Q1 without the visibility filter; ad-hoc time slicing | `SEARCH i USING INDEX idx_items_published (published_at>?)` |
| `idx_items_tree(root_id, thread_path)` | Q2 full comment tree, one ordered range scan, no sort | `SEARCH items USING INDEX idx_items_tree (root_id=? AND thread_path>? AND thread_path<?)` — **covering** when the select list stays inside the index |
| `idx_items_stream(container_id, source_seq)` | Q3 a conversation in order | `SEARCH items USING INDEX idx_items_stream (container_id=?)` — **covering** for `SELECT source_seq` |
| `idx_items_container(container_id, published_at DESC)` | one container's feed, newest first | |
| `idx_items_author(author_id, published_at DESC)` | everything by one author | |
| `idx_items_parent(parent_id)` | direct replies to an item | |
| `idx_items_dangling(source, parent_ref) WHERE parent_id IS NULL AND parent_ref IS NOT NULL` | the parent-repair pass | `SEARCH items USING INDEX idx_items_dangling (source=? AND parent_ref>?)` |
| `idx_items_localday(local_day, source)` | "how much did I collect per day, in my timezone" | |
| `idx_items_reparse(source, parser_version)` | which items are behind the current parser | |
| `idx_items_sweep(container_id, absence_streak) WHERE visibility='visible'` | the absence/tombstone sweep | |

```sql
CREATE INDEX idx_items_recent    ON items(published_at DESC)
  WHERE parent_id IS NULL AND visibility = 'visible';
CREATE INDEX idx_items_published ON items(published_at DESC);
CREATE INDEX idx_items_tree      ON items(root_id, thread_path);
CREATE INDEX idx_items_stream    ON items(container_id, source_seq);
CREATE INDEX idx_items_container ON items(container_id, published_at DESC);
CREATE INDEX idx_items_author    ON items(author_id, published_at DESC);
CREATE INDEX idx_items_parent    ON items(parent_id);
CREATE INDEX idx_items_dangling  ON items(source, parent_ref)
  WHERE parent_id IS NULL AND parent_ref IS NOT NULL;
CREATE INDEX idx_items_localday  ON items(local_day, source);
CREATE INDEX idx_items_reparse   ON items(source, parser_version);
CREATE INDEX idx_items_sweep     ON items(container_id, absence_streak)
  WHERE visibility = 'visible';
```

**Two index facts that bite, both reproduced.**

*Partial indexes only apply when the query repeats their predicate verbatim.* Drop
`AND visibility='visible'` and the planner silently switches index:

```
-- with the predicate:     SEARCH i USING INDEX idx_items_recent   (published_at>?)
-- without the predicate:  SEARCH i USING INDEX idx_items_published (published_at>?)
```

Both are correct SQL and both are fast. The difference is that the second one **returns
tombstoned rows**. That is why the canonical timeline query text lives in a named module
constant and is never retyped.

*"Covering" is a property of the select list, not the index.* `idx_items_tree` reports
`COVERING INDEX` for `SELECT id` and plain `INDEX` for `SELECT depth, text`. The property
that actually matters — an ordered scan with **no `USE TEMP B-TREE FOR ORDER BY`** — holds
in both cases. Do not read `COVERING` disappearing as a regression.

Run `ANALYZE` after the first substantial crawl and in the daily job. At low row counts,
before statistics exist, SQLite will pick a different index plus a temp b-tree sort.

### Versions, relations, provenance

```sql
-- Edits get history. Without this a Telegram edit, an edited Facebook post and an X edit
-- all overwrite text in place, recoverable only by hand from raw envelopes. Written on the
-- same conditional rule as metrics: an edit is a new row, an unchanged re-fetch is a no-op.
CREATE TABLE item_versions (
  item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  content_hash TEXT NOT NULL,
  observed_at  INTEGER NOT NULL,
  text         TEXT,
  entities_json TEXT,                   -- TG MessageEntity list, RAW. Never flattened to
                                        -- markdown at ingest: offsets are UTF-16 and
                                        -- flattening is a lossy parse you cannot undo.
  PRIMARY KEY (item_id, content_hash)
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_item_versions_time ON item_versions(item_id, observed_at);
  -- serves: "show me this message as it read on date X"

-- Non-reply edges: quote, retweet, forward, crosspost, album. NOT parent edges.
CREATE TABLE item_relations (
  from_item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  -- No 'pinned_in': pinning is items.is_pinned, no source mapping ever emitted it, and the
  -- CHECK must match RelationDraft.rel in ARCHITECTURE.md §5 exactly.
  rel TEXT NOT NULL CHECK (rel IN
    ('quote_of','repost_of','forward_of','crosspost_of','album_member')),
  to_ref       TEXT NOT NULL,           -- ALWAYS written, even when unresolvable
  to_item_id   INTEGER REFERENCES items(id),
  to_source    TEXT,
  captured_at  INTEGER NOT NULL,
  PRIMARY KEY (from_item_id, rel, to_ref)
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_relations_to ON item_relations(to_item_id, rel);
  -- serves: "who quoted this post?" — the reverse edge

-- N:M provenance, bounded: role='primary' only (the envelopes that produced or updated
-- CONTENT). Metric-refresh batches do not write here, so this cannot grow one row per item
-- per daily re-observation.
--
-- The single-valued CHECK on `role` is the point, and it is why the column exists at all:
-- boundedness is a property of the SCHEMA rather than a convention in the writer, and
-- adding a second role becomes a migration — which is the review checkpoint ADR-0048
-- wants. Same pattern as the CHECK that used to guard author_person_links.linked_by.
CREATE TABLE item_envelopes (
  item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  envelope_seq INTEGER NOT NULL REFERENCES envelopes(seq) ON DELETE CASCADE,
  role         TEXT NOT NULL DEFAULT 'primary' CHECK (role IN ('primary')),
  PRIMARY KEY (item_id, envelope_seq, role)
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_item_env_rev ON item_envelopes(envelope_seq);
  -- serves: reparse — "which items did this envelope produce?"
```

### Media

```sql
-- SNAPSHOT-AT-INGEST SIDE-CAR with explicitly WEAKER guarantees than the rest of the
-- raw-first promise. media_mode defaults to 'link' for ALL privacy classes at v1, because
-- media is the only unbounded cost in the project (a 200k-message Telegram channel is tens
-- of MB of text and potentially hundreds of GB with media, and each download also spends
-- the flood budget).
--
-- Stated honestly: a stored URL is a DEAD POINTER for Telegram (file_reference expires),
-- Zalo (token-bearing *.zdn.vn CDN paths) and Facebook. The replay guarantee covers
-- STRUCTURED CONTENT ONLY.
--
-- Bytes go content-addressed to disk (media/ab/cd/<sha256>), never as SQLite BLOBs.
CREATE TABLE media (
  id         INTEGER PRIMARY KEY,
  item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  ord        INTEGER NOT NULL DEFAULT 0,
  -- No 'location' kind. scrub() drops latitude/longitude/geo/gps/venue/location before
  -- persistence and assert_clean() raises PIILeak if one survives, so reserving a media
  -- kind for exactly that data class would create somewhere to put what nothing may write.
  -- Same structural argument as "there is no phone column" (ADR-0027).
  kind       TEXT NOT NULL CHECK (kind IN
               ('image','video','audio','voice','file','sticker','link_card','poll')),
  url        TEXT,
  url_sha256 TEXT NOT NULL,             -- FB/TG media URLs are long and expire; key on the hash
  thumb_url  TEXT, mime TEXT, byte_len INTEGER,
  width INTEGER, height INTEGER, duration_s INTEGER,
  caption    TEXT,
  local_path TEXT, local_sha256 TEXT,
  fetched_at INTEGER,
  extra      BLOB,
  UNIQUE (item_id, url_sha256)
) STRICT;
CREATE INDEX idx_media_local ON media(local_sha256) WHERE local_sha256 IS NOT NULL;
  -- serves: dedupe on disk, and the purge path — "which blobs does this redaction remove?"
```

### Metric drift

```sql
CREATE TABLE metrics (id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE) STRICT;
-- seeded: score, upvote_ratio, comments, shares, reactions.total, reactions.like,
--         reactions.<emoji>, views, forwards, quotes, bookmarks, likes

-- CHANGE-LOG, not sample-log: a row is written ONLY when the value moved. Per-source
-- metric columns die on Telegram's per-emoji reactions alone; an unconditional daily
-- sample is ~180M rows/year at 100k items for zero information about year-old posts that
-- stopped moving.
CREATE TABLE metric_observations (
  item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  metric_id   INTEGER NOT NULL REFERENCES metrics(id),
  observed_at INTEGER NOT NULL,
  value       INTEGER NOT NULL,
  approximate INTEGER NOT NULL DEFAULT 0 CHECK (approximate IN (0,1)),
                                        -- Reddit vote-fuzzes; FB rounds above 1k
                                        -- ('1.2K' -> 1200). A +/-3 delta is noise and the
                                        -- schema must say so.
  source_kind TEXT NOT NULL DEFAULT 'live' CHECK (source_kind IN ('live','archive')),
                                        -- 'archive' = Arctic Shift, ~36h stale. Mixing the
                                        -- two without this column silently corrupts the
                                        -- time series.
  PRIMARY KEY (item_id, metric_id, observed_at)
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_metrics_time ON metric_observations(observed_at);
  -- serves: "what moved since yesterday, across everything"
  -- (the WITHOUT ROWID PK already serves per-item history — no second index needed)

-- Re-observation is scheduled by core for Cap.MUTABLE_METRICS sources on a decaying
-- schedule (+1h/+6h/+24h/+72h/+7d), batched.
CREATE TABLE metric_schedule (
  item_id  INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
  due_at   INTEGER NOT NULL,
  stage    INTEGER NOT NULL DEFAULT 0
) STRICT, WITHOUT ROWID;
CREATE INDEX idx_metric_due ON metric_schedule(due_at);
  -- serves: "which items are due for re-observation this tick"
```

### Redaction

```sql
-- block_reingest = 1 is NOT bookkeeping. Without it, tomorrow's 08:05 run re-fetches the
-- same conversation and silently re-creates every row you just deleted. A redaction that
-- undoes itself is WORSE than none, because you believe it worked. Every ingest path
-- consults this table before insert.
CREATE TABLE redactions (
  id             INTEGER PRIMARY KEY,
  scope          TEXT NOT NULL CHECK (scope IN ('person','container','item','range')),
  actor_hmac     TEXT,
  container_id   INTEGER,
  item_id        INTEGER,
  range_lo       INTEGER, range_hi INTEGER,
  block_reingest INTEGER NOT NULL DEFAULT 1 CHECK (block_reingest IN (0,1)),
  reason         TEXT,
  requested_at   INTEGER NOT NULL,
  applied_at     INTEGER,
  items_hit      INTEGER,
  envelopes_hit  INTEGER,
  media_hit      INTEGER
) STRICT;
CREATE INDEX idx_redactions_hmac ON redactions(actor_hmac) WHERE block_reingest = 1;
  -- serves: the pre-insert block check, per author. Partial: only live blocks are indexed.
CREATE INDEX idx_redactions_cont ON redactions(container_id) WHERE block_reingest = 1;
  -- serves: the same check, per container (a `crawler forget <target>`)
```

### FTS5 and the export view

See [§9](#9-full-text-search) for the tokenizer argument and the corruption trap this
shape eliminates.

```sql
CREATE VIRTUAL TABLE items_fts USING fts5(
  title, text,
  content = 'items',
  content_rowid = 'id',
  tokenize = "unicode61 remove_diacritics 2",
  detail = full
);

CREATE TRIGGER items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
END;
CREATE TRIGGER items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, text)
    VALUES ('delete', old.id, old.title, old.text);
END;
CREATE TRIGGER items_au AFTER UPDATE OF title, text ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, text)
    VALUES ('delete', old.id, old.title, old.text);
  INSERT INTO items_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
END;

CREATE VIEW v_items_masked AS
SELECT i.id, i.source, c.kind AS container_kind, c.privacy,
       datetime(i.published_at,'unixepoch','+7 hours') AS local_time,
       CASE WHEN c.privacy = 'conversation' AND i.is_from_self = 0
            THEN a.label
            ELSE COALESCE(a.display_name, a.handle, a.label) END AS author,
       i.title, i.text, i.permalink
  FROM items i
  JOIN containers c ON c.id = i.container_id
  LEFT JOIN authors a ON a.id = i.author_id;
-- `crawler export` reads THIS view by default; --include-names bypasses the masking, and
-- the user's own is_from_self rows are never masked.
```

**Object count, both files:** 30 tables, 41 indexes, 5 triggers, 1 view — **77 rows** in
`sqlite_master`, executed clean on 3.51.0 (`PRAGMA integrity_check` → `ok`). Re-executed
after the corrections in this section: both `targets_ack_*` triggers ABORT, `media.kind =
'location'` is rejected, `usage_counters.metric = 'reads'` is rejected, and
`ALTER TABLE items ADD COLUMN … VIRTUAL` still succeeds on a populated table.

> **Standing rule, added because it is how three documents drifted.** This section is the
> **only** copy of the DDL. Any DDL fragment quoted anywhere else in this document set must
> be a **copy of the executed text**, never a paraphrase, and must carry a link back here.
> If a fragment elsewhere disagrees with this section, this section is right and the other
> one is a bug.

---

## 4. How each source maps

Every row-set below was **loaded into the real schema and queried**. The `id` values are
the ones SQLite assigned; `thread_path` values are what the writer actually computed.

### Column mapping at a glance

| `items` column | Facebook | Reddit | X | Telegram | Zalo (deferred) |
|---|---|---|---|---|---|
| `platform_item_id` | `story_fbid`, else `sha256(author+text+day)` | **fullname** (`t3_…`/`t1_…`) | post id (Snowflake, as text) | `message_id` as text | `msgId` — UNVERIFIED field name |
| `item_type` | `fb.post` / `fb.comment` | `reddit.submission` / `reddit.comment` | `x.post` | `tg.channel_post` / `tg.message` | `zalo.message` |
| `parent_ref` | parent comment/post id | `parent_id` — already a fullname, no string surgery | `referenced_tweets[type=replied_to].id` | `reply_to.reply_to_msg_id` | none (flat) |
| `root_ref` | post id | link fullname `t3_…` | derive from `referenced_tweets`, **not** `conversation_id` | `reply_to.reply_to_top_id` | none |
| `source_seq` | NULL | NULL | NULL | `message.id` (per-peer monotonic) | NULL — no reliable ordinal |
| `published_prec` | `minute`/`hour`/`relative` — honestly downgraded | `exact` (`created_utc`) | `exact` | `exact` | `exact` (ms) |
| `title` | NULL | submission title | NULL | NULL | NULL |
| `url` | link attachment | `url` (NULL ⇒ self-post) | NULL | NULL | NULL |
| `more_remaining` | collapsed-comment count | `sum(more.count)` unexpanded | NULL | unexpanded replies | NULL |
| `extra` | reaction breakdown, `sponsored` metadata | `link_flair_text`, `removed_by_category`, `upvote_ratio` source | `edit_history_tweet_ids`, `lang` | `grouped_id`, `via_bot_id`, `post_author` | — |
| `metrics_current` | `{reactions.total, comments, shares}` | `{score, comments, upvote_ratio}` | `{likes, quotes, bookmarks}` | `{views, forwards, reactions.<emoji>}` | — |

Two mapping notes worth stating because they are easy to get wrong:

- **Reddit `parent_id` is already a fullname**, so storing `platform_item_id` as the
  fullname makes `parent_ref` match it directly with no prefix stripping. A self-post
  versus a link post is `url IS NULL` versus `url` set — no discriminator column needed.
- **X: do not derive `root_id` from `conversation_id`.** For a *retweet*,
  `conversation_id` equals the retweet's own id rather than the original's. Derive the
  root by walking `referenced_tweets` instead. (Verified as a behaviour; confirm against
  the current API response before the connector ships.)

---

### 4.1 Facebook group post with comments

`containers(id=3, kind='group', shape='feed', privacy='joined')` → **`social.db`**.

| id | item_type | platform_item_id | parent_ref | depth | thread_path |
|---|---|---|---|---|---|
| 1 | `fb.post` | `pfbid0AAA` | NULL | 0 | `0000000001` |
| 2 | `fb.comment` | `cAAA1` | `pfbid0AAA` | 1 | `0000000001/0000000002` |
| 3 | `fb.comment` | `cAAA2` | `cAAA1` | 2 | `0000000001/0000000002/0000000003` |

Facebook's 2-level comment model needs no special case: it is the same tree, two levels
deep. Two Facebook-specific things land elsewhere:

```sql
-- Reaction counts are rounded in the UI ('1.2K'), so approximate=1 is not decoration.
INSERT INTO metric_observations(item_id, metric_id, observed_at, value, approximate)
VALUES (1, 5 /* reactions.total */, 1787996400, 1200, 1);

-- Coverage: Facebook can never make an honest interval claim. A scroll unmounts
-- offscreen posts; inventing a position interval would be worse than none, because core
-- would then confidently record 'no gap' over a feed that dropped half its content.
INSERT INTO envelopes(source, target_id, kind, content_type, sha256, body,
                      raw_bytes, stored_bytes, cover_kind, cover_note, privacy,
                      first_captured_at)
VALUES ('facebook', 3, 'fb.feed_html', 'text/html', 'sha-f1', :zlib_bytes,
        420000, 52000, 'opaque', 'scroll_unmount', 'joined', 1787990300);
```

`cover_kind='opaque'` is a first-class honest value, not a failure state. Facebook will
claim it forever.

---

### 4.2 Reddit submission with a 5-deep comment tree

`containers(id=4, kind='subreddit', shape='feed', privacy='broadcast')` → **`social.db`**.

| id | item_type | platform_item_id | parent_ref | depth | thread_path | more_remaining |
|---|---|---|---|---|---|---|
| 4 | `reddit.submission` | `t3_1abcdef` | NULL | 0 | `0000000004` | **7** |
| 5 | `reddit.comment` | `t1_c1` | `t3_1abcdef` | 1 | `0000000004/0000000005` | 0 |
| 6 | `reddit.comment` | `t1_c2` | `t1_c1` | 2 | `…/0000000006` | 0 |
| 7 | `reddit.comment` | `t1_c3` | `t1_c2` | 3 | `…/0000000007` | 0 |
| 8 | `reddit.comment` | `t1_c4` | `t1_c3` | 4 | `…/0000000008` | 0 |
| 9 | `reddit.comment` | `t1_c5` | `t1_c4` | 5 | `…/0000000009` | 0 |
| 10 | `reddit.comment` | `t1_c99` | `t1_c3` | — | **NULL on arrival** | 0 |

Row 10 is the important one. It came back from a `morechildren` expansion whose parent
chain was not yet loaded, so it was written with `parent_id = NULL` and
`thread_path = NULL` — and it was **still writable**, which is the entire argument for
adjacency-as-truth. A path-only design cannot store this row at all.

`more_remaining = 7` on the submission records that the tree is knowingly partial. A
partially-expanded thread that *says* it is partial is a correct result; a silently
truncated one is a bug.

The 1000-item listing wall and unexpanded `more` counts become `gaps` rows written by
**core** from the connector's coverage claim — the connector contributes zero
gap-detection code:

```
$ sqlite3 t.db "SELECT target_id, reason, withheld,
      datetime(lo,'unixepoch','+7 hours')||' .. '||datetime(hi,'unixepoch','+7 hours')
    FROM gaps WHERE filled_at IS NULL;"
4|withheld|7|2026-08-29 13:30:00 .. 2026-08-29 15:40:00
```

Reddit metrics carry two flags that must never be dropped: `approximate=1` (Reddit
deliberately vote-fuzzes — *current behaviour UNVERIFIED, confirm on the first live
token*) and `source_kind='archive'` for anything sourced from Arctic Shift, whose
documented metric backfill lag is ~36 hours. Mixing archive and live observations in one
series without that column silently corrupts it.

---

### 4.3 X thread with quote-posts

`containers(id=5, kind='timeline', shape='feed', privacy='broadcast')` → **`social.db`**.

| id | platform_item_id | parent_ref | root_id | depth | thread_path |
|---|---|---|---|---|---|
| 11 | `1900000000000000001` | NULL | 11 | 0 | `000000000b` |
| 12 | `1900000000000000002` | `…001` | 11 | 1 | `000000000b/000000000c` |
| 13 | `1900000000000000003` | `…002` | 11 | 2 | `000000000b/000000000c/000000000d` |
| 14 | `1900000000000000010` | NULL | 14 | 0 | `000000000e` |

A reply **is** a post, so a self-reply thread needs no special handling — `root_id` groups
it and the path orders it. Quote and repost are *not* parent edges; they go to
`item_relations`:

```
$ sqlite3 t.db "SELECT from_item_id, rel, to_ref, ifnull(to_item_id,'NULL')
                FROM item_relations;"
14|quote_of|1899999999999999999|NULL      <- quoted post is protected: NEVER fetchable
14|quote_of|1900000000000000001|11        <- resolved to the local row
```

`to_ref` is always written; `to_item_id` resolves only if the target was ingested. That
is the same "always write the raw reference" discipline as `parent_ref`, applied to a
non-reply edge — and it is what lets the archive record "this quotes something I cannot
see" instead of dropping the edge.

X ingestion is `cover_kind='partial'` with `cover_note='timeline_ceiling'` once a backfill
reaches the ~3,200-post timeline limit. That limit is a platform fact, not a budget
choice: no amount of money lifts it on the timeline endpoint.

X DMs arrive only via the free data-archive ZIP as `kind='x.archive.dm'`, and they land in
a **`conversation`** container in `private.db` — the same `items` table, a different file.
This is the case where a source that reads as "broadcast" quietly acquires a private half,
and the container-level privacy is what catches it.

---

### 4.4 Telegram channel post

`containers(id=1, kind='channel', shape='feed', privacy='broadcast')` → **`social.db`**.

| id | item_type | source_seq | published_prec | text |
|---|---|---|---|---|
| 15 | `tg.channel_post` | **4821** | `exact` | `Giá vàng hôm nay tăng 300 nghìn đồng/lượng` |

```sql
-- metrics_current is denormalised so the timeline query needs no correlated MAX
UPDATE items SET metrics_current = jsonb('{"views":10432,"forwards":57}') WHERE id = 15;

-- Entities are stored RAW and never flattened to markdown at ingest: offsets are UTF-16
-- and flattening is a lossy parse you cannot undo — which is exactly what raw-first
-- exists to prevent.
INSERT INTO item_versions(item_id, content_hash, observed_at, text, entities_json)
VALUES (15, 'h1', 1787998200,
        'Giá vàng hôm nay tăng 300 nghìn đồng/lượng',
        '[{"_":"MessageEntityBold","offset":0,"length":8}]');
```

`source_seq` is the per-peer monotonic `message_id`. It is the only exact ordinal any of
the five sources provides, and it is what makes Telegram's cursor exact rather than
approximate.

A channel's **linked discussion group** is `containers.parent_id`, not a cross-table join:
the channel is one container, the discussion group is another with
`parent_id = <channel>`, and comments on a channel post are ordinary `items` in the
discussion group. Per-emoji reactions become `metrics` keys — `reactions.🔥` — which is
the single case that kills any per-source metric-column design.

---

### 4.5 Telegram private group, 200 messages

`containers(id=6, kind='chat', shape='conversation', privacy='conversation')` →
**`private.db`**. `viewer_account_id` is `NOT NULL`-enforced by the container CHECK, and
`targets.ack_third_party = 1` is enforced by the trigger.

| id | source_seq | is_from_self | author_id | parent_ref | text |
|---|---|---|---|---|---|
| 16 | 9000 | 1 | 7 (`is_self`) | – | `message 0 in the team chat` |
| 17 | 9001 | 0 | 8 | – | `message 1 in the team chat` |
| 18 | 9002 | 0 | 8 | – | `message 2 in the team chat` |
| … | … | … | … | … | … |
| 33 | 9017 | 0 | 8 | **`9016`** | `message 17 in the team chat` |
| 50 | 9034 | 0 | 8 | **`9033`** | `message 34 in the team chat` |
| … | … | … | … | … | … |
| 215 | 9199 | 0 | 8 | – | `message 199 in the team chat` |

200 rows, one container, `SELECT count(*) FROM items WHERE container_id=6` → `200`.

Four things this example demonstrates that nothing else does:

1. **`parent_id` is mostly NULL** in a conversation — replies are the exception, not the
   structure. Ordering comes from `source_seq`, not from a tree.
2. **`is_from_self` is a first-class column.** The user's own messages are never
   pseudonymized in `v_items_masked`, never expire under conversation retention, and are
   always exportable. The tiers exist to protect the *other* participant.
3. **The same writer handled 4.4 and 4.5.** One Telegram connector, one `items` upsert
   path, zero branching — one row went to `social.db` and 200 went to `private.db`, decided
   by `containers.privacy` before the write.
4. **The envelopes went to `private.db` too.** This is the half everyone forgets. Raw-first
   means the payload contains everything the parsed rows contain and more; parsed DMs in
   the encrypted file with raw TL slices in the plain one defeats the entire separation.

`enrolled_at` is the ingest floor: enrolling this chat sets it to `now` and nothing before
it is fetched. Backfill is explicit and per-conversation. Every other control in this
design is damage limitation for data already in the database; this one keeps it out.

---

### 4.6 Zalo (deferred — the slot, not the connector)

No transport ships at v1. What the schema carries so the slot is real:

- Three transports declared as separate `sources` rows (`zalo.oa` / `zalo.bot` /
  `zalo.user`) with honest `notes`, because they differ in identity namespace, auth
  lifecycle, whether backfill exists at all, and legal posture.
- `containers.content_unavailable = 1` for an E2EE-upgraded thread, so the archive can say
  *"thread exists, content not retrievable"* rather than silently looking complete.
- The `redactions` + `block_reingest` path is built generically for every source, shaped by
  Zalo's `user_withdraw` webhook — a machine-readable data-subject-rights signal that
  turns "never delete raw" into "raw is immutable *except* to the deletion sweep".
- Identity keying is `(source, actor_hmac)` rather than a global user id, because Zalo ids
  are **scope-local**: the same person carries a different `user_id` per OA and a different
  `user_id_by_app` per app. Designing the key around the strictest case costs nothing for
  the others.

Zalo field names (`msgId`, `uidFrom`, `ts`, `threadId`) are **UNVERIFIED** — the available
libraries document a listener clearly but not a complete message schema. `source_seq` may
have to stay NULL, in which case conversation order falls back to
`COALESCE(source_seq, published_at)`.

---

## 5. Threading

**Adjacency is truth. The materialized path is a derived index that is allowed to be NULL.**

| | stored | authoritative? | may be NULL? |
|---|---|---|---|
| `parent_ref` | raw platform parent id, verbatim | — always written | only when there is no parent |
| `parent_id` | resolved FK into `items` | **yes** | yes, until repaired |
| `root_id`, `depth`, `thread_path` | derived | no | **yes, legally** |

Correctness must never depend on `thread_path` being non-NULL — only speed. The recursive
CTE is the permanent fallback.

### Why not the alternatives

**Closure table.** A 20k-comment Reddit thread at depth 30 costs roughly 200k closure rows,
and closure only earns that when subtrees get *reparented*. None of these five platforms
ever reparents a comment. Paying O(n·depth) for a mutation that cannot occur is a straight
loss.

**Materialized path alone.** You cannot compute a path until the whole ancestor chain is
loaded, and it frequently is not — Reddit `more` children, Telegram replies outside the
`offset_id` window, X replies to protected posts. Those rows become unwritable. Row 10 in
§4.2 is that exact case.

**Adjacency alone.** A recursive CTE per tree read, cost scaling with depth × fan-out, and
no way to get depth-first order out of a single index scan.

### The `thread_path` encoding, and why it is frozen

`/`-joined, 10-character zero-padded hex of each ancestor's `items.id`.

```
$ sqlite3 t.db "SELECT printf('%010x',20), printf('%010x',200),
                       unicode('/'), unicode('0'),
                       printf('%010x',20)||'/x' < printf('%010x',21);"
0000000014|00000000c8|47|48|1
```

Two properties, both verified:

1. **Fixed width means no prefix collision.** Root 20 is `0000000014`, root 200 is
   `00000000c8`. A half-open range `[printf('%010x',20), printf('%010x',21))` does **not**
   capture root 200 — the check returned `0`.
2. **`/` is 0x2F, exactly one below `'0'` (0x30).** So a child path
   `0000000014/…` sorts *after* the bare root `0000000014` and *before* `0000000015`. One
   half-open range therefore captures the root and its entire subtree and nothing else.

### Reconstructing a comment tree

**Fast path** — one ordered range scan, no sort:

```sql
-- :root_path = printf('%010x', :root)   :root_succ = printf('%010x', :root + 1)
SELECT depth, thread_path, text
  FROM items
 WHERE root_id = :root
   AND thread_path >= :root_path
   AND thread_path <  :root_succ
 ORDER BY thread_path;
-- SEARCH items USING INDEX idx_items_tree (root_id=? AND thread_path>? AND thread_path<?)
```

Executed against §4.2's Reddit tree:

```
depth  thread_path                                              text
0      0000000004                                               Looking for recommendations.
1      0000000004/0000000005                                    reply at depth 1
2      0000000004/0000000005/0000000006                         reply at depth 2
3      0000000004/0000000005/0000000006/0000000007              reply at depth 3
4      0000000004/0000000005/0000000006/0000000007/0000000008   reply at depth 4
5      0000000004/…/0000000008/0000000009                       reply at depth 5
```

**Fallback that always works**, including for rows whose path is NULL:

```sql
WITH RECURSIVE t(id, d, ord) AS (
    SELECT id, 0, printf('%010d', id) FROM items WHERE id = :root
  UNION ALL
    SELECT i.id, t.d + 1, t.ord || '/' || printf('%010d', i.id)
      FROM items i JOIN t ON i.parent_id = t.id
)
SELECT t.d AS depth, i.text
  FROM t JOIN items i ON i.id = t.id
 ORDER BY t.ord;                       -- depth-first, same shape as thread_path
```

The two agree. And the CTE is what proves the repair pass matters:

```
-- row 10 arrived with parent_id NULL, thread_path NULL
CTE walk from the root:                 6 rows      <- orphan invisible, parent unresolved
thread_path range scan:                 6 rows

-- after the repair pass resolves parent_ref -> parent_id
CTE walk:                               7 rows      <- orphan found immediately
thread_path range scan:                 6 rows      <- path still NULL, still invisible here

-- after the path backfill
thread_path range scan:                 7 rows
```

That is the whole design in four numbers: **adjacency recovers the row one step earlier
than the path does, and the path never has to be correct for the answer to be correct.**

### The repair pass

```sql
-- ingestion-order independence: Reddit `more` children, TG replies outside the offset
-- window, X replies to unfetchable posts
UPDATE items SET parent_id = (
        SELECT p.id FROM items p
         WHERE p.container_id = items.container_id
           AND p.platform_item_id = items.parent_ref)
 WHERE parent_id IS NULL AND parent_ref IS NOT NULL;
-- SEARCH items USING INDEX idx_items_dangling (source=? AND parent_ref>?)

-- then backfill the derived columns for anything newly adopted
UPDATE items
   SET thread_path = (SELECT p.thread_path FROM items p WHERE p.id = items.parent_id)
                     || '/' || printf('%010x', items.id),
       depth       = (SELECT p.depth   FROM items p WHERE p.id = items.parent_id) + 1,
       root_id     = (SELECT p.root_id FROM items p WHERE p.id = items.parent_id)
 WHERE thread_path IS NULL AND parent_id IS NOT NULL
   AND (SELECT p.thread_path FROM items p WHERE p.id = items.parent_id) IS NOT NULL;
```

Run both after every ingest, and make the backfill iterate until it changes zero rows — a
chain of orphans resolves one level per pass.

**Ordering caveat, stated honestly.** `ORDER BY thread_path` gives parents-before-children
with siblings in *ingestion id* order. That approximates chronological order within one
fetch. It is **not** Reddit's "best" or "top" sort. For display ordering, extract the
subtree by range scan (fast) and sort in Python — it is one thread, and the candidate set
is small.

**Consistency check for `doctor`**, because nothing in SQLite enforces that the
denormalization agrees with the truth:

```sql
SELECT count(*) AS drifted FROM items
 WHERE thread_path IS NOT NULL AND parent_id IS NULL AND depth > 0;
```

### Reconstructing a conversation

No tree involved. One index, no sort:

```sql
SELECT source_seq,
       datetime(published_at,'unixepoch','+7 hours') AS t,
       CASE WHEN is_from_self THEN 'me'
            ELSE COALESCE((SELECT COALESCE(display_name, handle, label)
                             FROM authors WHERE id = items.author_id), '?') END AS who,
       text,
       parent_id AS reply_to
  FROM items
 WHERE container_id = :chat
 ORDER BY source_seq;
-- SEARCH items USING INDEX idx_items_stream (container_id=?)
```

For a source with no per-container ordinal (Zalo, and X archive DMs), use
`ORDER BY COALESCE(source_seq, published_at)` — at the cost of the covering-index property,
since the expression cannot be satisfied from `idx_items_stream` alone.

---

## 6. Metrics as a time series

**A change-log, not a sample-log.** A row is written only when the value moved.

The arithmetic that forces this: five metrics × 100k items × daily sampling is roughly
180 M rows a year, and essentially all of it says nothing — a year-old post's score stopped
moving eleven months ago. Old content stops changing and therefore stops writing.

```sql
INSERT INTO metric_observations(item_id, metric_id, observed_at, value, approximate, source_kind)
SELECT :item, :metric, :now, :value, :approx, :src
 WHERE NOT EXISTS (
   SELECT 1 FROM metric_observations m
    WHERE m.item_id = :item AND m.metric_id = :metric AND m.value = :value
      AND m.observed_at = (SELECT max(observed_at) FROM metric_observations
                            WHERE item_id = :item AND metric_id = :metric));
```

Verified with three observations of a Reddit score — 220, 220, 265:

```
observed_at  value  approximate  source_kind
1788000000   220    1            live
1788007200   265    1            live
rows stored for 3 observations: 2
```

`metrics_current` on `items` carries the latest values as JSONB so the timeline query never
needs a correlated `MAX`. It is updated in the same transaction.

```
$ sqlite3 t.db "SELECT metrics_current ->> '\$.score', metrics_current ->> '\$.comments',
                       typeof(metrics_current) FROM items WHERE id=4;"
220|31|blob
```

Per-item history needs no secondary index — the `WITHOUT ROWID` primary key *is* the index:

```
EXPLAIN QUERY PLAN
  SELECT observed_at, value FROM metric_observations
   WHERE item_id=4 AND metric_id=1 ORDER BY observed_at;
-- SEARCH metric_observations USING PRIMARY KEY (item_id=? AND metric_id=?)
```

### The two flags that keep the series honest

**`approximate`** — Reddit vote-fuzzes and Facebook rounds above 1k in the UI (`1.2K` →
1200). A ±3 delta is noise. Storing `1200` without saying it is rounded lets a chart imply
a precision that does not exist. *(Reddit's current fuzzing behaviour is **UNVERIFIED** —
set the flag anyway; it costs one bit and cannot be retrofitted onto history.)*

**`source_kind`** — `'live'` versus `'archive'`. Arctic Shift's documented behaviour is
that for roughly 36 hours after a post is archived, `score` and `num_comments` come back
as 0 or 1, then get backfilled. Mixing that into a live series without a discriminator
silently corrupts it.

### Re-observation schedule

`metric_schedule` drives a decaying cadence — +1h, +6h, +24h, +72h, +7d, then stop —
batched. Reddit's `/api/info` takes many fullnames per call *(widely cited as 100 per call;
**UNVERIFIED** — if it is lower, the cost scales proportionally and is still small)*, so
3,000 tracked posts cost tens of requests rather than thousands.

### The honest weakness

Step-wise interpolation. A value is held until the next recorded change, so a chart drawn
across a three-day outage shows a flat line and then a cliff, not a gradual rise. That is
accurate but easy to misread. Any charting layer should join against `runs` and shade the
periods where no observation happened.

**The related cost, with the arithmetic done rather than asserted.** An earlier draft said
"3,000 tracked items re-observed daily is over a million envelope rows a year" and used that
number to justify a deferred compaction pass. It was wrong by about two orders of magnitude,
and the design refutes it three ways:

1. **Re-observation is batched.** `/api/info` takes ~100 fullnames per call, so 3,000
   tracked posts cost **~30 requests a day**, and one request is one envelope — ~11,000
   envelope rows a year, not 1.1M.
2. **The schedule stops.** `+1h / +6h / +24h / +72h / +7d, then stop` means each item
   generates **five** refresh events in its lifetime. Nothing is re-observed daily forever.
3. **Byte-identical responses do not create envelope rows at all** — they create
   `envelope_fetches` rows of roughly 40 bytes each, which is the whole point of the split
   below.

So the growth term that actually matters is `envelope_fetches`, plus Facebook's HTML
snapshots at ~50 KB compressed per scroll step. The compaction pass has been **dropped, not
deferred** (see [../PLAN.md](../PLAN.md) §11): the retention sweep and `envelopes.purge_after`
already bound the store, and a trigger sized against a hundredfold-inflated number is worse
than no trigger.

---

## 7. Raw payloads and reparse

### The split the original `raw_payloads` got wrong

The original plan put `sha256 TEXT NOT NULL UNIQUE` on a table that also carries
`captured_at`. Fetch an unchanged page on Monday, Tuesday and Wednesday and you get **one row
with Monday's timestamp**. The re-fetch history is destroyed, and with it the exact input to
soft-delete detection: *when did I last confirm this still existed?*

- `envelopes` — one row per **distinct byte sequence**. `sha256` UNIQUE. Free dedupe.
- `envelope_fetches` — one row per **fetch event**. History.

```
$ sqlite3 t.db "SELECT e.kind, e.sha256, count(*)||' fetches',
       datetime(min(f.fetched_at),'unixepoch','+7 hours'),
       datetime(max(f.fetched_at),'unixepoch','+7 hours')
  FROM envelopes e JOIN envelope_fetches f ON f.envelope_seq=e.seq
 GROUP BY e.seq HAVING count(*)>1;"
reddit.info|sha-r7|2 fetches|2026-08-29 14:58:20|2026-08-30 14:53:20
```

One stored body, two confirmations, a day apart. Under the single-table design that second
observation does not exist.

**Read the example carefully: it is a Reddit one, and that is deliberate.** `reddit.info` on
a batch of unchanged posts genuinely returns the same bytes day after day, which is where
the dedupe fires. **Facebook is the connector where it essentially never fires**, because
`fb.feed_html` bodies are per-scroll-step *deltas* built from the posts not yet seen in this
run (see [./sources/facebook.md](./sources/facebook.md) §7.1): different runs produce
different fresh sets, different orderings and different embedded tracking params, and within
a single run the sets are disjoint by construction. So for Facebook, *"when did I last
confirm this post existed"* does **not** come from `envelope_fetches` at all — it comes from
`items.last_seen`, bumped by the upsert, and from `items.absence_streak`. Both mechanisms
are real; they just belong to different connectors, and the schema serves both.

*(Reddit's scores are mutable and possibly vote-fuzzed, so a `reddit.info` batch is only
byte-identical while nothing in it moved. That is common for a batch of week-old posts and
rare for a batch of fresh ones — which is exactly the shape the decaying re-observation
ladder assumes.)*

### Why `AUTOINCREMENT` is load-bearing

Retention pruning, `purge_after` and per-subject redaction all DELETE envelope rows. Plain
rowid reuse would silently break every `WHERE seq > :last_processed` reparse and backfill
scan — the scan would skip rows that were assigned a *recycled, lower* seq. Verified:

```
-- delete seq 4, then insert a new envelope
new row got seq = 5
sqlite_sequence high-water: 5
```

One keyword, one real bug avoided.

### Content types per transport

| `content_type` | `kind` examples | Notes |
|---|---|---|
| `text/html` | `fb.feed_html`, `fb.post_html` | compresses ~8:1 under zlib |
| `application/json` | `reddit.listing`, `reddit.comments`, `x.timeline`, `zalo.event` | always fetched with `raw_json=1` on Reddit so bodies are not HTML-escaped |
| `application/x-msgpack` | `tg.slice` | msgpack of the TL object's `to_dict()`. `meta` carries `tl_layer` and `lib` |
| `application/zip` (member) | `x.archive.dm` | the ZIP **is** the payload |

**Honest caveat on Telegram.** An MTProto object is not "the payload" the way an HTTP body
is — the transport is decrypted and deserialized by the client library before you see it.
A structural snapshot is faithful enough that a reparse recovers fields you failed to
*map*; it does not recover fields the library failed to *decode*. Whether a Python MTProto
client exposes re-serializable raw wire bytes at all is **UNVERIFIED**. Store the
structural form and record `tl_layer` next to it, because layer numbers change several
times a year.

### Compression

`codec` and `zdict` are in the frozen DDL so the switch is a flag flip rather than a
migration. zlib at v1. zstd with trained dictionaries arrives with connector two, because
the measured win is specifically for **small** JSON/msgpack records — there is no window to
find repetition inside a 754-byte record, which is where a trained dictionary earns its
keep, while Facebook's large HTML slices already do well under zlib. Dictionaries are keyed
on `(source, kind)`, retrained on drift, and **never deleted**, because `dict_id` is on the
blob.

### The generic reparse flow

Reparse is one mechanism, not a per-connector special case.

```sql
-- 1. what is behind the current parser
SELECT e.seq, e.source, e.kind, e.body, e.codec, e.dict_id, e.content_type,
       e.first_captured_at            -- the COLUMN. `Envelope.captured_at` is the
                                      -- dataclass field; the column was renamed when
                                      -- envelope_fetches took over per-event history.
  FROM envelopes e
  JOIN parsers p ON p.source = e.source AND p.kind = e.kind
 WHERE e.parser_version < p.parser_version
   AND (:kind IS NULL OR e.kind = :kind)
 ORDER BY e.seq;                        -- seq, not time: AUTOINCREMENT makes it total
```

2. Decompress; dispatch on `(source, kind)` to the connector's **pure** `parse(env)`.
   No network, no DB, no clock — `env.captured_at` is the only time source. This is enforced
   by `test_parse_needs_no_secrets`, which constructs every connector with `secrets={}` and
   parses every fixture. Without that test, "parse is pure" is a comment that decays the
   first time someone needs one more request.
3. Upsert every draft on `UNIQUE (container_id, platform_item_id)`.
4. Write `item_envelopes` rows with `role='primary'` so content provenance survives.
5. Set `envelopes.parser_version`, `parse_status`, and bump `items.parser_version` to
   `MAX(new, existing)`.

**The upsert never deletes.** `COALESCE` everywhere, so a regressed parser degrades to
*"no new information"* rather than *"wiped the good rows"*. Verified by re-running with
`text = NULL` and a higher `parser_version`:

```
before:  text='Looking for recommendations.'  permalink='https://…'  parser_version=0
after:   text='Looking for recommendations.'  permalink='https://…'  parser_version=9
row count still: 1
```

The full statement, including the redaction guard corrected in §13:

```sql
INSERT INTO items (source, container_id, platform_item_id, item_type, title, text,
                   permalink, author_id, parser_version, first_seen, last_seen, last_seq)
VALUES (...)
ON CONFLICT (container_id, platform_item_id) DO UPDATE SET
  title = CASE WHEN items.visibility = 'redacted_locally' THEN items.title
               ELSE COALESCE(excluded.title, items.title) END,
  text  = CASE WHEN items.visibility = 'redacted_locally' THEN items.text
               ELSE COALESCE(excluded.text,  items.text)  END,
  permalink      = COALESCE(excluded.permalink, items.permalink),
  author_id      = COALESCE(excluded.author_id, items.author_id),
  parser_version = MAX(excluded.parser_version, items.parser_version),
  visibility     = CASE WHEN items.visibility = 'redacted_locally'
                        THEN items.visibility ELSE 'visible' END,
  absence_streak = 0,
  last_seen      = excluded.last_seen,
  last_seq       = excluded.last_seq;
```

### Parser versioning

`parsers(source, kind) -> parser_version` is the current version. `envelopes.parser_version`
and `items.parser_version` record what actually produced each row, so the reparse queue is
a simple inequality and `crawler explain <item>` is two integers (`first_seq`, `last_seq`)
back into the envelope store.

**Golden-parse snapshots** are `sha256(canonical_json(ParseResult))` per fixture per
`parser_version`. Bumping the version must change the snapshot deliberately. This is how
you notice that an "innocent" selector tweak silently stopped populating a field — which is
the exact failure raw-first storage exists to let you recover from.

The named future extension is **projection A/B diffing**: `--rebuild items --into items_v8`
rebuilds from stored envelopes into a shadow table, then diffs row counts and per-field fill
rates before you promote. It converts the fill-rate canary from an alarm that fires three
weeks late into a pre-flight check on your own fix. It is deferred, with its trigger — the
second Facebook parser rewrite after a DOM rotation — in [../PLAN.md](../PLAN.md) §11.

---

## 8. Cursors, coverage and sync bookkeeping

### Three things called "cursor", and only one of them is durable

| Kind | Examples | Durable? | Where it lives |
|---|---|---|---|
| **page token** | Reddit `after=t3_…`, X `next_token` | **No** — valid only inside one paging session | `runs.params` (jsonb) |
| **watermark** | Telegram max `message_id`, Reddit `created_utc`, X `since_id` | **Yes** | `cursors` |
| **none at all** | a Selenium scroll | position is not addressable | a content watermark plus a behavioural stop rule |

A page token persisted forever is the bug that silently breaks daily runs months later.
The schema forbids it: `CHECK (kind <> 'page' OR expires_at IS NOT NULL)`, with the real
control being that durable and ephemeral state live in **different tables**, not in the
same table with different flags.

### The commit ordering

Core commits the envelope, then the cursor, **in one transaction**, and there is no
`advance_cursor()` method on the connector. `kill -9` mid-run costs at most one envelope
and zero correctness, uniformly across all five transports. *"The cursor advanced past data
we never stored"* is the one silent-corruption bug that would otherwise be written five
times and gotten wrong at least once.

### Cursors are rebuildable

Because every durable proposal is persisted on the envelope that carried it, the entire
cursor table is a derived artifact:

```sql
-- crawler cursors --rebuild
SELECT target_id, cursor_json, max(seq) AS from_seq
  FROM envelopes
 WHERE cursor_json IS NOT NULL
 GROUP BY target_id;
```

```
4|{"created_utc":1787992800}|1
6|{"max_id":9199}|4
```

That turns a correctness argument into an executable recovery path after corruption or
manual surgery, for the cost of one query.

### Worked example per connector type

**Facebook — no cursor exists.** Durable state is a content watermark: the newest
`platform_item_id` plus its `published_at`. The stop rule is behavioural — halt after N
consecutive already-seen posts, **skipping `is_pinned` rows** (pinned posts sit at the top
forever and would trip the counter on scroll one) and never firing on an empty database.

```sql
INSERT INTO cursors(target_id, name, kind, axis, value, axis_value, updated_at, run_id)
VALUES (3, 'main', 'watermark', 'published_at',
        '{"newest_item":"pfbid0AAA","newest_at":1787996400}', 1787996400, :now, :run);
-- envelope coverage: cover_kind='opaque', cover_note='scroll_unmount'
-- -> no gap row is ever written for Facebook, because no honest interval exists
```

**Reddit — durable timestamp, disposable fullname.** `after`/`before` accept only
fullnames, and a fullname pointing at a deleted post makes the cursor unreliable, so the
durable state is `created_utc`:

```sql
-- durable
INSERT INTO cursors(target_id, name, kind, axis, value, axis_value, updated_at, run_id)
VALUES (4, 'main', 'watermark', 'published_at',
        '{"created_utc":1787992800}', 1787992800, :now, :run);
-- ephemeral, same run, different table
UPDATE runs SET params = jsonb('{"after":"t3_1abcdef","page":3}') WHERE id = :run;
```

Re-read with a **6-hour overlap** on the next run — cheap insurance against clock skew,
sticky posts, and remove-then-reinstate. The ~1000-item listing wall becomes a `gaps` row
with `reason='listing_cap'` written **by core** from the connector's coverage claim.

**X — Snowflake `since_id`, bounded by the UTC day.** `since_id` is exclusive and
monotonic, which makes it more robust than `start_time` (no clock skew, no boundary
double-fetch). What it cannot do: surface edits or deletions, because it never re-shows an
older post. The archive is a point-in-time capture, and the schema says so via
`envelope_fetches` and `item_versions` rather than pretending otherwise.

```sql
INSERT INTO cursors(target_id, name, kind, axis, value, axis_value, updated_at, run_id)
VALUES (5, 'main', 'watermark', 'published_at',
        '{"since_id":"1900000000000000010"}', 1787995000, :now, :run);
-- and the thing a bucket cannot express, checked BEFORE the run starts:
INSERT INTO usage_counters(source, period, metric, value, updated_at)
VALUES ('x', '2026-09', 'reads', 0, :now)
ON CONFLICT (source, period, metric) DO UPDATE SET value = value + :n, updated_at = :now;
```

**Telegram — the only exact cursor in the project.** `message_id` is per-peer monotonic, so
`iter_messages(reverse=True, offset_id=last_id)` walks forward from the watermark with no
overlap window needed. Every commit advances a monotonic high-water mark, so a crash leaves
no hole.

```sql
INSERT INTO cursors(target_id, name, kind, axis, value, axis_value, updated_at, run_id)
VALUES (6, 'main', 'watermark', 'source_seq', '{"max_id":9199}', 9199, :now, :run);
```

The single forward cursor still cannot catch edits, deletions, views or reactions, because
Telegram offers no "changed since" query. The only mechanism is re-fetch by id, which is
why a **bounded ids-refresh window** is a permanent, unavoidable second pass — 500 ids for
broadcast, 2,000 or 30 days for a conversation. It is also the part most likely to be
dropped "for now", and dropping it turns the tool into one that specifically defeats other
people's deletions.

**Zalo (deferred) — push, drained from a spool.** A `Receiver` fsyncs signed bytes into
`spool/<source>/` and the ordinary scheduled `fetch()` drains that directory, so the
persistent-stream, inbound-webhook and polled-HTTP shapes collapse into one lane. The
durable watermark is `time_value`. The spool carries a 256 MB quota and a 72-hour TTL
checked by `doctor`, because it holds unencrypted third-party message bytes *outside*
`private.db` and outside its retention sweep.

### Coverage claims and the `gaps` table

Every envelope declares what it contains over an axis interval:

| `cover_kind` | Meaning | Who claims it |
|---|---|---|
| `exact` | every item in `[lo, hi]` on `axis` is in these bytes | Reddit listing page, Telegram slice |
| `partial` | items in `[lo, hi]`, but the source admits withholding — `cover_withheld` says how many | Reddit `more`, X ceiling |
| `opaque` | bytes only; no interval claim is honestly possible | Facebook, always |

Reddit's 1000-item wall, Telegram's `UpdatesTooLong`, X's ~3,200-post ceiling and Zalo's
enrolment floor are four bespoke silent-data-loss bugs that become **one table core
maintains**. Real rows from the loaded example:

```
seq  source    kind              cover_kind  cover_lo     cover_hi     withheld  note
1    reddit    reddit.listing    exact       1787900000   1787992800   0
2    reddit    reddit.comments   partial     1787985000   1787992800   7         more_children
3    facebook  fb.feed_html      opaque      NULL         NULL         0         scroll_unmount
4    telegram  tg.slice          exact       9000         9199         0
```

`opaque` being a first-class honest value is the point. Inventing a scroll-position
interval for Facebook would be *worse* than recording none, because core would then
confidently record "no gap" over a feed that unmounted half its posts.

### SUSPECT — the failure that looks like success

Two rules, because each is blind where the other fires. Either one **rolls back the cursor
write** and sets `runs.status='suspect'`. Two consecutive escalate to a HUMAN verdict.

**(a) Claimed `exact` over a non-empty interval, found zero items.** Fires on run one.
Catches Facebook's empty-feed-with-HTTP-200, a Reddit auth-error page rendered as JSON, and
Telegram's silently-empty history after removal from a group.

```sql
SELECT e.seq, e.cover_kind, e.cover_lo, e.cover_hi
  FROM envelopes e JOIN runs r ON r.id = :run
 WHERE e.target_id = r.target_id
   AND e.cover_kind = 'exact' AND e.cover_lo IS NOT NULL
   AND r.items_new = 0;
```

**(b) Zero items from a target that produced items in each of its last three runs.**
Catches what (a) cannot see — every case where the coverage claim is `opaque`, which for
Facebook is every case.

```sql
SELECT count(*) = 3 AS was_productive
  FROM (SELECT items_new FROM runs
         WHERE target_id = :t AND status = 'ok' AND id < :run
         ORDER BY started_at DESC LIMIT 3)
 WHERE items_new > 0;
```

Rule (b) alone is blind on a brand-new target and for three runs after a reparse, which is
exactly why both exist. Never truncate `runs` — rule (b) is the only thing standing between
a quiet Facebook block and a watermark that skips real content forever.

### The absence sweep, and its two gates

An item missing from a re-fetch can mean: deleted by the author, removed by a moderator,
you lost access, you got soft-blocked, or pagination simply did not reach it. So absence
is promoted to a tombstone only after `absence_strikes` consecutive confirmations
(3 broadcast, 2 joined/conversation) **inside a positively-covered range**, and only when
both the container and the run are healthy.

```sql
UPDATE items
   SET visibility = 'deleted_upstream', deleted_upstream_at = :now
 WHERE container_id = :c
   AND absence_streak >= :strikes
   AND visibility = 'visible'
   AND (SELECT access_state FROM containers WHERE id = :c) = 'ok';
-- and the caller must additionally require runs.status = 'ok'
```

Verified, because this is the guard that prevents the worst failure in the system:

```
container access_state='left', absence_streak=3  ->  tombstoned rows: 0
container access_state='ok',   absence_streak=3  ->  tombstoned rows: 1
```

Without the `access_state` gate, leaving a Facebook group marks 4,000 posts deleted.

`Cap.DELETE_EVENTS` is deliberately **not** set for Telegram, so the sweep stays mandatory
there: `MessageDeleted` is documented as unreliable, and `UpdatesTooLong` /
`ChannelDifferenceTooLong` explicitly mean *"I will not enumerate what you missed, go
re-read history."* Any design that gates the sweep on that flag would mean DM deletions are
never detected.

---

## 9. Full-text search

**One external-content FTS5 index per file.** Because each file holds exactly one privacy
half, the triggers are **unconditional** — and that is not cosmetic.

### The corruption trap this shape eliminates

The rejected alternative was one database with conditional triggers: insert into
`items_fts_public` only when the container is not a conversation, and so on. The trap is
that the idiomatic delete from the SQLite docs is unconditional:

```sql
INSERT INTO items_fts_public(items_fts_public, rowid, title, text)
  VALUES ('delete', old.id, old.title, old.text);   -- WRONG when the INSERT was conditional
```

That issues an FTS5 `'delete'` for rowids that were never in that index. The failure mode
is what makes it dangerous:

- searches keep returning correct results immediately after — **no visible symptom**
- `INSERT INTO fts(fts) VALUES('integrity-check')` returns **OK**
- `PRAGMA integrity_check` returns **ok**
- then a *later, unrelated* write fails with `database disk image is malformed (11)`, and
  every subsequent write to `items` fails

The mechanism is documented on the SQLite forum: when the values supplied to a `'delete'`
command are not the same as those currently stored, the index goes inconsistent with its
content table. Splitting by file makes that class of bug **unrepresentable** — the delete
predicate is the insert predicate, because both are "always".

`doctor --repair` still offers the recovery: `INSERT INTO items_fts(items_fts) VALUES('rebuild');`

### Tokenizer: `unicode61 remove_diacritics 2`

`remove_diacritics 1` leaves diacritics in place for a **single codepoint carrying more than
one diacritic** — which is most of the Vietnamese vowel set (`ế` U+1EBF, `ộ` U+1ED9, `ằ`
U+1EB1 …). Only `remove_diacritics 2` folds them, and it folds both directions.

Say it that way rather than "multiple combining marks", because the two are different
problems and only one of them this setting solves. **This says nothing about NFD-decomposed
input**: a base letter followed by combining marks is a Unicode *normalisation* concern that
`remove_diacritics 2` does not address at all. If any source can deliver decomposed text,
normalise to NFC before insert — a mixed-normalisation FTS index fails silently, matching
some rows and not others with no error anywhere. Confirm at M1 whether any connector
delivers NFD.

Verified on real Vietnamese text:

```
$ MATCH '"ha noi"'    -> Ai đi Hà Nội cuối tuần này không?
$ MATCH '"hà nội"'    -> Ai đi Hà Nội cuối tuần này không?
$ highlight(...)      -> Ai đi [Hà Nội] cuối tuần này không?
$ MATCH '"gia vang"'  -> Giá vàng hôm nay tăng 300 nghìn đồng/lượng
```

### The honest cost, and how precision comes back

In Vietnamese, tone marks are semantic, not decorative: `ma` / `má` / `mà` / `mã` / `mạ`
are five different words that all fold to the token `ma`. The index is **recall-first**.

That matches how people actually type — search boxes and phone keyboards produce tone-free
Vietnamese constantly. Precision is recovered with a `LIKE` second stage over the tiny
candidate set, because the exact text is still in `items.text` verbatim:

```sql
SELECT i.id, i.text
  FROM items_fts f JOIN items i ON i.id = f.rowid
 WHERE f.items_fts MATCH '"ha noi"'                  -- fast, diacritic-blind candidates
   AND (:exact = 0 OR i.text LIKE '%Hà Nội%')        -- exact filter over a small set
 ORDER BY bm25(f, 5.0, 1.0)
 LIMIT 50;
```

### `detail = full`, and why

Vietnamese is written with spaces between **syllables**, not words — `Hà Nội` is two
tokens, `điện thoại` is two tokens. Single-token search is therefore very low precision and
**phrase queries are the primary query form**. `detail=column` saves roughly 46% of index
size and `detail=none` roughly 82%, but both **disable phrase queries**. At this corpus
size that trade is not close.

Settled options: `columnsize=1` (default, keeps `bm25()` fast); no `prefix=` index at v1
(syllable segmentation means prefix matching buys little); no `trigram` table at v1 (it is
the escape hatch for substring/CJK search and it is large); **not** `contentless_delete`,
which is for when you do not keep the text — here `items` keeps it.

Ranking is `ORDER BY bm25(items_fts, 5.0, 1.0)` — title weighted 5×. Verified:

```
$ MATCH 'pho OR hanoi' ORDER BY bm25(items_fts,5.0,1.0)
4 | -17.1697 | Best pho in Hanoi?
```

(bm25 in SQLite returns a *negative* score where more negative is better, so plain
`ORDER BY bm25(...)` ascending is already best-first.)

### What is indexed, and what is not

Only `title` and `text`. Not `extra`, not `metrics_current`, not author names, not
`entities_json`. Author search goes through `idx_authors_handle`; JSON search goes through
a promoted generated column when it becomes hot.

**The audit rule, again, because it is counter-intuitive:** never use `count(*)` on an
external-content FTS5 table to check what is indexed. It reads the content table's row
count and reports 215 for a 215-row `items` regardless of index state. Use `MATCH`.

---

## 10. Governance columns, and how they gate exports

Governance is not a policy note bolted on. It is a set of columns, a file boundary, and a
`GovernanceProfile` resolved **once by core** and handed to both the connector *and* the
persistence layer — so a connector that forgets to scrub still cannot write a phone number
into a conversation row, because `db.upsert_*` re-applies the profile. Defence in depth,
one line of code.

Full policy rationale lives in [./GOVERNANCE.md](./GOVERNANCE.md). This section covers only
what the schema does.

### The three classes and what carries them

| | `broadcast` | `joined` | `conversation` |
|---|---|---|---|
| `containers.privacy` | `broadcast` | `joined` | `conversation` |
| file | `social.db` | `social.db` | **`private.db`** (SQLCipher) |
| `viewer_account_id` | optional | optional | **`NOT NULL`, CHECK-enforced** |
| `targets.ack_third_party` | 0 | 0 | **1, trigger-enforced** |
| `targets.enrolled_at` | informational | informational | **the ingest floor** |
| `absence_strikes` | 3 | 2 | 2 |
| default `media_mode` | `link` | `link` | `link` |
| `v_items_masked` author column | real name | real name | `authors.label` unless `is_from_self` |
| exportable by default | yes | no | no |

Privacy is proposed by the connector from **platform metadata** — Telegram peer type, a
Facebook group badge, member count — never from a config default. Config may only **raise**
sensitivity. Lowering requires `--force-tier` and stamps `privacy_source='forced'` forever.
An unclassifiable target fails closed to `conversation`.

`privacy_source` is what makes that auditable after the fact:

```sql
SELECT source, title, privacy, privacy_source
  FROM containers WHERE privacy_source = 'forced';
```

### The structural controls

**There is no `phone` column anywhere in the schema.** That is a structural choice, not a
policy note: there is nowhere to put a phone number, so no connector can accidentally
persist one. `scrub()` additionally drops `phone|email|latitude|longitude|access_hash|
file_reference|online|status|read_outbox_max_id` before persistence, with `assert_clean()`
re-checking on every non-broadcast write.

Telegram's `access_hash` is the non-obvious one. It is an account-scoped *capability
token*: persisting it means storing **the ability to look strangers up**, not merely a
record that they spoke.

**`authors` joins on `actor_hmac`, never on the clear id.** That is what makes redaction one
`UPDATE` that breaks no foreign key and needs no cascade rewrite:

```sql
UPDATE authors
   SET platform_uid = NULL, display_name = NULL, handle = NULL, profile_url = NULL,
       redacted_at = :now
 WHERE actor_hmac = :hmac;
```

If you join on `platform_uid` you can never null it. The pepper (32 random bytes) lives in
the Keychain, never in the DB. Documented honestly as **pseudonymization, not
anonymization**: platform ids are a low-entropy enumerable space, and anyone holding both
the DB and the pepper can rebuild the mapping.

**Identity is stored `clear` in both files, and there is no per-target `identity_mode`
column.** A private DM archive full of `P-7f3a` labels is useless to its owner, and
pseudonymizing the author column while storing full message text is theatre — names appear
in the text. The controls that actually work are scope, encryption, retention, export gating
and purge. `actor_hmac` is present regardless so `purge --person` stays a one-liner, and
masking happens in `v_items_masked` at export time, keyed off `containers.privacy` and
`items.is_from_self` — never off a per-target flag. A column with no reader is worse than no
column, so the column is gone (ADR-0026).

### Redaction must not undo itself

```sql
-- every purge writes this, and every ingest path consults it before insert
INSERT INTO redactions(scope, actor_hmac, block_reingest, reason, requested_at, applied_at,
                       items_hit, envelopes_hit, media_hit)
VALUES ('person', :hmac, 1, :reason, :now, :now, :n_items, :n_env, :n_media);

-- the pre-insert check, served by idx_redactions_hmac (partial: only live blocks indexed)
SELECT 1 FROM redactions
 WHERE block_reingest = 1
   AND (actor_hmac = :hmac OR container_id = :container)
 LIMIT 1;
```

Without `block_reingest`, tomorrow's 08:05 run re-fetches the same conversation and
silently re-creates every row you just deleted. **A redaction you believe worked but did
not is worse than none.** Test it directly: redact, crawl a fixture that still contains the
person, assert zero rows.

The second half of that guard is the upsert. §13 documents a defect in the frozen version;
with the correction verified:

```
-- item 1 redacted: visibility='redacted_locally', text=NULL, title=NULL
-- a reparse then supplies the original text again
after corrected upsert:  visibility=redacted_locally  text=<NULL>  title=<NULL>
FTS hits for the resurrected phrase: 0
```

### Deleting for real

`PRAGMA secure_delete = ON` is a **persistent header flag set at creation** — turning it on
later does not retroactively zero pages already on the freelist. After any conversation
purge: `VACUUM`, then `PRAGMA wal_checkpoint(TRUNCATE)`.

And then tell the truth: APFS local snapshots and Time Machine may still hold the pre-purge
file. The tool prints that rather than a clean success line, and it does **not** run
`tmutil deletelocalsnapshots` itself — that is a system-wide destructive act and the user's
call.

### Upstream deletes

`--respect-upstream-deletes=auto` resolves per class:

| Class | Default | Why |
|---|---|---|
| `broadcast` | `tombstone` | for a public page, the *fact* of a deletion is often the datum |
| `joined` | `follow` | member-only content; the bounded-audience expectation extends to unsending |
| `conversation` | `follow` | an unsend is a request expressed in the only vocabulary the platform gives the other person |
| **Reddit, any class** | **`follow`, locked** | Reddit's Data API terms require dropping deleted content even when de-identified. `delete_policy_locked = True`; a CLI override is refused. *(Exact terms wording is **UNVERIFIED** — secondary sources only; read the primary before shipping the Reddit connector.)* |

### How the columns gate export

`crawler export` reads `v_items_masked` by default. Four gates, in order of how much
accidental spillage each prevents:

1. **Default filter is `privacy='broadcast'`.** Not a warning — a filter. And it always
   prints what was withheld, because a silent filter teaches the user to distrust the tool
   and reach for raw SQL, which defeats every other control.
2. **`--include-private` is refused outright when stdin is not a TTY.** Not prompted —
   refused. That makes it structurally impossible for a scheduled job to emit conversation
   data. `export` never `ATTACH`es `private.db` unless that gate has already passed, so a
   query-builder bug cannot leak what was never attached.
3. **Third-party names are pseudonymized unless `--include-names` is also passed.** The
   user's own `is_from_self` rows are never masked. This makes the common legitimate case
   — grep my own DM history — work without producing a file full of other people's names.
4. **Conversation *envelopes* are not exportable at all.** No flag exists. Removing the
   option removes the accident.

Every export writes a sidecar `.manifest.json` recording tiers included, target slugs, row
counts, date range, `names_included`, and tool version — so anything downstream checks one
small file instead of parsing a multi-GB dump. The manifest also carries Telegram's
constraint that its API terms prohibit using or aggregating Telegram data to train or
fine-tune ML models, so the restriction travels with the data.

Output paths under `~/Library/Mobile Documents`, `~/Dropbox`, `~/Google Drive`,
`~/OneDrive`, `~/Library/CloudStorage`, or inside an untracked git work tree are rejected.
Twenty lines of code that catch the single most likely real-world leak.

### The residual tension, stated plainly

Raw-first storage and deletion requests genuinely conflict. A `purge --person` must reach
into `envelopes`, but one envelope can contain fifty posts from many authors and cannot be
selectively scrubbed without rewriting the blob. **The design deletes whole envelopes that
mention the subject, losing unrelated content in them.** That is the correct trade, and it
must be documented behaviour rather than a surprise discovered mid-purge.

Likewise: while a crawl runs, the `private.db` key is in process memory and the database is
decrypted for that connection. SQLCipher covers backup and sync exfiltration — a Time
Machine snapshot, a `~/Dropbox` symlink, a stray `git add -A`. It does **not** cover malware
running as your own uid. A design that implies otherwise creates a false sense of security
that is worse than no encryption, because it changes behaviour.

---

## 11. Analyst queries

Eleven queries that answer what the user will actually ask. All executed against the loaded
example data.

### A1 — Everything from the last 24 hours, across all sources

The canonical timeline query. **Must repeat the partial index predicate verbatim** — this
exact text lives in a named module constant and is never retyped.

```sql
SELECT i.source,
       c.title AS container,
       datetime(i.published_at,'unixepoch','+7 hours') AS local_time,
       COALESCE(a.display_name, a.handle, a.label) AS author,
       COALESCE(i.title, substr(i.text,1,120)) AS excerpt,
       i.permalink
  FROM items i
  JOIN containers c ON c.id = i.container_id
  LEFT JOIN authors a ON a.id = i.author_id
 WHERE i.parent_id IS NULL
   AND i.visibility = 'visible'
   AND i.published_at > unixepoch('now','-1 day')
 ORDER BY i.published_at DESC
 LIMIT 200;
-- SEARCH i USING INDEX idx_items_recent (published_at>?)
-- SEARCH c USING INTEGER PRIMARY KEY (rowid=?)
```

```
source    container   local_time            excerpt
reddit    r/vietnam   2026-08-30 06:50:00   late night UTC
telegram  Team chat   2026-08-29 17:39:00   message 199 in the team chat
telegram  Team chat   2026-08-29 17:38:00   message 198 in the team chat
…
```

Run it against `private.db` separately for the conversation half — there is no federated
query, by design.

### A2 — Per-target volume over time, in local days

```sql
SELECT c.title AS target, i.local_day, count(*) AS n
  FROM items i JOIN containers c ON c.id = i.container_id
 WHERE i.local_day >= date('now','-30 days','+7 hours')
 GROUP BY c.title, i.local_day
 ORDER BY i.local_day DESC, n DESC;
```

```
target      local_day    n
r/vietnam   2026-08-30   1
Team chat   2026-08-29   200
r/vietnam   2026-08-29   7
@someone    2026-08-29   4
VN Devs     2026-08-29   3
```

`local_day` is a **STORED** generated column, so this is an index scan on
`idx_items_localday` rather than a full-table `date()` computation. UTC+7 is a real
constant — Vietnam has no DST — and the boundary behaves:

```
platform_item_id  utc                    local_day
t3_boundary       2026-08-29 23:50:00    2026-08-30      <- correctly the NEXT local day
```

### A3 — Most-engaged posts, cross-source

```sql
SELECT i.source,
       substr(COALESCE(i.title, i.text),1,60) AS item,
       COALESCE(i.metrics_current ->> '$.score',
                i.metrics_current ->> '$.reactions.total',
                i.metrics_current ->> '$.views') AS engagement,
       i.permalink
  FROM items i
 WHERE i.metrics_current IS NOT NULL
   AND i.visibility = 'visible'
 ORDER BY CAST(engagement AS INTEGER) DESC
 LIMIT 20;
```

```
source    item                                  engagement
telegram  Giá vàng hôm nay tăng 300 nghìn…      10432
reddit    Best pho in Hanoi?                    220
```

**Read this one carefully.** A Telegram view count and a Reddit score are not the same
quantity, and ranking them against each other is apples-to-oranges. It is useful as
"what's big *within* each source" — add `GROUP BY i.source` with a window function, or
filter to one source, before drawing any conclusion.

### A4 — What changed since yesterday

Two kinds of change, unioned: a metric moved, or the content was edited.

```sql
SELECT 'metric' AS what, mo.item_id, m.key AS field,
       mo.value, mo.approximate, mo.source_kind,
       datetime(mo.observed_at,'unixepoch','+7 hours') AS at
  FROM metric_observations mo JOIN metrics m ON m.id = mo.metric_id
 WHERE mo.observed_at > unixepoch('now','-1 day')
UNION ALL
SELECT 'edit', iv.item_id, 'text', NULL, NULL, NULL,
       datetime(iv.observed_at,'unixepoch','+7 hours')
  FROM item_versions iv
 WHERE iv.observed_at > unixepoch('now','-1 day')
   AND EXISTS (SELECT 1 FROM item_versions p
                WHERE p.item_id = iv.item_id AND p.observed_at < iv.observed_at)
 ORDER BY at DESC;
```

```
what    item_id  field  value  approximate  source_kind
metric  4        score  265    1            live
```

`approximate=1` is right there in the output, which is the point: a reader can see that a
220 → 265 move on a vote-fuzzed source is signal but a 220 → 222 move would not be.

### A5 — Full thread reconstruction

```sql
-- fast path
SELECT i.depth, i.thread_path,
       COALESCE(a.display_name, a.handle, a.label) AS author,
       i.text,
       i.metrics_current ->> '$.score' AS score
  FROM items i LEFT JOIN authors a ON a.id = i.author_id
 WHERE i.root_id = :root
   AND i.thread_path >= printf('%010x', :root)
   AND i.thread_path <  printf('%010x', :root + 1)
 ORDER BY i.thread_path;

-- and always report what is knowingly missing
SELECT more_remaining FROM items WHERE id = :root;   -- 7 in the §4.2 example
```

### A6 — A conversation, in order

```sql
SELECT source_seq,
       datetime(published_at,'unixepoch','+7 hours') AS t,
       CASE WHEN is_from_self THEN 'me'
            ELSE COALESCE(a.display_name, a.label) END AS who,
       text
  FROM items i LEFT JOIN authors a ON a.id = i.author_id
 WHERE i.container_id = :chat
 ORDER BY i.source_seq;
-- SEARCH items USING INDEX idx_items_stream (container_id=?)
```

Runs against `private.db` only.

### A7 — Find every mention of a keyword

```sql
SELECT i.source, c.title AS container,
       datetime(i.published_at,'unixepoch','+7 hours') AS local_time,
       snippet(items_fts, 1, '[', ']', '…', 12) AS hit,
       i.permalink
  FROM items_fts f
  JOIN items i      ON i.id = f.rowid
  JOIN containers c ON c.id = i.container_id
 WHERE f.items_fts MATCH :q                        -- '"ha noi"' or 'gia AND vang'
   AND i.visibility = 'visible'
   AND (:exact = 0 OR i.text LIKE '%' || :literal || '%')
 ORDER BY bm25(f, 5.0, 1.0)
 LIMIT 50;
```

Tone-free input matches toned text and vice versa. Set `:exact = 1` with `:literal` to
recover tone precision over the candidate set.

### A8 — Health: what did the last tick actually do?

```sql
SELECT r.source, c.title AS target, r.status, r.verdict, r.exit_code,
       r.envelopes, r.items_new, r.requests, r.cost_micros,
       datetime(r.started_at,'unixepoch','+7 hours') AS started,
       r.finished_at - r.started_at AS secs
  FROM runs r
  LEFT JOIN targets t   ON t.id = r.target_id
  LEFT JOIN containers c ON c.id = t.container_id
 WHERE r.started_at > unixepoch('now','-2 days')
 ORDER BY r.started_at DESC;
```

`status='empty'` versus `'ok'` is the distinction that matters: a run that returned nothing
from a productive target is a **failure**, not a quiet day.

### A9 — What do I know I am missing?

```sql
SELECT c.title AS target, g.reason, g.withheld,
       datetime(g.lo,'unixepoch','+7 hours') || ' .. ' ||
       datetime(g.hi,'unixepoch','+7 hours') AS window,
       (unixepoch('now') - g.opened_at) / 86400 AS days_open
  FROM gaps g
  JOIN targets t    ON t.id = g.target_id
  JOIN containers c ON c.id = t.container_id
 WHERE g.filled_at IS NULL
 ORDER BY g.opened_at;
```

```
target      reason    withheld  window                                       days_open
r/vietnam   withheld  7         2026-08-29 13:30:00 .. 2026-08-29 15:40:00   0
```

An open gap older than 30 days is the documented trigger to pull Arctic Shift's monthly
`.zst` dumps to local disk rather than relying on its API.

### A10 — Governance audit: what am I holding about other people?

```sql
SELECT c.source, c.kind, c.title, c.privacy, c.privacy_source, c.access_state,
       count(i.id) AS items,
       datetime(min(i.published_at),'unixepoch','+7 hours') AS oldest,
       datetime(max(i.published_at),'unixepoch','+7 hours') AS newest,
       datetime(t.enrolled_at,'unixepoch','+7 hours') AS enrolled,
       count(DISTINCT i.author_id) AS distinct_people
  FROM containers c
  JOIN targets t ON t.container_id = c.id
  LEFT JOIN items i ON i.container_id = c.id AND i.is_from_self = 0
 WHERE c.privacy = 'conversation'
 GROUP BY c.id
 ORDER BY items DESC;
```

Run against `private.db`. This is the query behind `crawler targets --sensitive`, and it is
printed unprompted in the daily report when a conversation target grows by more than the
review threshold since the last report.

### A11 — Storage accounting

```sql
SELECT source, kind, codec,
       count(*) AS envelopes,
       sum(raw_bytes)    / 1048576 AS raw_mb,
       sum(stored_bytes) / 1048576 AS stored_mb,
       round(1.0 * sum(raw_bytes) / NULLIF(sum(stored_bytes),0), 2) AS ratio
  FROM envelopes
 GROUP BY source, kind, codec
 ORDER BY sum(stored_bytes) DESC;
```

This is the query that tells you when the zstd-plus-dictionary switch is worth flipping —
look for small-record kinds (`tg.slice`, `reddit.listing`) whose ratio sits near 1.8 while
`fb.feed_html` sits near 8.

---

## 12. Migration strategy

### Per-source version counters, not one global train

```sql
CREATE TABLE schema_versions (
  source TEXT PRIMARY KEY,    -- '_core' | 'facebook' | 'telegram' | ...
  version INTEGER NOT NULL,
  applied_at INTEGER NOT NULL
) STRICT, WITHOUT ROWID;
```

Facebook's inevitable DOM-rotation migrations must not drag Telegram through a version
bump. `_core` owns everything in §3; a source's counter owns only its own additions —
which at v1 is nothing, because per-source detail tables are empty.

### The runner

- Migrations are numbered SQL files, applied inside one transaction each, recorded on
  commit.
- **Both files get every migration.** The runner opens `social.db` and `private.db` in turn
  and applies the identical statement list. `doctor` fails loudly if the two files report
  different `_core` versions — divergent DDL is what would break the "identical schema"
  invariant that makes `rm private.db` safe.
- Migrating twice is a no-op. That is the first test.

### What is cheap, what is expensive

| Change | Cost | Notes |
|---|---|---|
| Add a nullable column | cheap | `ALTER TABLE ADD COLUMN` |
| Add a `VIRTUAL` generated column + partial index | cheap | this is the promotion escape hatch — **verified** |
| Add an index | cheap | |
| Add a table | cheap | |
| Add a `STORED` generated column | **impossible** | `Error: cannot add a STORED column` — verified. This is why `local_day` is in the initial DDL. |
| Widen a `CHECK` enum | **table rebuild** | 12-step procedure |
| Change a `UNIQUE` key | **table rebuild** + re-key | |
| Add a column to `items_fts` | **rebuild the index** | |

### The rebuild procedure, when it is unavoidable

Follow SQLite's documented 12-step sequence, and note the two steps people skip:

```sql
PRAGMA foreign_keys = OFF;          -- OUTSIDE the transaction; it is a no-op inside one
BEGIN;
  -- 1. create items_new with the changed definition
  -- 2. INSERT INTO items_new SELECT ... FROM items
  DROP TABLE items;                  -- this fires items_ad for every row: see below
  ALTER TABLE items_new RENAME TO items;
  -- 3. recreate every index, trigger and view that referenced items
  INSERT INTO items_fts(items_fts) VALUES('rebuild');
COMMIT;
PRAGMA foreign_keys = ON;
PRAGMA foreign_key_check;            -- verify before you trust it
```

**Drop the FTS triggers before the rebuild and recreate them after.** Otherwise
`DROP TABLE items` fires `items_ad` once per row against an index whose content table is
mid-surgery — which is precisely the "delete values that do not match what is stored"
condition that corrupts an external-content index. Then `'rebuild'` from the new content
table.

### Handling `extra` schema drift

`extra` is JSONB with no schema, which is the point — a connector adding a field is not a
migration. Two disciplines keep that from rotting:

- **`field_stats` is the canary.** Per run, per field, seen versus filled. A collapse in
  fill rate is the early warning that a source changed shape, and it works identically for
  a Facebook DOM rotation and a Reddit JSON field disappearing.
- **Promotion is a deliberate act.** When a field becomes hot, promote it to a `VIRTUAL`
  generated column plus a partial index, and record why in the migration file. Do not query
  `extra ->> '$.x'` in a hot path without promoting it — that is a full scan.

### Reparse is not a migration

Bumping `parsers.parser_version` is a data operation, not a schema operation: the reparse
queue picks up every envelope behind the new version and re-derives rows from bytes you
already hold. That is the payoff of raw-first storage and it should be exercised before you
need it — reparse after a deliberate no-op version bump, and confirm the golden-parse
snapshots are unchanged.

---

## 13. Defects found while validating the frozen DDL

Three. All were found by executing the frozen text rather than reading it. None changes the
design; all three change the statements you type.

> **The standing rule that comes out of all three, stated once.** Every one of these was a
> case where **the DDL was executed and the surrounding prose was not.** So: §3 is the only
> copy of the schema. A DDL fragment quoted in any other document — including
> [./GOVERNANCE.md](./GOVERNANCE.md) §3 and §16, [../ARCHITECTURE.md](../ARCHITECTURE.md)
> §4, and the source docs — must be a **verbatim copy of the executed text with a link back
> here**, never a paraphrase and never a remembered version. A paraphrased constraint is a
> constraint nobody ran.

### 13.1 `targets`' `ack_third_party` CHECK is not valid SQLite

Frozen text:

```sql
CHECK (ack_third_party = 1 OR container_id IN
         (SELECT id FROM containers WHERE privacy <> 'conversation'))
```

```
Parse error: subqueries prohibited in CHECK constraints
  CHECK (ack_third_party = 1 OR container_id IN            (
                                     error here ---^
```

SQLite does not allow subqueries in `CHECK` constraints — a `CHECK` must be evaluable from
the row alone. The frozen `containers` CHECK
(`privacy <> 'conversation' OR viewer_account_id IS NOT NULL`) is fine, because it only
reads its own row.

**Verified replacement** — two `BEFORE` triggers, in §3:

```
broadcast target,    ack=0  ->  inserted OK
conversation target, ack=0  ->  Error: conversation target requires ack_third_party=1 (19)
conversation target, ack=1  ->  inserted OK
```

Both `INSERT` and `UPDATE` need a trigger; a single insert trigger leaves an
`UPDATE targets SET ack_third_party = 0` unguarded. Re-executed after the §3 corrections:
the insert trigger and the update trigger both `ABORT` with SQLITE_CONSTRAINT (19).

**Do not "fix" the parse error by dropping the second clause.** `CHECK (ack_third_party = 1)`
parses fine and makes **every broadcast target unenrollable** — a strictly worse outcome than
the original bug, arrived at by the most natural debugging move available.

### 13.2 The redaction guard in the frozen upsert protects the wrong column

The frozen design states that the upsert "guards `visibility='redacted_locally'` so a
reparse cannot resurrect purged content". As written it does not. Verified:

```sql
-- item 1 has been redacted: visibility='redacted_locally', text=NULL
INSERT INTO items(...) VALUES (..., 'the original text came back', ...)
ON CONFLICT (container_id, platform_item_id) DO UPDATE SET
  text = COALESCE(excluded.text, items.text),          -- <-- no guard here
  visibility = CASE WHEN items.visibility='redacted_locally'
                    THEN items.visibility ELSE 'visible' END;
```

```
id  visibility          text
1   redacted_locally    the original text came back        <-- CONTENT RESURRECTED
```

The `CASE` guards `visibility` and nothing else. `COALESCE(excluded.text, items.text)`
happily accepts the new non-NULL text, so the row keeps its "redacted" label while holding
the redacted content — and the `items_au` trigger puts it straight back into the FTS index,
where it is searchable. That is the worst possible outcome: a redaction that *looks* like
it held.

**The guard has to be on every content column.** Verified correct form:

```sql
ON CONFLICT (container_id, platform_item_id) DO UPDATE SET
  title = CASE WHEN items.visibility = 'redacted_locally' THEN items.title
               ELSE COALESCE(excluded.title, items.title) END,
  text  = CASE WHEN items.visibility = 'redacted_locally' THEN items.text
               ELSE COALESCE(excluded.text,  items.text)  END,
  ...
```

```
id  visibility          text      title     FTS hits for the resurrected phrase
1   redacted_locally    <NULL>    <NULL>    0
```

Two notes on why this matters more than it looks:

- The `redactions.block_reingest` pre-insert check is the *primary* control and would
  normally stop the row before the upsert. The upsert guard is defence in depth — but it is
  the layer that catches a **reparse**, which reads bytes already on disk and therefore
  never passes through the connector's ingest filter at all.
- Add this to the redaction test explicitly: redact, then `crawler reparse`, then assert
  both zero FTS hits and NULL content — not just zero new rows.

### 13.3 `item_envelopes` had no `role` column, and five documents read one

`role='primary'` was written or filtered on in five places across four documents — the
`commit_envelope()` transaction diagram, the generic reparse flow, ADR-0048's entire
rationale, and Reddit's provenance join — while the frozen `CREATE TABLE item_envelopes`
had `item_id` and `envelope_seq` and nothing else.

That made ADR-0048's guarantee ("metric refreshes never write provenance rows") an
**unwritten convention in the writer**: nothing enforced it, nothing could audit it
afterwards, and nothing distinguished a content envelope from a metric envelope in the
table. The column is now in §3 with `CHECK (role IN ('primary'))` and in the primary key, so
the boundedness is a schema property and a second role is a migration. The object count in
§3 was re-derived after the change: **77**, not 80.

---

## 14. Verify before building

**This is the schema-facing subset.** The consolidated, project-wide checklist — every
unverified claim in every document, with how to check it and roughly how long that takes —
is [../PLAN.md](../PLAN.md) §12. Where the two overlap, PLAN §12 is the list to work from;
this section carries the schema-specific consequence of each answer.

Nothing here blocks the schema — it is frozen and it executes. These are the claims the
schema *touches* that were not confirmed at first-party level, carried forward from recon
with their confidence intact. Do not let any of them silently become fact.

| # | Claim | Status | How to settle it | Impact if wrong |
|---|---|---|---|---|
| 1 | The vendored SQLCipher build in `sqlcipher3` 0.6.2 has **FTS5** compiled in | **UNVERIFIED** | `SELECT * FROM pragma_compile_options();` on a `sqlcipher3` connection — **the first thing M0 does**, with two written branches below | **Not "nothing else changes."** If FTS5 is absent, `CREATE VIRTUAL TABLE items_fts` fails and the statement list cannot be applied to `private.db` **at all**, which breaks invariant #4. See the fork below. |

**The FTS5-in-SQLCipher fork, written out rather than waved off.** The "identical DDL in
both files" invariant (§15 #4) is what makes `rm data/private.db` safe, what lets the
migration runner apply one statement list to both, and what `doctor`'s version-equality
check tests on every run. It rests on this unverified fact, so the fork gets specified
before it is needed:

| M0 answer | What ships |
|---|---|
| **FTS5 present** (expected) | Nothing changes. Identical statement list, both files, `77` objects each. |
| **FTS5 absent** | `private.db` gets the **same statement list minus the five FTS objects** (`items_fts` + `items_ai` / `items_ad` / `items_au`). `schema_versions` gains a `_core.fts` row recording the divergence as a **declared state**, `doctor` reports it as declared rather than as an error, and `crawler search --private` degrades to a `LIKE` scan over `items.text`. At personal DM volume a `LIKE` scan is milliseconds, so the loss is ranking, not capability. |

Both branches preserve what actually matters: conversations stay searchable (which is the
flaw ADR-0024 rejected), `rm private.db` stays complete, and the divergence is a recorded
fact rather than a surprise. What is **not** acceptable is the earlier framing — "FTS in
`private.db` is opt-in anyway, so this is not a blocker" — which was wrong twice over:
[./GOVERNANCE.md](./GOVERNANCE.md) §2.1 lists conversation full-text search as
unconditional, and a failed `CREATE VIRTUAL TABLE` takes the whole migration with it.
| 2 | Reddit still **vote-fuzzes** displayed scores | UNVERIFIED | compare repeated `/api/info` reads of one post | Only affects whether `approximate=1` is honest. Set it anyway — it costs one bit and cannot be retrofitted onto history. |
| 3 | `/api/info` accepts **100 fullnames** per call | UNVERIFIED (widely cited) | one live call | Metric re-observation costs scale proportionally. Still small. |
| 4 | Reddit's Data API terms require **dropping deleted content** even de-identified | UNVERIFIED (secondary sources; the primary pages returned 403 to automated fetch) | read the Data API Wiki and Terms in a browser | Decides whether `on_upstream_delete` is `follow`-locked for Reddit. Ship `follow` regardless; it is the conservative default. |
| 5 | A Python MTProto client exposes **re-serializable raw TL wire bytes** | UNVERIFIED | inspect the library's object API during the Telegram spike | Decides `content_type` for `tg.slice`. The structural msgpack form is the safe default and is what the schema assumes. |
| 6 | Telegram delete events are replayed to a session that was offline | UNVERIFIED | observe during the Telegram spike | Only affects whether the ids-refresh window can shrink. The design already assumes it cannot and keeps the sweep mandatory. |
| 7 | X `expansions` bill as separate resources; media rides free on the post read | UNVERIFIED | one request, then read the credit ledger | Cost only. No schema impact. |
| 8 | X full-archive search is reachable on pay-per-usage | UNVERIFIED (docs and vendor blogs contradict) | check `console.x.com` | Decides whether `gaps` with `reason='timeline_ceiling'` are ever fillable. |
| 9 | Zalo message field names (`msgId`, `uidFrom`, `ts`, `threadId`) | UNVERIFIED | inspect a live payload if Zalo is ever built | `source_seq` may stay NULL for Zalo; conversation order falls back to `COALESCE(source_seq, published_at)`. |
| 10 | X `conversation_id` equals the retweet's own id for a retweet | LIKELY | one live response | Decides whether `root_id` may be derived from `conversation_id`. The schema derives it from `referenced_tweets` regardless, which is correct either way. |

Two things that **were** verified on this machine and are worth re-checking on the actual
project interpreter, because a uv-managed Python bundles its own SQLite:

```python
import sqlite3
assert sqlite3.sqlite_version_info >= (3, 45, 0)     # JSONB, ->>
# and confirm in a scratch DB:
#   local_day STORED generated column           -> works
#   FTS5 with remove_diacritics 2               -> works
#   STRICT + WITHOUT ROWID combined             -> works
```

Run `crawler doctor` on the project interpreter before writing the first migration.

---

## 15. Summary of the invariants

Eleven statements. If one of them stops being true, something is wrong.

1. **One `items` table.** `item_type` is provenance; nothing in core branches on it.
2. **Privacy is a property of the container**, resolved by core, only ever raised.
3. **A target maps to exactly one container, which maps to exactly one file.** Routing is
   total.
4. **Identical DDL in both files**, so `rm data/private.db` is complete and consistent —
   with exactly one declared, recorded exception if M0 finds no FTS5 in the SQLCipher build
   (§14 item 1). A divergence that `schema_versions` records is a declared state; a
   divergence nobody wrote down is the bug this invariant exists to prevent.
5. **Conversation envelopes go to `private.db` too**, not just parsed rows.
6. **`parent_ref` is always written**, even when unresolvable. `thread_path` may be NULL and
   correctness never depends on it.
7. **`envelopes` is one row per distinct byte sequence; `envelope_fetches` is one row per
   fetch event.** The split is what preserves "when did I last confirm this existed".
8. **The cursor never outruns the bytes** — envelope then cursor, one transaction, no
   `advance_cursor()`.
9. **Metrics are a change-log**, and `approximate` / `source_kind` are load-bearing, not
   decoration.
10. **A redaction blocks re-ingest**, and the block covers reparse as well as fetch.
11. **No `phone` column exists anywhere**, so no connector can accidentally persist one.

---

*Every SQL statement, query plan and result in this document was executed against SQLite
3.51.0 on 2026-09-01. Confidence labels on platform facts are carried forward from recon
unchanged; see §14.*
