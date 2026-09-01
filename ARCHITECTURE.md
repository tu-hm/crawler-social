# ARCHITECTURE

**crawler-social** — system design. Frozen 2026-09-01.

This document describes the shape of the system: what core owns, what a connector
owns, and the exact seam between them. It does not describe the schema (see
[./docs/DATA-MODEL.md](./docs/DATA-MODEL.md)), the per-source transports (see
[./docs/sources/](./docs/sources/)), or the build order (see [./PLAN.md](./PLAN.md) §6
for the numbered milestones and [./ROADMAP.md](./ROADMAP.md) for the phase shape and
deferred-item triggers). Every decision here is justified once, in
[./docs/DECISIONS.md](./docs/DECISIONS.md); this document says *what*, that one says
*why*, and neither repeats the other.

All code blocks are **illustrative**. Nothing in this repository is executable yet.

---

## 1. The problem

One person in Vietnam (UTC+7) wants a local, durable, queryable archive of the social
and messaging content they can already see: Facebook Pages and Groups now; Reddit,
X, Telegram and eventually Zalo later. Everything lands in SQLite, **raw-first** —
the original transport bytes are stored verbatim so a parser bug found in March can
be fixed and replayed over February's data without re-fetching it. Three run modes
(one-time, `--limit N`, daily routine) and one machine. The hard part is not any
single source; it is that the five sources have almost nothing in common. Facebook is
an obfuscated DOM behind a logged-in headful browser that Meta's terms prohibit
automating. Reddit is a REST API whose credentials may never be granted. Telegram is
a first-class user-account RPC protocol that makes scraping strictly inferior. X is a
pricing question wearing a technical costume. Zalo has no legitimate personal-account
path at all. **A design that assumes "crawler == browser" is wrong**, and a design
that abstracts over the transports is wrong in the other direction. This document
puts the seam in the one place all five agree.

---

## 2. Four operational shapes, one abstraction

The five transports produce work in four genuinely different shapes. They are not
variations on a theme; they differ in whether a cursor exists, whether a request is
even the unit of work, what a failure costs, and who initiates.

| Shape | Transport | Unit of work | Cursor | Cost of one mistake | Sources |
|---|---|---|---|---|---|
| **A. Supervised session** | headful Chrome | "the viewport after N scrolls" | none — position is not addressable | a flagged account | Facebook |
| **B. Paged request/response** | REST over OAuth | one HTTP response | exact page token + durable watermark | quota (Reddit) or **money** (X) | Reddit, X |
| **C. Stateful RPC batch** | MTProto | 100 TL objects per call | exact per-peer ordinal | `AUTH_KEY_DUPLICATED` destroys the credential permanently | Telegram |
| **D. Bytes that arrive unasked** | webhook · push stream · archive ZIP | one event or one file | arrival order only | silent data loss | Zalo, X archive, TG listen |

**The one abstraction is `Envelope`: verbatim transport bytes, plus a cursor
proposal, plus a coverage claim.** Nothing else crosses the connector boundary.

The reason is not elegance, it is that the bytes are *already* the thing core must
durably store. Raw-first is the project's central promise, and an `Envelope` is
exactly a `raw_payloads` row with two extra fields. Every other candidate abstraction
sits **upstream** of the bytes — a `Fetcher.get(url)`, a `Transport` base class, a
`Response` type — and each one forces a Selenium scroll, an HTTP GET and a 100-message
TL batch into a single call shape that none of them fit. Downstream of the bytes,
everything the tool promises is uniform: one timeline, one search index, one
governance boundary, one error taxonomy, one replay.

Shape D does not get its own scheduler lane. A push transport writes signed bytes into
a local spool directory and the *ordinary scheduled* `fetch()` drains that directory —
which collapses persistent-stream, inbound-webhook and polled-HTTP into shape B, and
makes the archive-ZIP importer the same code path as the spool drain. The scheduler
therefore sees **two lanes, not four**: "needs a GUI" and "does not".

> **Litmus test, run at every architecture review:** delete `connectors/facebook/`.
> Core must still compile and its tests must still pass. If it does not, something
> upstream of `Envelope` has leaked into core and must come back out.

---

## 3. Module map

One package, two top-level namespaces, and a hard rule about which way the arrows
point.

```
crawler/
  __main__.py       `python -m crawler`; delegates to cli.main()
  cli.py            argparse command surface; the only module that may print

  core/
    contract.py     THE FROZEN SEAM: Envelope, Cursor, Coverage, Verdict, Cap, *Draft, Protocols
    config.py       Config / TargetSpec dataclasses + YAML loader; every default lives here
    log.py          structured logging bound to (source, target_id, run_id)
    secrets.py      resolve env -> Keychain -> loud failure printing the exact `security` command
    codec.py        zlib | zstd encode/decode; zdict lookup by (source, kind)
    pace.py         Bucket (token bucket) + Budget (limit-N, deadline, spend) + BudgetExhausted
    verdict.py      dispositions, classify() dispatch, Verdict -> exit code, DISARMING set
    http.py         shared httpx session helper — a HELPER a connector may import, not a base class
    pii.py          scrub(), assert_clean(), DROP_KEYS; pure functions, no I/O
    gov.py          resolves GovernanceProfile per target; privacy raise-only; store routing
    db.py           connect(store), pragmas, SQLite>=3.45 assertion, per-source migration runner
    ident.py        actor_hmac (pepper from Keychain), authors upsert, redaction-aware
    cursors.py      durable watermark read/write; `cursors --rebuild` from stored envelopes
    coverage.py     coverage claim -> gaps rows; the two SUSPECT rules
    runs.py         run ledger open/close/tally; field_stats canary
    usage.py        usage_counters — the monthly billed-spend counter (X only)
    upsert.py       *Draft -> rows; item_versions, metric change-log, absence bookkeeping
    threads.py      thread_path encoding, parent-repair pass, recursive-CTE fallback
    media.py        link vs download; content-addressed store under media/ab/cd/<sha256>
    commit.py       commit_envelope(): the one transaction that orders bytes before cursor
    registry.py     REGISTRY dict of "module:factory"; THE ONLY module permitted to import a connector
    tick.py         the scheduler body: due targets, lanes, flocks, budget, drain, sweeps
    spool.py        Spool.put()/drain(); 256MB quota, 72h TTL          [SPECIFIED, UNBUILT at v1]
    receivers.py    Receiver supervision + StopSignal                  [SPECIFIED, UNBUILT at v1]
    retain.py       retention sweep, purge, redactions, secure vacuum
    export.py       default-deny export by privacy class + sidecar manifest
    fts.py          search entry points; doctor --repair rebuild
    doctor.py       preflight: sqlite version, FileVault, Keychain, locks, spool quota
    schema/         _core.sql, facebook.sql, telegram.sql, … applied IDENTICALLY to both files

  connectors/
    fixture.py      FixtureConnector — satisfies the Protocol, replays recorded envelopes
    facebook/       driver.py stealth.py feed.py parse.py errors.py  (selenium lives ONLY here)
    telegram/       client.py peers.py slices.py parse.py errors.py  (telethon lives ONLY here)
    reddit/         auth.py wire.py listings.py comments.py parse.py errors.py
    x/              wire.py timeline.py archive.py parse.py errors.py
    zalo/           oa/ bot/ user/  — three transports under one source  [DEFERRED]

tests/
  fixtures/<source>/…    captured by `crawler capture --redact`, never hand-copied
  golden/<source>/…      sha256(canonical_json(ParseResult)) per fixture per parser_version
scripts/
  run-tick.sh  run-receivers.sh  *.plist templates (no secrets in the templates)
```

### Import DAG

Layers import only downward. No cycle exists at any layer.

```
L0  contract, config                      stdlib only. contract imports NOTHING from core.
L1  log, codec, pace, pii, secrets, verdict, http        -> L0
L2  db  -> L0,L1(secrets,log)             gov -> L0
L3  ident, cursors, runs, usage, coverage, upsert, threads, media, spool  -> L0..L2
L4  commit  -> L0..L3   (db, upsert, cursors, coverage, runs, ident, codec, gov, pii)
L5  registry -> L0 + importlib            (resolves connector strings LAZILY)
L6  tick -> L0..L5
L7  retain, export, fts, doctor -> L0..L4    (siblings of tick, never imported BY tick)
L8  cli -> everything

connectors/<source>/ -> contract, and optionally the helpers: http, pace, spool, log, codec
```

**Three rules that keep this a DAG, enforced at review:**

1. `core.*` never imports `connectors.*`. The single exception is `registry.py`, and it
   imports by **string** through `importlib`, so the dependency is not a static edge.
2. A connector never imports `db`, `commit`, `upsert`, `tick`, `runs` or `registry`. It
   cannot open SQLite because it is never handed a connection.
3. `contract.py` imports only the standard library. It is the file both sides read, so
   anything it drags in, both sides pay for.

The mechanical proof that rule 2 holds: the test lane installs `--group core --group
reddit` (no selenium), runs a full `crawler reparse` over every Facebook fixture, and
asserts `'selenium' not in sys.modules`. See §13.

---

## 4. Data flow: `sync` to rows in SQLite

Two connectors, two completely different transports, one core. The **only** place the
two paths differ is inside the double-ruled boxes.

```
 $ crawler sync --source facebook --target vnexpress --limit 200        BROWSER
 $ crawler sync --source reddit   --target r/vietnam  --mode daily      API
         │
         ▼
 ┌── cli.py ───────────────────────────────────────────────────────────────────┐
 │ argparse → Config → doctor preflight:                                       │
 │   sqlite3.sqlite_version_info >= (3,45) · `fdesetup status` · Keychain reads │
 │   · no stale flock · spool under quota                                       │
 └─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
 ┌── core/tick.py ─── THE ONLY PROCESS THAT EVER WRITES SQLITE ────────────────┐
 │  1  gov.resolve(target)     → GovernanceProfile{privacy, store, media, …}   │
 │  2  db.connect(gov.store)   → data/social.db (plain) | data/private.db (enc) │
 │  3  runs.open(target,mode)  → run_id                                        │
 │  4  usage.check(source)     → Cap.BILLED only: refuse BEFORE spending       │
 │  5  Budget(max_items=200, deadline_ts=now+25m, spend_units, bucket=Bucket)  │
 │  6  registry.build("facebook") → importlib.import_module(...) → factory()   │
 │  7  flock("locks/<source>.lock") if caps & Cap.SINGLE_FLIGHT                │
 │  8  cursors.load(target)    → Cursor | None                                 │
 └─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼      connector.fetch(target, cursor, budget, gov)   ← LAZY generator
   ╔═════╧═══════════════════════════════╗ ╔═══════════════════════════════════╗
   ║ connectors/facebook   (shape A)     ║ ║ connectors/reddit     (shape B)   ║
   ║  Chrome profile 0700, OUTSIDE the   ║ ║  prawcore.Authorizer → bearer     ║
   ║   worktree · UC Mode · headful      ║ ║  httpx GET /r/x/new?raw_json=1    ║
   ║  navigate → scroll → snapshot the   ║ ║  read X-Ratelimit-* → correct the ║
   ║   viewport BEFORE it unmounts       ║ ║   Bucket downward if reality is   ║
   ║  budget.take(items=n, requests=1)   ║ ║   stingier than the config prior  ║
   ║  Coverage(OPAQUE) — honest: no      ║ ║  Coverage(EXACT, axis=time,lo,hi) ║
   ║   interval is claimable from a      ║ ║   or (PARTIAL, note='listing_cap',║
   ║   feed that unmounts its own DOM    ║ ║   withheld=sum(more.count))       ║
   ║  Cursor(WATERMARK, newest post key) ║ ║  Cursor(WATERMARK, created_utc)   ║
   ║  page token: none exists            ║ ║  page token → runs.params, NEVER  ║
   ║                                     ║ ║   `cursors`                       ║
   ╚═════╤═══════════════════════════════╝ ╚════════════════╤══════════════════╝
         │ yield Envelope(kind='fb.feed_html',              │ yield Envelope(
         │   content_type='text/html',                      │   kind='reddit.listing',
         │   body=<utf-8 bytes>, privacy='joined')          │   body=<json bytes>,
         │                                                  │   privacy='broadcast')
         └───────────────────────┬──────────────────────────┘
                                 ▼
 ┌── core/commit.py :: commit_envelope() ─── ONE TRANSACTION ──────────────────┐
 │  1  pii.scrub(env, gov) + assert_clean()   ← re-applied here, not trusted   │
 │  2  codec.encode(body)      zlib @v1 · zstd+zdict from connector two        │
 │  3  INSERT INTO envelopes … ON CONFLICT(sha256) DO NOTHING     → seq        │
 │  4  INSERT INTO envelope_fetches …   ← ALWAYS, even on identical bytes.     │
 │       This is what answers "when did I last confirm this still existed".    │
 │  5  res = connector.parse(env)      PURE: no network, no DB, no clock       │
 │  6  upsert drafts → authors · items · item_versions · metric_observations   │
 │       · media · item_relations · item_envelopes(role='primary')             │
 │  7  coverage.check(env.coverage, res.found)  → SUSPECT?                     │
 │       ├─ yes → status='suspect', SKIP step 10, do not advance               │
 │       └─ no  → continue                                                     │
 │  8  coverage.record_gap(...)                → gaps                          │
 │  9  runs.tally(...) + field_stats(run_id, field, seen, filled)              │
 │ 10  cursors.write(env.cursor_after)   ← LAST, and only if durable.          │
 │       The cursor can never outrun the bytes.                                │
 └─────────────────────────────────────────────────────────────────────────────┘
                                 │  loop: core pulls the NEXT envelope only now
                                 ▼
        data/social.db                          data/private.db
        plain sqlite3                           sqlcipher3, key from Keychain
        privacy ∈ {broadcast, joined}           privacy = conversation
        ├── IDENTICAL DDL ──────────────────────┤
        items + items_fts                       items + items_fts
        (unconditional triggers)                (unconditional triggers)
        envelopes + envelope_fetches            envelopes + envelope_fetches
                                                ↑ conversation RAW bytes live HERE too
```

**After the generator is exhausted** (or `BudgetExhausted`, or SIGTERM):

```
 tick.py  →  absence sweep (only if runs.status='ok' AND containers.access_state='ok')
          →  metric re-observation schedule (Cap.MUTABLE_METRICS)
          →  parent-repair pass over idx_items_dangling
          →  retain.sweep()  →  ANALYZE  →  runs.close(status, verdict, exit_code)
          →  sys.exit(verdict → 0 | 69 | 75 | 77 | 78 | 86)
```

**On an exception at any point inside `fetch()`:** core calls
`connector.classify(exc)`, gets a `Verdict`, and acts on it (§10). The partially
committed envelopes stay committed — that is the point of committing them one at a
time — and the cursor sits exactly where the last successful envelope left it.

---

## 5. The connector contract

`core/contract.py` is the frozen seam. Reproduced here in full because it is the
document; the walkthrough follows.

```python
"""core/contract.py -- the frozen connector seam.

ILLUSTRATIVE. Documentation of the interface, not a file on disk yet.
House style follows crawler-pages/webcrawler/challenge.py: module-level string
constants for kinds, frozen dataclasses, stdlib-only in core,
`from __future__ import annotations` everywhere.

Nothing crosses this boundary except an Envelope. There is no Fetcher base
class, no Transport abstraction, no shared connector superclass -- a Selenium
scroll, an HTTP GET, an MTProto RPC batch and a drained webhook spool have
nothing in common upstream of the bytes they produce. There are shared
*helpers* a connector may import (core.http, core.pace.Bucket, core.spool);
helpers, not inheritance.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import Flag, auto
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, Sequence

# ══════════════════════════════════════════════════════════════════════
# Verdicts -- what core DOES about a failure.  classify() is pure.
# ══════════════════════════════════════════════════════════════════════
OK      = "ok"       # proceed
RETRY   = "retry"    # transient; jittered backoff, retry this envelope
WAIT    = "wait"     # rate limited; retry_after exact where the server says so
HUMAN   = "human"    # credential expired/revoked; notify, exit 0, STAY stopped
STOP    = "stop"     # blocked, flagged, or over spend cap; disarm, NEVER retry
DROP    = "drop"     # target permanently gone; mark dead, advance past it
SUSPECT = "suspect"  # looked like success and wasn't -- core-detected, never raised

DISPOSITIONS = frozenset({OK, RETRY, WAIT, HUMAN, STOP, DROP, SUSPECT})


@dataclass(frozen=True)
class Verdict:
    kind: str                          # one of the constants above
    detail: str = ""
    retry_after: float | None = None   # WAIT only; None means "use policy backoff"


# Process-level projection of the same taxonomy. The subprocess boundary can
# only carry an integer; keeping the table here means a future Node sidecar
# (Zalo) or per-connector isolation needs no redesign.
EXIT_OK           = 0
EXIT_TRANSIENT    = 69   # EX_UNAVAILABLE -> retry next tick, no backoff
EXIT_RATE_LIMITED = 75   # EX_TEMPFAIL    -> scheduler backs this source off
EXIT_NEEDS_HUMAN  = 77   # EX_NOPERM      -> notify, disarm until `crawler resume`
EXIT_CONFIG       = 78   # EX_CONFIG      -> disarm, notify
EXIT_BLOCKED      = 86   # hard block / checkpoint / PEER_FLOOD -> DISARM, never retry
DISARMING = frozenset({EXIT_NEEDS_HUMAN, EXIT_CONFIG, EXIT_BLOCKED})


# ══════════════════════════════════════════════════════════════════════
# Coverage -- the claim an envelope makes about what it contains.
# This is what turns four bespoke silent-data-loss bugs into one gaps table.
# ══════════════════════════════════════════════════════════════════════
COVER_EXACT   = "exact"    # every item in [lo, hi] on `axis` is in these bytes
COVER_PARTIAL = "partial"  # items in [lo, hi], but the source admits withholding
COVER_OPAQUE  = "opaque"   # bytes only; no interval claim is honestly possible

AXIS_TIME = "published_at"
AXIS_SEQ  = "source_seq"


@dataclass(frozen=True)
class Coverage:
    kind: str                       # COVER_*
    axis: str = AXIS_TIME
    lo: int | None = None           # inclusive floor on `axis`
    hi: int | None = None           # inclusive ceiling
    withheld: int = 0               # Reddit sum(more.count); TG unexpanded; FB collapsed
    note: str = ""                  # listing_cap | scroll_unmount | enrolled_floor | ...


# ══════════════════════════════════════════════════════════════════════
# Cursors -- durability is a storage LOCATION, not a convention.
# ══════════════════════════════════════════════════════════════════════
CURSOR_WATERMARK = "watermark"   # DURABLE: monotonic, goes in `cursors`
CURSOR_PAGE      = "page"        # EPHEMERAL: goes in runs.params, never `cursors`


@dataclass(frozen=True)
class Cursor:
    kind: str
    value: str                     # JSON in the connector's own shape. Core NEVER parses it.
    axis: str = AXIS_TIME
    axis_value: int | None = None  # the durable ordinal core compares for gap detection
    expires_at: int | None = None  # REQUIRED when kind == CURSOR_PAGE

    @property
    def durable(self) -> bool:
        return self.kind == CURSOR_WATERMARK


# ══════════════════════════════════════════════════════════════════════
# Privacy -- proposed by the connector, RESOLVED by core, may only be raised.
# ══════════════════════════════════════════════════════════════════════
BROADCAST    = "broadcast"      # pages, subreddits, public channels, public timelines
JOINED       = "joined"         # closed FB group, private subreddit, large supergroup
CONVERSATION = "conversation"   # DMs, private groups <= 200, any unclassifiable target

PRIVACY_ORDER = (BROADCAST, JOINED, CONVERSATION)   # fail closed = last element

STORE_SOCIAL  = "social"        # data/social.db  -- plain sqlite3
STORE_PRIVATE = "private"       # data/private.db -- sqlcipher3, key from Keychain

RAW_NONE, RAW_META, RAW_FULL       = "none", "meta", "full"
MEDIA_NONE, MEDIA_LINK, MEDIA_DL   = "none", "link", "download"
DEL_OFF, DEL_TOMBSTONE, DEL_FOLLOW = "off", "tombstone", "follow"


@dataclass(frozen=True)
class GovernanceProfile:
    """Resolved ONCE by core, handed to the connector AND to the persistence
    layer.  A connector that forgets to scrub still cannot write a phone number
    into a conversation row, because db.upsert_* re-applies this.  Defence in
    depth, one line of code."""
    privacy: str                    # BROADCAST | JOINED | CONVERSATION
    store: str                      # STORE_SOCIAL | STORE_PRIVATE -- a file path, not a flag
    retention_days: int | None      # None = forever
    raw_mode: str
    media_mode: str
    media_max_bytes: int | None
    media_mime_allow: tuple[str, ...]
    on_upstream_delete: str
    delete_policy_locked: bool      # True for reddit -- CLI override is refused
    absence_strikes: int            # 3 broadcast, 2 joined/conversation
    rescan_window_days: int
    exportable: bool
    enrolled_at: int                # conversation ingest floor. Forward-only.
    backfill_from: int | None       # None = no backfill (the conversation default)


# ══════════════════════════════════════════════════════════════════════
# Targets and containers
# ══════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class TargetDraft:
    """What resolve() returns.  Pure: no network, no DB."""
    source: str
    platform_container_id: str      # connector-normalised, stable, unique within source
    kind: str                       # page|group|subreddit|channel|chat|dm|timeline|query
    shape: str                      # 'feed' | 'conversation'
    proposed_privacy: str           # core may RAISE this, never lower it
    display: str
    url: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)   # connector-private


@dataclass(frozen=True)
class Target:
    """A resolved, enrolled target.  One target -> exactly one container ->
    exactly one store file.  Frozen invariant; it makes routing total."""
    id: int
    source: str
    container_id: int
    platform_container_id: str
    kind: str
    shape: str
    display: str
    gov: GovernanceProfile
    params: Mapping[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════
# The unit of durable truth
# ══════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Envelope:
    source: str
    target_id: int
    kind: str                       # fb.feed_html | fb.graphql.feed | reddit.listing |
                                    # x.timeline | tg.slice | zalo.event | x.archive.dm
    body: bytes                     # VERBATIM transport bytes (msgpack for TL objects)
    content_type: str               # text/html | application/json | application/x-msgpack
    captured_at: int                # unix UTC
    coverage: Coverage
    cursor_after: Cursor | None     # honoured ONLY after this envelope commits
    privacy: str                    # stamped at fetch time -> decides the file, pre-write
    meta: Mapping[str, Any] = field(default_factory=dict)   # status, url, dc_id, sig, tl_layer


# ══════════════════════════════════════════════════════════════════════
# Parse output -- typed drafts, not a bag of Any
# ══════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class AuthorDraft:
    platform_uid: str
    handle: str | None = None
    display_name: str | None = None
    profile_url: str | None = None
    is_self: bool = False
    is_bot: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ItemDraft:
    platform_item_id: str
    item_type: str                  # provenance only; core NEVER branches on it
    author_uid: str | None = None
    parent_ref: str | None = None   # ALWAYS written when one exists, even if unresolvable
    root_ref: str | None = None
    source_seq: int | None = None   # TG message id; NULL where no ordinal exists
    published_at: int | None = None
    published_prec: str = "unknown" # exact|minute|hour|day|relative|unknown -- never lie
    edited_at: int | None = None
    title: str | None = None
    text: str | None = None
    url: str | None = None
    permalink: str | None = None
    lang: str | None = None
    is_pinned: bool = False
    is_sponsored: bool = False
    is_from_self: bool = False
    more_remaining: int = 0         # a partial tree that SAYS it is partial is correct
    extra: Mapping[str, Any] = field(default_factory=dict)   # -> jsonb


@dataclass(frozen=True)
class MetricDraft:
    item_ref: str
    metric: str                     # score | upvote_ratio | reactions.total |
                                    # reactions.<emoji> | views | forwards | comments
    value: int
    approximate: bool = False       # Reddit vote fuzzing; FB "1.2K" rounding
    source_kind: str = "live"       # 'live' | 'archive' (~36h stale from Arctic Shift)


@dataclass(frozen=True)
class MediaDraft:
    item_ref: str
    ord: int
    kind: str                       # image|video|audio|voice|file|sticker|link_card|poll
    url: str | None
    thumb_url: str | None = None
    mime: str | None = None
    byte_len: int | None = None
    width: int | None = None
    height: int | None = None
    duration_s: int | None = None
    caption: str | None = None


@dataclass(frozen=True)
class RelationDraft:
    from_ref: str
    rel: str                        # quote_of|repost_of|forward_of|crosspost_of|album_member
    to_ref: str
    to_source: str | None = None


@dataclass(frozen=True)
class TombstoneDraft:
    item_ref: str
    reason: str                     # deleted_upstream|removed_by_mod|hidden_from_us


Draft = AuthorDraft | ItemDraft | MetricDraft | MediaDraft | RelationDraft | TombstoneDraft


@dataclass
class ParseResult:
    parser_version: int
    drafts: list[Draft] = field(default_factory=list)
    found: int = 0                          # items actually recovered from these bytes
    partial: bool = False
    diagnostics: list[str] = field(default_factory=list)   # feeds the fill-rate canary


# ══════════════════════════════════════════════════════════════════════
# Budget -- limit-N, session length and the spend ceiling, as ONE object
# ══════════════════════════════════════════════════════════════════════
class BudgetExhausted(Exception): ...


@dataclass
class Budget:
    max_items: int | None = None        # --limit N (billable unit for X)
    max_requests: int | None = None
    deadline_ts: float | None = None    # Facebook's 25-minute session cap lives here
    spend_units: int | None = None      # X: billable resources remaining THIS run
    bucket: "Bucket | None" = None      # core.pace.Bucket; blocks inside take()

    def take(self, items: int = 1, requests: int = 1, spend: int = 0) -> None: ...
    def exhausted(self) -> bool: ...
```

```python
# ══════════════════════════════════════════════════════════════════════
# THE CONTRACT
# ══════════════════════════════════════════════════════════════════════
class Connector(Protocol):
    source: str
    caps: Cap
    parser_version: int

    def resolve(self, spec: str, kind: str | None = None) -> TargetDraft:
        """PURE. No network, no DB. Raises TargetSpecError with a message a
        human can act on."""

    def propose_privacy(self, draft: TargetDraft) -> str:
        """PURE. Derived from platform metadata (TG peer type, FB group badge,
        member count), never from a config default. Core may only RAISE it.
        Unclassifiable MUST return CONVERSATION -- fail closed."""

    def probe(self) -> "Probe":
        """At most ONE network call. NEVER interactive. Backs `crawler doctor`."""

    def login(self, io: "ConsoleIO") -> None:
        """Human in the loop. Called only by `crawler login`. Never by a job.
        A scheduled run with no session must fail loudly (exit 78), never
        block on stdin."""

    def fetch(self,
              target: Target,
              cursor: Cursor | None,
              budget: Budget,
              gov: GovernanceProfile) -> Iterator[Envelope]:
        """LAZY generator. Core commits each envelope before pulling the next.
        MUST stop on budget.exhausted(). MUST NOT parse. MUST NOT touch the DB.
        MUST honour gov.media_mode (do not spend requests on bytes you may not
        keep) and gov.enrolled_at (never fetch before the conversation floor).
        Traps SIGTERM: checkpoint by yielding, then return."""

    def parse(self, env: Envelope) -> ParseResult:
        """PURE: no network, no DB, no clock (use env.captured_at), no
        self-mutation. Same bytes in -> same drafts out, forever.
        Enforced by test_parse_needs_no_secrets, which constructs every
        connector with secrets={} and parses every fixture."""

    def classify(self, exc: BaseException) -> Verdict:
        """PURE. Maps a transport exception onto the taxonomy. Fixture-testable.
        This is where FloodWaitError.seconds becomes Verdict(WAIT, retry_after=n)
        and PeerFloodError becomes Verdict(STOP) -- account-wide, no defined
        duration, cannot be waited out."""

    def capabilities_note(self) -> str:
        """Free text. Core RENDERS it in `crawler capabilities` and never
        branches on it. This is where a connector says what no enum can:
        'backfill: groups only, via getGroupChatHistory; no 1:1 DM history
        method exists' or 'listings hard-stop at ~1000 items via `after`
        fullnames; there is no page 11'."""

    def close(self) -> None: ...


class Receiver(Protocol):
    """Push transports ONLY. Writes bytes to a local spool. NEVER opens SQLite
    -- that is what keeps exactly one process writing rows, keeps a listener
    from contending with the batch job on the WAL, and scopes
    AUTH_KEY_DUPLICATED to one flock."""
    source: str

    def serve(self, spool: "Spool", stop: "StopSignal") -> None:
        """Long-lived. spool.put(headers, body) is durable on return (fsync,
        then atomic rename). Returns when stop.set(). Subject to the spool
        quota (256MB) and TTL (72h) enforced by core."""


# ══════════════════════════════════════════════════════════════════════
# What a connector imports FROM core.  Helpers, not inheritance.
# ══════════════════════════════════════════════════════════════════════
def open_store(store: str) -> sqlite3.Connection:
    """store in {STORE_SOCIAL, STORE_PRIVATE}. STORE_PRIVATE -> sqlcipher3.dbapi2,
    key from Keychain, PRAGMA key FIRST, refuses to open if `fdesetup status`
    reports FileVault off."""

def secret(name: str) -> str:
    """env CRAWLER_<SOURCE>_<NAME> -> Keychain -> LOUD failure printing the
    literal `security add-generic-password` command. Never a silent file fallback."""

def commit_envelope(conn: sqlite3.Connection, env: Envelope,
                    res: ParseResult, run_id: int) -> int:
    """ONE transaction: dedupe the body on sha256, INSERT the envelope, ALWAYS
    append an envelope_fetches row, upsert the drafts, cross-check coverage vs
    res.found (SUSPECT), record any gap, and ONLY THEN write cursor_after.
    The cursor cannot outrun the bytes. This is the single correctness property
    worth centralising, because core owns both tables."""
```

### Method walkthrough

| Method | Called by | Network? | DB? | Contract |
|---|---|---|---|---|
| `resolve` | `crawler targets add` | **no** | **no** | Pure string → `TargetDraft`. `"r/vietnam"`, `"https://facebook.com/groups/123"`, `"@channel"` all normalise here. Raises `TargetSpecError` with an actionable message, never a traceback. |
| `propose_privacy` | `crawler targets add`, then core | **no** | **no** | Reads only what the draft already carries. Returns `CONVERSATION` when unsure. Core may raise, never lower. |
| `probe` | `crawler doctor` | ≤1 call | no | "Is this credential alive?" Never interactive, never expensive. Backs the health line, nothing else. |
| `login` | `crawler login` only | yes | no | The only interactive method. A scheduled job that finds no session must exit 78, not block on stdin. |
| `fetch` | `core/tick.py` | yes | **no** | The generator. See below. |
| `parse` | `core/commit.py`, `crawler reparse` | **no** | **no** | Pure. See §7. |
| `classify` | `core/tick.py` on any exception | **no** | no | Pure exception → `Verdict`. Fixture-testable from pickled/reconstructed exceptions. |
| `capabilities_note` | `crawler capabilities` | no | no | Free text. Core renders, never branches. |
| `close` | `core/tick.py`, always, in a `finally` | maybe | no | Quit the driver, disconnect the client, release the flock. Must be idempotent. |

**`fetch()` in detail** — five obligations, each of which is a real bug if dropped:

1. **Lazy.** Core commits envelope *n* before requesting *n+1*. A connector that
   builds a list and returns it converts `kill -9` from "lose one envelope" into
   "lose the run".
2. **Stops on `budget.exhausted()`.** Checked at every natural boundary — after a
   scroll, after a page, after a TL batch. This is how `--limit N`, Facebook's
   25-minute session cap and X's per-run spend ceiling are all one mechanism.
3. **Never parses.** Yielding requires only enough inspection to build a `Coverage`
   and a `Cursor`. Reading `data.children[-1].data.created_utc` from a Reddit listing
   is permitted; building an `ItemDraft` is not.
4. **Never touches the DB.** It is never handed a connection, so this is structural,
   not a rule. See import rule 2 in §3.
5. **Honours `gov`.** `media_mode='link'` means do not spend requests on bytes you
   are not allowed to keep. `enrolled_at` means never request messages older than
   the conversation floor — the forward-only guarantee has to hold in `fetch()`,
   because filtering it out afterwards means the bytes already crossed the wire.

**`cursor_after` is a proposal, not a command.** Core honours it only after the
envelope commits, and only if `cursor.durable`. A `CURSOR_PAGE` proposal goes into
`runs.params` and dies with the run. There is deliberately **no** `advance_cursor()`
method: a connector method that mutates cursor state is exactly how "the cursor
advanced past data we never stored" gets written five times and gotten wrong once.

---

## 6. Capability model

```python
class Cap(Flag):
    NONE             = 0
    # transport shape -- the ONLY place transport leaks into core (~10 lines)
    NEEDS_SESSION    = auto()   # bounded, supervised, expensive to open
    NEEDS_GUI        = auto()   # implies NEEDS_SESSION; must run in an Aqua session
    SINGLE_FLIGHT    = auto()   # exactly one process may hold this credential (flock)
    PUSH             = auto()   # has a Receiver; core supervises it and drains its spool
    FILE_IMPORT      = auto()   # fetch() reads a local path (archive ZIP, drained spool)
    PARALLEL_TARGETS = auto()   # thread pool vs strictly serial
    NEEDS_HUMAN_LOGIN= auto()
    # what the source can actually give
    BACKFILL         = auto()
    BACKFILL_CAPPED  = auto()   # walks back to a hard ceiling, then stops
    EXACT_CURSOR     = auto()   # resume needs no overlap re-read
    COMMENT_TREE     = auto()
    MUTABLE_METRICS  = auto()   # counts drift; core schedules re-observation
    DELETE_EVENTS    = auto()   # transport reports deletions; skip the absence sweep
    # operational and legal shape
    BILLED           = auto()   # every request costs money; core enforces a spend counter
    CONVERSATIONS    = auto()   # can reach third parties' private data
```

**The standing rule: a capability may exist only if core branches on it.** Every flag
below names the exact branch point. If a review cannot find the `if caps & X`, the
flag is documentation and belongs in the README, not the enum.

| Flag | Where core branches | What the branch does |
|---|---|---|
| `NEEDS_SESSION` | `tick.py` lane assignment | serialise the source; set `Budget.deadline_ts`; `close()` in a `finally` |
| `NEEDS_GUI` | `tick.py` preflight | refuse to run outside an Aqua session; route to the `tick` LaunchAgent; wrap in `caffeinate -i` |
| `SINGLE_FLIGHT` | `tick.py` before `registry.build` | `flock(locks/<source>.lock, LOCK_EX\|LOCK_NB)`; if held, exit 0 silently |
| `PUSH` | `receivers.py`, `tick.py` drain step | supervise a `Receiver`; drain `spool/<source>/` at the start of every tick |
| `FILE_IMPORT` | `tick.py` target construction | hand `fetch()` a path instead of a network target; no bucket, no `usage.check` |
| `PARALLEL_TARGETS` | `tick.py` target loop | `ThreadPoolExecutor` vs a strictly serial `for` |
| `NEEDS_HUMAN_LOGIN` | `cli.py` | `crawler login --source X` is registered; `doctor` says "run this" instead of "broken" |
| `BACKFILL` | `cli.py` argument validation | `--since` / `--backfill` are accepted at all |
| `BACKFILL_CAPPED` | `cli.py` + `coverage.py` | print the ceiling and proceed; open a `gaps` row when the ceiling is hit |
| `EXACT_CURSOR` | `tick.py` | skip the overlap re-read window; permit a connector to claim `COVER_EXACT` |
| `COMMENT_TREE` | `tick.py` phase list | run the comment-expansion phase; `--include-replies` becomes meaningful |
| `MUTABLE_METRICS` | `tick.py` post-run | populate and drain `metric_schedule` on the +1h/+6h/+24h/+72h/+7d ladder |
| `DELETE_EVENTS` | `tick.py` sweep gate | **skip** the absence sweep — the transport reports deletions itself |
| `BILLED` | `tick.py` step 4, `usage.py` | `usage.check(source)` **before** the run; `Budget.spend_units`; `runs.cost_micros` |
| `CONVERSATIONS` | `gov.py`, `cli.py`, `export.py` | `targets add` requires `ack_third_party=1`; export is gated behind the TTY check |

### Per-connector matrix

| | facebook | telegram | reddit | x (archive) | x (rest) | zalo.bot | zalo.oa | zalo.user |
|---|---|---|---|---|---|---|---|---|
| `NEEDS_SESSION` | ✓ | ✓ | — | — | — | — | — | ✓ |
| `NEEDS_GUI` | ✓ | — | — | — | — | — | — | — |
| `SINGLE_FLIGHT` | ✓ (profile) | ✓ (`.session`) | — | — | — | — | — | ✓ |
| `PUSH` | — | opt-in later | — | — | — | ✓ | ✓ | ✓ |
| `FILE_IMPORT` | — | — | — | ✓ | — | — | — | — |
| `PARALLEL_TARGETS` | — | — | ✓ | — | ✓ | — | — | — |
| `NEEDS_HUMAN_LOGIN` | ✓ | ✓ | ✓ (one-time) | — | — | — | ✓ | ✓ |
| `BACKFILL` | ✓ (slow, risky) | ✓ | — | ✓ | — | — | ? | groups only |
| `BACKFILL_CAPPED` | — | — | ✓ (~1000) | — | ✓ (~3,200) | — | ? | — |
| `EXACT_CURSOR` | — | ✓ | — | — | ✓ (`since_id`) | — | — | — |
| `COMMENT_TREE` | ✓ | ✓ (linked group) | ✓ | — | off by default | — | — | — |
| `MUTABLE_METRICS` | ✓ | ✓ | ✓ | — | ✓ | — | — | — |
| `DELETE_EVENTS` | — | **deliberately not set** | — | — | — | — | ✓ | — |
| `BILLED` | — | — | — | — | ✓ | — | ✓ (paid tier) | — |
| `CONVERSATIONS` | — | ✓ | — | ✓ (DMs) | — | ✓ | ✓ | ✓ |

`DELETE_EVENTS` is unset for Telegram on purpose. `MessageDeleted` is documented as
unreliable and `UpdatesTooLong` / `ChannelDifferenceTooLong` explicitly mean *"I will
not enumerate what you missed, go re-read history"*. Setting the flag would gate off
the absence sweep, and DM deletions would then silently never be detected. Zalo's OA
webhook is the one transport that genuinely reports deletion (`user_withdraw`), and
even there the sweep only skips for that transport.

**`capabilities_note()` covers what the enum cannot.** `BACKFILL_CAPPED` tells the
user nothing; *"backfill: groups only, via `getGroupChatHistory`; no 1:1 DM history
method exists"* is the sentence they actually need. Core renders it in
`crawler capabilities` and never parses it.

---

## 7. The fetch/parse split, and why replay depends on it

```
fetch()  ── impure, expensive, credentialed, time-dependent ──►  bytes
parse()  ── pure ────────────────────────────────────────────►  drafts
```

The split is **absolute**:

| | `fetch()` | `parse()` |
|---|---|---|
| network | yes | **never** |
| database | **never** | **never** |
| credentials | yes | **never** — must run with `secrets={}` |
| clock | yes | **never** — use `env.captured_at` |
| mutates `self` | yes | **never** |
| browser installed | maybe required | **never** required |
| determinism | none expected | same bytes → same drafts, forever |

### Why raw-first replay depends on this exactly

The promise is: *when Facebook rotates its DOM in March, fix the parser and recover
February.* That promise is worth exactly as much as `parse()` is pure.

- If `parse()` can make a network call, replay needs a live session — so replaying
  six months of Facebook HTML means six months of Facebook requests, which is the
  cost you stored the bytes to avoid, plus the account risk.
- If `parse()` can read the DB, replay is order-dependent and not idempotent: the
  second replay produces different drafts from the first.
- If `parse()` reads the wall clock, "3 hours ago" resolves against *today* instead
  of the capture day, and every replayed relative timestamp is silently wrong by
  however long the bytes sat on disk.
- If `parse()` needs Chrome installed, replay cannot run on the test lane, which is
  where you would actually notice a parser regression.

Purity is therefore not a style preference. It is the mechanism.

**Enforcement, because a comment decays.** `test_parse_needs_no_secrets` constructs
every registered connector with `secrets={}` and parses every fixture in
`tests/fixtures/`. Without that test, "parse is pure" survives until the first time
someone needs one more request — and that person will be you, in a hurry, six months
in.

### The purity tax, paid not negotiated

Facebook will want to fetch inside `parse()`. A truncated "See more". A comment tree
that needs a second navigation. The answer is always the same: **`fetch()` emits a
second envelope.**

```python
# connectors/facebook/feed.py -- ILLUSTRATIVE
def fetch(self, target, cursor, budget, gov):
    for html in self._scroll_and_snapshot(target, budget):
        yield Envelope(kind="fb.feed_html", body=html, ...,
                       coverage=Coverage(COVER_OPAQUE, note="scroll_unmount"),
                       cursor_after=None)                 # feed pages propose nothing

    for post_key in self._needs_expansion:                # decided by CHEAP inspection,
        if budget.exhausted():                            # not by parse()
            return
        yield Envelope(kind="fb.post_html", body=self._open(post_key), ...,
                       coverage=Coverage(COVER_OPAQUE),
                       cursor_after=Cursor(CURSOR_WATERMARK, value=..., axis_value=...))
```

This costs browser minutes. It buys six months of stored HTML that is re-parseable
after a DOM rotation. That is the trade, and it is the exact concession that keeps
the whole raw-first premise honest.

**Corollary — `parser_version` and golden snapshots.** Because `parse()` is a pure
function of bytes, `sha256(canonical_json(ParseResult))` is a stable fingerprint per
(fixture, `parser_version`). Bumping the version must change the snapshot
*deliberately*. That is how you notice an innocent-looking selector tweak silently
stopped populating `published_at` — which is precisely the failure raw-first storage
exists to let you recover from, and which you otherwise discover weeks later from
empty columns.

---

## 8. Scheduling topology

**Two LaunchAgents. Exactly one process ever writes SQLite. No home-grown supervisor.**

launchd is already a supervisor; a second one is a second thing that can be down. On
one Mac for one person that trade is not close.

### Processes

| Agent | Trigger | Lifetime | Writes SQLite | Status at v1 |
|---|---|---|---|---|
| `vn.moonbase.crawlersocial.tick` | `StartCalendarInterval` 08:05 + 20:35 | seconds to ~25 min, then exits | **yes — the only writer** | built |
| `vn.moonbase.crawlersocial.receivers` | `RunAtLoad` + `KeepAlive` | always-on | **never** | installed **empty** |
| *(optional third)* `…crawlersocial.batch` | `StartInterval 900` | seconds | yes | not at v1 |

The `receivers` agent is installed empty at v1 deliberately: the plist exists, its
`KeepAlive{SuccessfulExit=false}` semantics get proven against a real launchd, and
adding the first push transport later is a config change rather than a new operational
surface. A `Receiver` fsyncs signed bytes into `spool/<source>/` and the *ordinary*
`fetch()` drains that directory, which is what keeps the single-writer invariant true
even once a continuous listener exists.

The optional third agent buys fresher Reddit and nothing else. Add it only if a
12-hour Reddit lag actually annoys you; it costs a second flock contention path.

### macOS layout

```
~/dev/crawler-social/                    the worktree. NO credentials, NO databases here.
  data/social.db  data/private.db        (gitignored; see ./docs/GOVERNANCE.md)
  media/ab/cd/<sha256>                   content-addressed media, never SQLite BLOBs
  scripts/run-tick.sh  run-receivers.sh  set -euo pipefail; caffeinate wrapper
  scripts/*.plist                        TEMPLATES with placeholders, never real values

~/Library/Application Support/crawler-social/
  telegram/personal.session   0600       bearer credential == password + 2FA
  chrome/default/             0700       Facebook session cookie
  locks/<source>.lock         0600       flock target for Cap.SINGLE_FLIGHT
  spool/<source>/  consumed/             push bytes; 256MB quota, 72h TTL

~/Library/LaunchAgents/
  vn.moonbase.crawlersocial.tick.plist
  vn.moonbase.crawlersocial.receivers.plist

~/Library/Logs/crawler-social/
  tick.out.log  tick.err.log  receivers.out.log  receivers.err.log
```

The `.session` file and the Chrome profile live **outside the worktree** so that no
`.gitignore` mistake can ever stage them, and outside any synced directory
(iCloud/Dropbox/OneDrive) because a second concurrent use of the Telegram session from
another IP triggers `AUTH_KEY_DUPLICATED`, which destroys the credential permanently.

### The plists

```xml
<!-- vn.moonbase.crawlersocial.tick.plist -->
<key>Label</key>                 <string>vn.moonbase.crawlersocial.tick</string>
<key>ProgramArguments</key>
<array>
  <string>/Users/hoangminhtu/dev/crawler-social/scripts/run-tick.sh</string>
</array>
<key>StartCalendarInterval</key>
<array>
  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>5</integer></dict>
  <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>35</integer></dict>
</array>
<key>LimitLoadToSessionType</key> <string>Aqua</string>
<key>ProcessType</key>            <string>Interactive</string>
<key>StandardOutPath</key> <string>/Users/hoangminhtu/Library/Logs/crawler-social/tick.out.log</string>
<key>StandardErrorPath</key><string>/Users/hoangminhtu/Library/Logs/crawler-social/tick.err.log</string>
<!-- DELIBERATELY NO KeepAlive: a batch job that exits nonzero must stay exited,
     not respawn straight back into a Facebook block. -->
```

```xml
<!-- vn.moonbase.crawlersocial.receivers.plist  -- EMPTY of producers at v1 -->
<key>Label</key>          <string>vn.moonbase.crawlersocial.receivers</string>
<key>ProgramArguments</key>
<array>
  <string>/Users/hoangminhtu/dev/crawler-social/scripts/run-receivers.sh</string>
</array>
<key>RunAtLoad</key>      <true/>
<key>KeepAlive</key>
<dict>
  <key>SuccessfulExit</key><false/>   <!-- exit 0 == deliberate stop; STAY stopped -->
  <key>NetworkState</key>  <true/>    <!-- see the man-page note below -->
</dict>
<key>ThrottleInterval</key><integer>60</integer>
<key>ProcessType</key>     <string>Background</string>
```

```bash
# scripts/run-tick.sh -- ILLUSTRATIVE
set -euo pipefail
cd /Users/hoangminhtu/dev/crawler-social
exec caffeinate -i uv run crawler tick --lane gui,api
```

`caffeinate -i` creates an assertion preventing **idle** sleep for the duration of the
job, so a 25-minute Facebook session cannot be cut in half by the lid timer. Verified
locally from `man 8 caffeinate` on macOS 26.5.2. Do **not** use `pmset repeat wake` —
waking the Mac at 03:00 to scroll Facebook is the opposite of the pacing story.

### launchd semantics this design leans on

All four verified locally against `man 5 launchd.plist` (Darwin, page dated 30 July
2019) on macOS 26.5.2:

| Fact | Verbatim / verified | Consequence here |
|---|---|---|
| Missed calendar firings are deferred **and coalesced** | *"Unlike cron which skips job invocations when the computer is asleep, launchd will start the job the next time the computer wakes up. If multiple intervals transpire before the computer is woken, those events will be coalesced into one event upon wake from sleep."* | **Non-negotiable:** the daily job must catch up **by cursor**, never by "fetch yesterday". A week away yields **one** run, not seven. |
| `KeepAlive.SuccessfulExit=false` restarts on the inverse condition | *"If false, the job will be restarted in the inverse condition."* | A crash (nonzero) restarts. A `HUMAN` or `STOP` verdict alerts and exits **0**, so launchd leaves it down until `crawler resume`. This is how the error taxonomy reaches the OS. |
| `KeepAlive` implies `RunAtLoad` | *"This key implies that RunAtLoad is set to true."* | The explicit `RunAtLoad` in the receivers plist is redundant but harmless; keep it for readability. |
| `ThrottleInterval` default is 10s | *"by default, jobs will not be spawned more than once every 10 seconds"* | `60` slows a crash-loop from six restarts a minute to one. |
| `LimitLoadToSessionType` applies to **agents only** | *"This key only applies to jobs which are agents."* | Correct for both plists; it is what keeps the GUI lane out of a non-Aqua context. |

> **One correction to carry, verified locally:** `man 5 launchd.plist` on this machine
> documents `KeepAlive.NetworkState` as *"no longer implemented as it never acted how
> most users expected."* The frozen plist keeps the key (it is inert, not harmful), but
> **do not rely on it** to stop a restart loop while the Mac is offline. If offline
> thrash turns out to matter, the working control is `ThrottleInterval` plus the
> receiver exiting **0** when it cannot reach the network. Also note the man page's own
> wording: *"If multiple keys are provided, launchd **ORs** them"* — a `KeepAlive`
> dictionary is not a conjunction, which surprises most people reading it.

### Loading and operating

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/vn.moonbase.crawlersocial.tick.plist
launchctl kickstart -k gui/$(id -u)/vn.moonbase.crawlersocial.receivers   # force restart
launchctl print gui/$(id -u)/vn.moonbase.crawlersocial.tick               # last exit status
launchctl bootout gui/$(id -u)/vn.moonbase.crawlersocial.tick             # unload
```

`bootstrap` / `bootout` / `kickstart` / `print` all confirmed present in
`launchctl help` on macOS 26.5.2. The legacy `launchctl load` still exists but is the
deprecated spelling; use `bootstrap`.

### Cross-process locking

`Cap.SINGLE_FLIGHT` sources take `fcntl.flock(LOCK_EX | LOCK_NB)` on
`locks/<source>.lock` **before** `registry.build()`. If the lock is held, the run
exits **0** silently — a second overlapping tick is not an error, it is a no-op. This
is what stops the optional 15-minute batch agent from colliding with the twice-daily
agent, and what scopes Telegram's `AUTH_KEY_DUPLICATED` exposure to exactly one
process on exactly one machine.

---

## 9. Rate limiting: one bucket, one budget, one counter

Three mechanisms, because there are genuinely three things.

| Mechanism | Enforces | Lives in | Persisted? |
|---|---|---|---|
| `Bucket` (token bucket) | a **rate** | `core/pace.py` | no — per-process |
| `Budget` | a **run** (items, requests, wall clock, spend) | `core/pace.py` | no — per-run |
| `usage_counters` | a **budget in money** | `core/usage.py` + SQLite | **yes** |

A bucket smooths a rate. A counter enforces a budget. Squeezing X's monthly allowance
into a token bucket gives you a refill rate of 0.004/s and no way to answer *"how much
have I spent this month"* — so it gets its own table and is checked **before the run
starts**, not during it.

### `Budget` unifies the three things that looked unrelated

```python
Budget(
    max_items    = 200,             # --limit N. For X this is also the BILLABLE unit.
    max_requests = None,
    deadline_ts  = time.time()+1500,# Facebook's 25-minute session cap
    spend_units  = 3000,            # X: billable resources remaining THIS run
    bucket       = Bucket(capacity=3, refill_per_sec=0.2),
)
```

~20 lines, and it is the one piece of pacing that genuinely *is* shared across all
five sources — unlike the bucket constants themselves, which have nothing in common.

### Configuration

```yaml
# config/rate.yaml -- ILLUSTRATIVE
rate:
  facebook:
    mode: paranoia            # a COMMENT for humans. NO CODE BRANCHES ON THIS.
    buckets:
      navigation: { capacity: 3, refill_per_sec: 0.2 }    # ~1 navigation / 5s
    session:                                              # PLAN.md §7, numbers UNCHANGED
      scroll_dwell_s:   [2.5, 6.0]
      post_open_dwell_s:[4, 12]
      between_targets_s:[60, 180]
      max_items:        200
      max_seconds:      1500        # 25 min, then quit the driver
      sessions_per_day: 2
    on_soft_block: [900, 3600, 14400]   # 15m -> 1h -> 4h, then STOP
    on_hard_block: stop_forever

  reddit:
    mode: quota               # numbers came from a published (secondary) figure
    buckets:
      default: { capacity: 30, refill_per_sec: 0.5 }      # 30 QPM = 30% of the reported ceiling
    respect_headers: [x-ratelimit-remaining, x-ratelimit-reset, x-ratelimit-used]
    on_429: honor_retry_after
    overlap_hours: 6          # re-read window; the durable cursor is created_utc
    morechildren_budget: 32   # per post; record more_remaining for the rest

  telegram:
    mode: quota
    buckets:
      history: { capacity: 5, refill_per_sec: 0.5 }       # wait_time=2.0 -> ~3,000 msg/min
    ids_refresh_wait_s: 1.0   # Telethon does NOT auto-throttle ids fetches below 300 ids
    flood_sleep_threshold: 60 # < 60s slept transparently by the library
    flood_wait_auto_max: 300  # longer: checkpoint the cursor, exit 75, resume next tick

  x:
    mode: quota
    enabled: false            # ships OFF. Turn on only after console.x.com is confirmed.
    buckets:
      user_timeline: { capacity: 10, refill_per_sec: 0.5 }
    spend_cap: { period: month, metric: reads, limit: 4000, on_cap: stop_and_alert }
    # 01:00Z is NOT arbitrary: X deduplicates billing within a 24h UTC window, so a run
    # plus 23h of retries must sit inside ONE UTC day. 01:00Z = 08:00 ICT.
    run_at_utc: "01:00"
    exclude: [retweets]
    replies: false            # every reply is a separate billable read
    backfill_posts_per_account: 200
```

**`mode:` is a comment.** It records where the numbers came from — a published quota
(correctable by response headers) versus a paranoia budget (no feedback signal exists,
so it must be conservative by construction). **If anyone ever writes
`if mode == "paranoia"`, the abstraction has failed and must be split.**

**Feedback loop.** For sources with `respect_headers`, core drains the bucket down to
match the server's own accounting when reality is stingier than the configured prior.
Config is the prior; headers are the observation. Facebook publishes no headers, which
is exactly *why* its numbers must start conservative — the failure signal is a
checkpoint, not a 429, and by then it is too late.

---

## 10. Error taxonomy

Six in-process dispositions, projected onto six exit codes so the same taxonomy
survives a process boundary (launchd today, a Zalo Node sidecar later).

```
OK      proceed
RETRY   transient           -> jittered backoff, retry this envelope
WAIT    rate limited        -> honour retry_after where the server gives one
HUMAN   credential dead     -> notify, exit 0 from the job's view, STAY stopped
STOP    blocked / flagged   -> disarm the schedule, NEVER retry
DROP    target gone         -> mark dead, advance past it, continue the run
SUSPECT looked like success -> CORE-DETECTED ONLY. A connector never raises this.
```

| Verdict | Exit | Constant | launchd effect | `sources.state` |
|---|---|---|---|---|
| `OK` | 0 | `EX_OK` | nothing | `ok` |
| `RETRY` (exhausted) | 69 | `EX_UNAVAILABLE` | next tick retries | `ok` |
| `WAIT` (> threshold) | 75 | `EX_TEMPFAIL` | next tick resumes from the cursor | `backoff` + `state_until` |
| `HUMAN` | 77 | `EX_NOPERM` | **stays down** | `stopped` |
| *(config/no session)* | 78 | `EX_CONFIG` | **stays down** | `stopped` |
| `STOP` | 86 | *(project-local)* | **stays down** | `stopped` |
| `DROP` | 0 | — | nothing | `ok` |
| `SUSPECT` | 0 | — | nothing; cursor **not** advanced | `ok`, `runs.status='suspect'` |

`DISARMING = {77, 78, 86}`. `classify(exc) -> Verdict` is **pure** and fixture-testable.

> **Exit codes verified locally** against
> `$(xcrun --show-sdk-path)/usr/include/sysexits.h` on macOS 26.5.2:
> `EX_OK 0`, `EX_UNAVAILABLE 69`, `EX_TEMPFAIL 75`, `EX_NOPERM 77`, `EX_CONFIG 78`.
> **`86` is not a sysexits value** — `EX__MAX` is 78. It is a project-local code chosen
> deliberately *outside* the sysexits range so a hard block cannot be confused with a
> conventional one, and clear of the shell's reserved 126/127 and 128+n.

### Per-connector failure map

Each row is a real, named failure. The response column is what core does, not advice.

| Verdict | facebook | telegram | reddit | x | zalo |
|---|---|---|---|---|---|
| **RETRY** | `TimeoutException`, `StaleElementReferenceException`, chromedriver crash, navigation `WebDriverException` | `ServerError` (-500), `rpc_call_fail`, DC migrate | `httpx.ConnectError`, 500 / 502 / 503 | 500 / 503, connection reset | 5xx from the OA API, spool gap |
| **WAIT** | soft *"You're temporarily blocked"* interstitial — **no duration is supplied**, so policy backoff 15m → 1h → 4h → stop | `FloodWaitError.seconds` — **authoritative, obeyed exactly**; `SlowModeWaitError` | 429 + `Retry-After` / `X-Ratelimit-Reset` | 429 + `x-rate-limit-reset` (epoch) | OA rate-limit error code |
| **HUMAN** | redirect to `/login`, 2FA prompt, expired cookie | `AuthKeyUnregisteredError`, `SessionRevokedError`, `SessionPasswordNeededError`, `AuthKeyDuplicatedError` | 401 after refresh fails, app revoked | 401 invalid / revoked bearer | refresh token exhausted → browser re-authorisation |
| **STOP** | `/checkpoint/`, *"account disabled"*, **3rd consecutive soft block** | `PeerFloodError`, `UserDeactivatedBanError`, `PhoneNumberBannedError` | 403 from abuse rules, account suspended | **monthly spend cap reached**, account suspended | OA suspended; `zca-js` account restriction |
| **DROP** | 404 permalink, post deleted, you left the group | `ChannelPrivateError` *after* having had access, `ChatIdInvalidError` | 404, `SUBREDDIT_NOEXIST`, subreddit went private | post deleted, account protected/suspended | user unfollowed the OA |
| **SUSPECT** *(core)* | empty feed with HTTP 200 | history silently empty after removal from a group | auth-error page rendered with 200 | empty timeline from a productive account | `content_unavailable` (E2EE) vs genuinely empty |

### Three rules that live in core, not in connectors

**1. `WAIT` honours server-supplied durations literally.** Telegram's
`FloodWaitError.seconds` is authoritative and is obeyed exactly. If it exceeds 300s the
cursor is checkpointed and the run exits **75** so the next tick resumes. Facebook
supplies nothing, so it gets policy backoff — 15m → 1h → 4h → stop, per PLAN.md §7,
numbers unchanged.

**2. `STOP` disarms the schedule and never retries.** It writes
`sources.state='stopped'`, and every subsequent scheduled run for that source becomes
an immediate no-op exit 0 until `crawler resume --source X`. launchd keeps firing on
time; nothing happens. This is not caution — a retry loop is precisely how a temporary
Facebook block becomes a permanent one, and how a Telegram `PeerFloodError`
(account-wide, no defined duration, cannot be waited out) turns into an account loss.
`AuthKeyDuplicatedError` is classified `HUMAN` rather than `STOP` because the fix is
`crawler login`, but the operational effect is identical: both are in `DISARMING`.

**3. `SUSPECT` is two rules, not one, because each is blind where the other fires.**

- **(a)** an envelope claimed `COVER_EXACT` over a non-empty interval and
  `ParseResult.found == 0`.
- **(b)** zero items from a target that produced items in **each** of its last three
  runs.

Either rolls back the cursor write and sets `runs.status='suspect'`. **Two consecutive
suspect runs escalate to `HUMAN`.**

Rule (a) fires on run **one** — it catches Facebook's empty-feed-with-200, a Reddit
auth-error page and Telegram's silently-empty history immediately. Rule (b) catches
what (a) structurally cannot see: a connector that honestly claims `COVER_OPAQUE`
(Facebook always will) makes no interval claim, so (a) can never fire for it. But (b)
is blind on a brand-new target and for three runs after a reparse resets the ledger.
Hence both. This is the single most valuable rule in the system, because the failure it
catches *looks exactly like a quiet day* — the run "succeeds", the cursor advances, and
every future run skips real content forever.

---

## 11. Secrets

**macOS Keychain via `security(1)`, resolved `env → Keychain → LOUD failure`.** Never a
silent fallback to a file: a gitignored `.env` is one `git add -A` from a commit and
one Time Machine snapshot from a plaintext copy that outlives the mistake.

```
service                                    account            holds
vn.moonbase.crawler-social.reddit          client_id / client_secret / refresh_token
vn.moonbase.crawler-social.x               bearer_token
vn.moonbase.crawler-social.telegram        api_id / api_hash
vn.moonbase.crawler-social.zalo            app_secret / refresh_token / refresh_token.prev
vn.moonbase.crawler-social.pepper          pepper              32 random bytes, authors HMAC
vn.moonbase.crawler-social.private-db      key                 SQLCipher key for private.db
```

```bash
security add-generic-password -U \
  -s vn.moonbase.crawler-social.reddit -a client_secret -w 'xxxx' -T /usr/bin/security
security find-generic-password -s vn.moonbase.crawler-social.reddit -a client_secret -w
```

Resolution order in `core/secrets.py`: `CRAWLER_<SOURCE>_<NAME>` (fixture runs, CI) →
Keychain → failure that **prints the literal `security add-generic-password` command to
run**. An error a person can copy-paste is worth more than a stack trace.

### Two credentials the Keychain cannot hold

| Credential | Path | Mode | Why it is not in the Keychain |
|---|---|---|---|
| Telegram `.session` | `~/Library/Application Support/crawler-social/telegram/personal.session` | 0600 | Telethon wants a real file path. It is a **bearer credential equivalent to password + 2FA**. |
| Chrome profile | `~/Library/Application Support/crawler-social/chrome/default/` | 0700 | Selenium wants a `--user-data-dir`. Holds the Facebook session cookie. |

Both live **outside the git worktree** so no `.gitignore` mistake can stage them, and
both are `Cap.SINGLE_FLIGHT` (flocked). The `.session` file must never enter a synced
directory or be copied to a second machine: concurrent use from two IPs raises
`AUTH_KEY_DUPLICATED`, which destroys it **permanently**. Get a second session by
logging in again, never by copying.

### The Zalo rotation trap

Zalo's OA refresh token is **single-use**. A crash between spending a token and
persisting its replacement locks the connector out until a manual browser
re-authorisation — a real and entirely avoidable outage. The frozen sequence:

```
flock(zalo.token.lock)
  → write the NEW refresh token to Keychain          ← BEFORE using the new access token
  → copy the previous value to `refresh_token.prev`  ← survives one cycle
  → only now use the access token
```

*(Zalo token lifetimes are reported as a 25-hour access token and a 3-month single-use
refresh token by the official docs mirror; the 1-hour figure that circulates widely is
third-party. Confirm before building — see §15 and [./docs/sources/zalo.md](./docs/sources/zalo.md).)*

**Never committed, checked by `crawler doctor --secrets` walking the worktree:**
`*.session*`, `chrome/`, `profiles/`, `.env*`, `data/*.db*`, `secrets.yaml`, and any
installed `.plist` carrying a token in `EnvironmentVariables`. Plist **templates** in
the repo carry placeholders; the installed copies carry no secrets at all, because the
process reads the Keychain itself.

**Unverified, and it matters:** whether `security add-generic-password -T <binary>`
meaningfully restricts access when the caller is a `uv run python` process rather than
a signed application binary. Test it. If it does not hold, say so in the docs rather
than implying protection you do not have.

---

## 12. Connector registration

A hardcoded dict of lazy import strings. Boring, greppable, type-checkable.

```python
# core/registry.py -- the ONLY module permitted to import a connector
import importlib

REGISTRY: dict[str, str] = {
    "facebook":  "connectors.facebook:build",
    "telegram":  "connectors.telegram:build",
    "reddit":    "connectors.reddit:build",
    "x":         "connectors.x:build",
    "x.archive": "connectors.x.archive:build",
    "zalo.oa":   "connectors.zalo.oa:build",     # DEFERRED
    "zalo.bot":  "connectors.zalo.bot:build",    # DEFERRED
    "zalo.user": "connectors.zalo.user:build",   # DEFERRED, opt-in only
    "fixture":   "connectors.fixture:build",
}

def build(source: str, cfg: "Config") -> "Connector":
    try:
        spec = REGISTRY[source]
    except KeyError:
        raise UnknownSourceError(
            f"{source!r} is not a known source; known: {', '.join(sorted(REGISTRY))}")
    mod_name, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod_name), attr)(cfg)
```

**Why a dict and not entry points.** Entry points would require each connector to be a
distributable package — you would run `uv sync` to add a file to your own repo — and
they buy exactly one thing (third parties shipping connectors) with zero users here. A
typo yields `UnknownSourceError` listing the valid names, not silence.

**Why the values are strings.** Importing `connectors.facebook` drags in selenium.
Lazy resolution is what keeps `crawler reparse --source reddit` and the entire fixture
suite selenium-free, and it is what makes the "parse needs no credentials and no
browser" invariant *mechanically testable* rather than aspirational. That is the one
piece of indirection worth its ugliness.

**Door left open at zero cost.** `REGISTRY` is a plain dict, so adding
`REGISTRY.update(entry_points(group="crawler_social.connectors"))` later is four lines.
Preserve that by keeping `registry.py` the only module that imports a connector.

**Packaging.** One `uv` project with **per-connector dependency groups**, not five
projects. `uv sync --all-groups` locally; the test lane runs `--group core --group
reddit` and asserts `'selenium' not in sys.modules` after a full reparse, which proves
the isolation invariant mechanically instead of by convention. Trigger to split into
separate projects: an actual dependency conflict between two connectors, or the tool
needing to run on a second machine.

---

## 13. Testing without a network

The contract already forces this. `parse()` is pure and consumes exactly the bytes
core stored, so **a fixture is not a special test artifact — it is an `envelopes` row
on disk.**

```
tests/fixtures/
  facebook/  feed_html.zst  post_html.zst  post_100_comments.zst
             checkpoint_wall.html          # error path
             empty_feed_200.html           # THE SUSPECT case
  reddit/    listing_new.json  comments_tree.json  removed_post.json  429.json
  telegram/  slice.msgpack  floodwait.repr  channel_private.repr
  x/         timeline.json  429_with_reset.json  archive_dm.zip
  zalo/      user_send_text.json  bad_signature.json
  <each with a meta.json sidecar reproducing Envelope's non-body fields>

tests/golden/<source>/<fixture>@<parser_version>.sha256
```

### Capture is a command, never a copy-paste

```bash
crawler capture --source reddit   --target r/vietnam --limit 2 --out tests/fixtures/reddit/
crawler capture --source telegram --target @mytestgroup --limit 5 --redact \
                --out tests/fixtures/telegram/
```

`--redact` replaces names, phone numbers and platform ids with stable pseudonyms
**before the bytes hit disk**. Beyond that: **no real DM or private-group message
enters the repository, redacted or otherwise.** Telegram and Zalo fixtures are
synthetic or come from a test group the user owns.

### `FixtureConnector`

Satisfies the same `Connector` Protocol. `fetch()` replays recorded envelopes in
recorded order, respecting `budget`; `probe()` returns OK; **`parse()` delegates to the
real connector.** It must never grow logic of its own — the moment it reimplements
anything, the test stops testing production.

```bash
crawler tick --fixture tests/fixtures/ --db /tmp/t.db
```

That runs the **entire production pipeline** — budget accounting, commit ordering,
coverage checks, gap recording, upserts, sweeps, SUSPECT detection, the run ledger,
the canary — with no browser and no network. It is the integration test, and it is
cheap only because `Envelope` is the seam.

### Three tiers

| Tier | What it proves | Style |
|---|---|---|
| `test_parse_*` | fixture → expected drafts; a **mangled** fixture yields `None`s, never a traceback | table-driven, after `crawler-pages/tests/test_challenge.py` |
| `test_parse_needs_no_secrets` | every connector built with `secrets={}` parses every fixture | the **enforcement** of §7 |
| `test_pipeline_fixture` | `crawler tick --fixture` twice → identical row count, `last_seen` bumped, a second `envelope_fetches` row, no second `items` row | full pipeline |

### Golden-parse snapshots

`sha256(canonical_json(ParseResult))` per fixture per `parser_version`. Bumping the
version must change the snapshot **deliberately**. This is the mechanism that catches
an innocent selector tweak silently no longer populating a field.

### Invariants worth writing as tests before writing the features

1. An unclassifiable target resolves to `CONVERSATION`. *(fail closed)*
2. `assert_clean` raises on any non-broadcast write containing a `+84…` phone number
   or a Telegram `access_hash`.
3. Redact a person, then crawl a fixture that still contains them → **zero rows
   re-created**. *(`redactions.block_reingest`)*
4. `export` with no class flags emits zero rows from any joined/conversation target,
   and the manifest says so.
5. `--include-private` with a non-TTY stdin exits non-zero and writes nothing.
6. `'selenium' not in sys.modules` after a full `crawler reparse` on the core+reddit
   dependency groups.
7. A `kill -9` mid-fixture-run leaves the cursor at the last **committed** envelope,
   never past it.
8. The privacy split is audited with `MATCH` assertions, **never** `count(*)` — an
   external-content FTS5 table reads the *content* table's row count and will falsely
   pass.

---

## 14. What exists at v1, and what is only specified

The frozen ruling is **the shapes are fixed today, the machinery arrives just in
time.** The full DDL and the contract dataclasses are written now because they are
cheap to write and expensive to migrate. Everything else waits for the connector that
needs it. Milestones live in [./PLAN.md](./PLAN.md) §6; phase order and the trigger that
unblocks each deferred item live in [./ROADMAP.md](./ROADMAP.md).

| Component | v1 (Facebook) | Arrives with |
|---|---|---|
| `contract.py`, full DDL, `Cap` enum | **built** | — |
| `envelopes` + `envelope_fetches`, `commit_envelope`, cursors, runs, `field_stats` | **built** | — |
| `Budget`, `Bucket`, verdicts, exit table, the SUSPECT pair | **built** | — |
| `gaps` table + coverage recording | **built** (Facebook always claims `OPAQUE`) | drained at Reddit |
| `data/private.db` + SQLCipher + two-file router | schema only | **Telegram** |
| `item_versions`, absence/tombstone sweep, retention, purge | schema only | **Telegram** |
| zstd + trained dictionaries (`zdict`) | `codec` column only; **zlib at v1** | **Telegram** |
| `usage_counters`, billed spend counter | schema only | **X (rest)** |
| `spool/`, `Receiver`, the `receivers` LaunchAgent | **plist installed empty** | first push transport |
| `Cap.FILE_IMPORT` | — | **X archive ZIP** |
| media download (`media_mode='download'`) | per-target flag exists, defaults to `link` | on explicit request only |
| `persons` / `author_person_links` | tables exist, **zero writers** | a manual link |
| per-source detail tables | tables do not exist; the generated-column path is verified | never, so far |

---

## 15. Verify before building

Nothing below is settled. Each item is either second-hand, contradicted between
sources, or environment-dependent. **Do not upgrade any of these into a stated fact,
and do not invent a number to fill the gap.** Per-source detail and citations live in
[./docs/sources/](./docs/sources/).

### Blocks a design decision

| # | Claim | Status | How to settle it |
|---|---|---|---|
| V1 | A new Reddit OAuth app can be registered at all (self-service ended Nov 2025; manual review under the Responsible Builder Policy, stated ~7-day target) | **likely**, secondary sources only — `support.reddithelp.com` returned 403 to automated fetches | **Phase 0 / R0.** Submit the application on day one. It is free, and everything downstream of Reddit forks on the answer. |
| V2 | SQLCipher's vendored build in `sqlcipher3` 0.6.2 has **FTS5** compiled in | **unverified** | `SELECT * FROM pragma_compile_options();` at M0. If absent, conversation search needs a separate index — not a blocker, since it is opt-in. |
| V3 | Keychain ACLs (`-T <binary>`) meaningfully restrict a `uv run python` caller | **unverified** | Test with a second Python process. If they do not hold, the docs must say so rather than imply protection. |
| V4 | X pay-per-usage actually unlocks full-archive search (`/2/tweets/search/all`) | **contradicted** — official docs say "Self-serve or Enterprise", a vendor blog says 7-day only | console.x.com, before enabling anything that needs it. Not blocking: the default (timelines + `since_id`) needs neither search endpoint. |
| V5 | Zalo OA `listrecentchat` / `conversation` resolve at `/v3.0/` as third-party SDKs assume, or only at the documented `/v2.0/` | **unverified** | Test both against a live token. |

### Changes a number, not the design

| # | Claim | Status | How to settle it |
|---|---|---|---|
| V6 | Reddit free tier is ~100 QPM over a ~10-minute rolling window | **likely** — consistent across independent 2026 sources, never seen in Reddit's own words | Read live `X-Ratelimit-*` headers on the first authenticated request. The `respect_headers` feedback loop makes a wrong prior self-correcting. |
| V7 | `/api/info?id=…` accepts 100 fullnames per call | **unverified** | One request. If lower, metric re-observation costs proportionally more (still small). |
| V8 | X per-resource prices ($0.005/post read, $0.010/user read) and the monthly cap | **conflicted** — recon fetched docs.x.com and reports the price list verbatim; a parallel recon could not reach it and marks every figure unverified; official docs say 3M reads/month, vendor blogs say 2M | **console.x.com**, before the connector is enabled. This is the one connector where being wrong costs money. |
| V9 | Whether X `expansions` bill as separate resources, and whether media rides free | **unverified** — there is no media line item, but absence of a line item is not an exemption | One request, then read the credit ledger. |
| V10 | Telegram takeout's flood-limit multiplier (`wait_time=0.5` is a guess, not a budget) | **unverified** — the docs say only "some calls will have lower flood limits" | Instrument the first backfill and adjust. |
| V11 | Telethon 1.44.0 runs clean on Python 3.13 (its `python_requires='>=3.5'` and 3.8-max classifiers are stale metadata) | **unverified** | Smoke-test on the actual project interpreter before pinning. |
| V12 | Zalo OA rate limit — official appendix says a flat 4,000 req/min but is footered ©2023; trade sources say 100 (Tăng trưởng) / 2,000 (Toàn diện) req/min per tier | **contradicted** | Read `X-RateLimit-Limit` from a live response. |
| V13 | Zalo OA token lifetimes (25h access / 3-month single-use refresh per the docs mirror; 1h access widely repeated third-hand) | **unverified** | Load-bearing for the crash-safe rotation design. Confirm first. |

### Legal and policy, carried as-is

| # | Claim | Status |
|---|---|---|
| V14 | Reddit's Data API Terms require dropping content deleted upstream **even when de-identified**, with a reported 48-hour guidance | **unverified** — primary pages unreachable (403/blocked). This forces `on_upstream_delete='follow'`, locked, for Reddit; read the terms first-hand before fixing the retention default in code. |
| V15 | Telegram's API terms prohibit using or aggregating Telegram data to **train or fine-tune ML models** | **likely** — from search extracts of `core.telegram.org/api/terms`, not a direct fetch. A hard boundary if any downstream use involves an LLM. Surfaced in the export manifest so the constraint travels with the data. |
| V16 | Vietnam: Decree 13/2023 was **repealed 2026-01-01**; the operative instruments are Law 91/2025/QH15 and Decree 356/2025/NĐ-CP | **verified** across DLA Piper, Tilleke, DFDL. Any document citing Decree 13 is citing a repealed rule. |
| V17 | Whether the PDPL contains a purely-personal/household exemption | **unverified** at article level. **Design as though none applies** — the tool stores third parties' messages either way. |
| V18 | `KeepAlive.NetworkState` | **verified locally** as documented *"no longer implemented"* in `man 5 launchd.plist` on macOS 26.5.2. Inert, not harmful; do not rely on it. See §8. |

### Environment assertions (cheap, run them in `doctor`)

```python
if sqlite3.sqlite_version_info < (3, 45, 0):     # JSONB, ->>, STRICT, contentless_delete
    raise SystemExit(f"need SQLite >= 3.45; got {sqlite3.sqlite_version}")
# sqlite3.version was REMOVED in Python 3.14 -- use sqlite_version_info only.
```

A `uv`-managed interpreter bundles **its own** SQLite, which is not necessarily the one
the system `sqlite3` CLI links. On this machine (macOS 26.5.2) the pyenv 3.13.13
interpreter links SQLite 3.51.0 — verified — but that says nothing about what `uv`
will provision. Assert it in `db.connect()` and in `doctor`, not in a comment.

---

*Companion documents: [./PLAN.md](./PLAN.md) (scope, per-platform risk, the numbered
milestone plan), [./docs/DECISIONS.md](./docs/DECISIONS.md) (why each of these is frozen,
and what was rejected), [./docs/DATA-MODEL.md](./docs/DATA-MODEL.md) (the frozen DDL and
its query plans), [./docs/GOVERNANCE.md](./docs/GOVERNANCE.md) (privacy classes,
encryption, retention, export), [./ROADMAP.md](./ROADMAP.md) (phase order and the trigger
that unblocks each deferred item), [./docs/sources/](./docs/sources/) (per-connector
transports and the evidence behind them).*
