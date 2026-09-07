# Governance

Engineering requirements. Not a policy appendix — every row below is something a developer
implements, and most of them are enforced by a `CHECK` constraint, a file path or a missing
CLI flag rather than by discipline.

The organising idea:

> **Privacy is a property of the container, resolved once by core, and it decides a file path
> before a single byte is written.**

Not a column a query-builder bug can bypass. Not something a connector decides for itself. Not
a `WHERE` clause anyone has to remember.

---

## 1. The model: `GovernanceProfile`, resolved once

Core resolves one `GovernanceProfile` per target, **before the connector is constructed**, and
hands the same object to the connector *and* to the persistence layer.

```python
@dataclass(frozen=True)
class GovernanceProfile:
    privacy: str                    # broadcast | joined | conversation
    store: str                      # STORE_SOCIAL | STORE_PRIVATE -- a file path, not a flag
    retention_days: int | None      # None = forever
    raw_mode: str                   # none | meta | full
    media_mode: str                 # none | link | download
    media_max_bytes: int | None
    media_mime_allow: tuple[str, ...]
    on_upstream_delete: str         # off | tombstone | follow
    delete_policy_locked: bool      # True for reddit -- CLI override is refused
    absence_strikes: int
    rescan_window_days: int
    exportable: bool
    enrolled_at: int                # conversation ingest floor. Forward-only.
    backfill_from: int | None       # None = no backfill (the conversation default)
```

**Defence in depth, one line of code.** A connector that forgets to `scrub()` still cannot
write a phone number into a conversation row, because `db.upsert_*` re-applies the profile.
The connector *proposes*; core *resolves*; the persistence layer *re-checks*.

| Layer | Responsibility |
|---|---|
| Connector | `propose_privacy(draft) -> str`, pure, from platform metadata only |
| Core | resolve the full profile, pick the store, open the connection |
| Persistence | `assert_clean()`, redaction check, `visibility` guard, on every write |

---

## 2. Sensitivity tiers, with the per-tier defaults a developer implements

Three classes because the knobs genuinely differ across all three. Two physical files because
the encryption boundary must be a path, not a predicate.

| Class | Means | Examples |
|---|---|---|
| `broadcast` | The author published to the open internet | FB Page, subreddit, public Telegram channel, public X timeline |
| `joined` | Visible only to members; the author expects a bounded but large audience | closed FB group you belong to, private subreddit, public supergroup >200 members |
| `conversation` | A conversation. Other people's personal data, no audience expectation | any DM, private Telegram chat, basic group, private supergroup with no public link, Zalo chat, X DMs from the archive ZIP, **and anything unclassifiable** |

### 2.1 The defaults table

| Knob | `broadcast` | `joined` | `conversation` |
|---|---|---|---|
| **Store** | `data/social.db` (plain) | `data/social.db` (plain) | `data/private.db` (**SQLCipher**) |
| `retention_days` (parsed rows) | `NULL` — forever | `730` | **no default — you must set a number.** `forever` is a legal answer |
| `raw_mode` | `full` | `full` | **`full`** — see §2.2 |
| `envelopes.purge_after` | `NULL` — forever | `+90d` | **`+90d`** |
| `media_mode` | `link` | `link` | `link` |
| media `download` opt-in | per target | per target + mime allowlist + `max_bytes` | per target + `ack_third_party` + allowlist + `max_bytes` |
| Identity storage | `clear` | `clear` | **`clear`** — see §2.2(c) |
| `authors.actor_hmac` | always present | always present | always present |
| Contact PII (phone / email / location / presence) | **dropped** | **dropped** | **dropped** |
| Member rosters | n/a | **not stored** | **not stored** |
| Per-person reaction lists | stored | aggregate counts only | aggregate counts only |
| `on_upstream_delete` | `tombstone` | `follow` | `follow` |
| `absence_strikes` | `3` | `2` | `2` |
| `rescan_window_days` | `3` | `7` | `7` — **but `0` for Facebook and X**, whose coverage claims make absence evidence impossible (§8.3) |
| First-crawl ingest floor | `--since 90d` | `--since 90d` | **`enrolled_at` — forward only** |
| `backfill_from` | free | explicit | explicit, **per conversation** |
| Bulk enrolment command | `--all-pages` ok | `--all-groups` ok | **none exists** |
| `targets.ack_third_party` | not required | not required | **`= 1`, enforced by two `BEFORE` triggers** (a `CHECK` cannot express it — §3 rule 6) |
| `containers.viewer_account_id` | optional | optional | **`NOT NULL`, enforced by a real `CHECK`** |
| Full-text search | yes | yes | **yes — in its own file's index** |
| `export` default | **included** | denied → `--include-tier joined` | denied → `--include-private` + TTY |
| Third-party names on export | clear | clear | **pseudonymised** unless `--include-names` |
| **Envelopes exportable** | yes | yes | **never — no flag exists** |

### 2.2 Three deliberate overrides of the earlier governance draft, and one correction

These are frozen decisions and should not be re-litigated.

**(a) `raw_mode = 'full'` for conversations, not `'none'`.** Conversation *envelopes* go to
`private.db` alongside the parsed rows. Routing parsed DMs to the encrypted file while leaving
raw TL slices in the plain file **defeats the entire separation**, because raw-first means the
payload contains everything the parsed rows contain and more. The exposure is bounded by
`purge_after`, not by discarding the raw — which would make the raw-first promise a lie for
exactly the source where re-parsing matters most, since a fresh conversation ingest is where
parser bugs are most likely.

**That last clause is why `purge_after` for conversations is 90 days and not 30.** An earlier
draft set 30, which put the shortest replay window on the class with the highest parser-bug
rate — the two arguments pointed in opposite directions and the wrong one won. 90 days matches
`joined`, keeps a realistic "found the bug, fixed the parser, replayed the quarter" cycle
available, and is still a genuine bound rather than "forever". **State it plainly wherever the
raw-first promise is made**, because it is a real limit: a parser fix reaches back **90 days**
on `joined` and `conversation` targets and to the beginning on `broadcast` ones.

**(b) `media_mode = 'link'` everywhere at v1, not `download` for broadcast.** Media is the
**only unbounded cost in the project**. A 200k-message Telegram channel is tens of MB of text
and potentially hundreds of GB with media, and every download also spends the flood budget.
Per-target opt-in exists from day one; **never enable it globally.** Trigger: the user names a
specific target, with a mime allowlist and a `max_bytes` ceiling set in the same command.

State the cost of the default honestly: a stored URL is a **dead pointer** for Telegram
(`file_reference` expires — [./sources/telegram.md](./sources/telegram.md) §11), Zalo
(token-bearing `*.zdn.vn` paths — [./sources/zalo.md](./sources/zalo.md) §9.3) and
Facebook. **The replay guarantee covers structured content only.**

**(c) Identity is stored `clear` in both files. Pseudonymisation happens at export, not at
storage.** Two reasons, and both are practical rather than principled:

1. A private DM archive full of `P-7f3a` labels is **useless to its owner**, which is the one
   person it is for.
2. Pseudonymising the author column while storing full message text is **theatre** — names
   appear in the text.

The controls that actually work are scope, encryption, retention, export gating and purge.
`authors.actor_hmac` is still always present as the join key, so `purge --person` stays a
one-liner.

**There is no per-target `identity_mode` column.** An earlier draft kept one, defaulting to
`clear`, "available for a conversation the user wants extra-hardened". Nothing anywhere read
it: `v_items_masked` keys its masking off `containers.privacy = 'conversation' AND
items.is_from_self = 0`, and export masking is gated on `--include-names`. A governance
column with a plausible name and no reader is worse than no column, because it reads like a
control that is running.

### 2.3 One knob the two-file split killed

Earlier drafts carried `index_fts` as a per-tier switch, because a single shared FTS index
would let a global search surface DMs. **That knob no longer exists.** Each file holds exactly
one privacy half and carries **identical DDL**, so each has its own `items_fts` with
**unconditional** triggers.

Three things fall out at once:

- Conversations are **searchable**, in their own file. The old design made them unsearchable by
  construction, which was a real loss.
- A global search is **structurally incapable** of surfacing a DM, because the index does not
  contain one.
- The FTS delete-trigger corruption trap becomes unrepresentable — see §11.

---

## 3. Privacy resolution: propose, raise, fail closed

| # | Rule | Enforcement |
|---|---|---|
| 1 | The connector **proposes** from platform metadata (peer type, group badge, member count) — never from a config default | `propose_privacy()` is pure; `test_privacy_from_metadata` |
| 2 | Config may only **raise** sensitivity: `broadcast → joined → conversation` | `PRIVACY_ORDER` comparison in core |
| 3 | **Lowering** requires `--force-tier` and stamps `privacy_source = 'forced'` **forever** | `containers.privacy_source CHECK (… IN ('connector','config','forced'))` |
| 4 | Anything unclassifiable resolves to **`conversation`** | `PRIVACY_ORDER[-1]`; `test_privacy_fails_closed` |
| 5 | A conversation container **must** record which of your accounts was a participant | a real `CHECK`, and it executes: `CHECK (privacy <> 'conversation' OR viewer_account_id IS NOT NULL)` — it reads only its own row, which is what makes it legal |
| 6 | A conversation target **cannot be enrolled** without an explicit human acknowledgement | **two `BEFORE` triggers**, `targets_ack_ins` and `targets_ack_upd`. A `CHECK` *cannot* express this — see [./DATA-MODEL.md](./DATA-MODEL.md) §13.1 |

Rule 5 is *"by what right do I hold this?"* expressed as a `NOT NULL` constraint.

**Rules 5 and 6 sit next to each other and are enforced by different mechanisms, so it is
worth saying which is which.** Rule 5 is a `CHECK` and it works. Rule 6 **cannot** be a
`CHECK`: SQLite rejects subqueries in `CHECK` constraints outright (`Parse error: subqueries
prohibited in CHECK constraints`), and an earlier draft of this section specified exactly
that rejected statement. Two things follow, and the second is the dangerous one:

- The working mechanism is a pair of `BEFORE` triggers. **Both** are needed — an insert
  trigger alone leaves `UPDATE targets SET ack_third_party = 0` unguarded, which is the half
  that actually protects the invariant over time. The executed DDL for both is in
  [./DATA-MODEL.md](./DATA-MODEL.md) §3 under *Containers and targets*; copy it verbatim.
- **Do not repair the parse error by dropping the second clause.** `CHECK
  (ack_third_party = 1)` parses cleanly and makes every *broadcast* target unenrollable — a
  worse outcome than the original bug, reached by the most natural debugging move available.

**Group-size threshold.** `LARGE_GROUP_MEMBERS = 200` separates `joined` from `conversation`
for chat platforms — **and it only counts when the group also has a public join link.** No
public link → `conversation`, regardless of size. A private 5,000-member supergroup is a
conversation.

**`doctor` reports drift**: any container whose `kind` implies a conversation (`dm`, `chat`)
but whose `privacy` is not `conversation`, and any container with `privacy_source = 'forced'`.

---

## 4. Two files, identical DDL

The DDL applied to both files is in [./DATA-MODEL.md](./DATA-MODEL.md).

```
data/social.db    plain sqlite3    broadcast + joined
data/private.db   sqlcipher3       conversation
```

| Property | Consequence |
|---|---|
| **Identical DDL** applied to both | each file is self-contained; each has its own `items`, `items_fts`, `runs`, `gaps` |
| **No foreign key crosses the boundary** | SQLite cannot enforce one under `ATTACH` anyway; cross-boundary edges degrade to unresolved `*_ref` text |
| **One envelope → one target → one container → one file** | frozen invariant; makes routing trivial and total |
| **A cross-container reference** (a Telegram forward from another channel) | produces an unresolved `item_relations.to_ref` edge only — never a write into another container |
| **`rm data/private.db`** | a complete, consistent operation |
| Registry tables (`sources`, `accounts`, `metrics`) | mirrored to both by the migration runner, written only by CLI commands, never by a connector |
| `crawler status` | reads both |
| `crawler search` | opens `social.db` only. `crawler search --private` opens `private.db` only. **Never both.** `bm25()` scores are not comparable across indexes anyway |

**One tick can write to both files.** A single Telegram run writes a channel post to
`social.db` and a DM to `private.db` — see
[./sources/telegram.md](./sources/telegram.md) §9. That is the case that decided the
architecture.

---

## 5. Encryption at rest

### 5.1 The threat model, which decides everything

| # | Threat | FileVault | POSIX perms | SQLCipher |
|---|---|---|---|---|
| A | Laptop stolen while **powered off** | **solved** | – | solved |
| B | Laptop open and unlocked, someone sits down | no | no | yes, if the key is not loaded |
| C | Malicious process running **as your own uid** (an npm/pip postinstall, a browser extension, an agent with filesystem access) | no | **no — same uid** | only while the key is not loaded |
| D | **Backup / sync exfiltration** — Time Machine, iCloud Drive, Dropbox, a stray `git add -A` | **no** | no | **yes** |
| E | Legal compulsion, border search | no | no | no |

**D is the realistic threat** for a personal tool on a laptop that is powered on all day. A is
the one everyone designs for. **C is unsolvable at this layer, and pretending otherwise is
dishonest.**

### 5.2 Requirements

| # | Requirement | Enforcement |
|---|---|---|
| 1 | **FileVault is a checked prerequisite, not advice** | `crawler doctor` shells `fdesetup status`. `crawler targets add` **refuses** to enrol a `conversation` target when it is off |
| 2 | For `broadcast` and `joined`, FileVault **is** the whole answer | no further encryption; keeps `sqlite3 data/social.db ".schema"` and every ad-hoc query against it working |
| 3 | `private.db` is **SQLCipher**, key from the macOS Keychain | `sqlcipher3` **0.6.2** (2026-01-07). `PRAGMA key` is the **first** statement on the connection |
| 4 | `PRAGMA secure_delete = ON` **at creation** | persistent header flag; setting it later does **not** retroactively zero already-freed pages |
| 5 | `PRAGMA cipher_memory_security = ON` | |
| 6 | No DB path may resolve under a synced directory | `doctor` rejects `~/Library/Mobile Documents`, `~/Library/CloudStorage`, `~/Dropbox`, `~/Google Drive`, `~/OneDrive` |
| 7 | Key + pepper generated once by `crawler init-keys`, 32 bytes from `secrets.token_bytes` each | never in the DB, never in the repo, never in a plist |
| 8 | `doctor` verifies the pepper resolves and `private.db` opens **before** any conversation crawl | a wrong key fails at `SELECT count(*) FROM sqlite_master`, loudly |

### 5.3 Library choice, verified

| Package | Latest | Date | macOS arm64 wheels | Verdict |
|---|---|---|---|---|
| **`sqlcipher3`** (coleifer) | **0.6.2** | **2026-01-07** | **yes — `macosx_11_0_arm64` for cp310–cp314, 78 files, SQLCipher 4.x vendored** | **use this** |
| `sqlcipher3-binary` | 0.6.0 | — | **no — `manylinux2014_x86_64` only**, SQLCipher **3.x** | wrong platform |
| `pysqlcipher3` | 1.2.0 | **2023-01-29** | no — sdist only, SQLCipher 3.x | **dead** |
| `rotki-pysqlcipher3` | 2026.8.3 | 2026-08-27 | yes, but `requires_python >=3.14,<3.15` | vendor fork pinned to one CPython |

*(`sqlcipher3` 0.6.2, its date, its 78 files and the presence of `macosx_11_0_arm64` wheels
re-verified against the PyPI JSON API on 2026-09-01.)*

**The usual objection is stale.** SQLCipher on macOS in 2026 is a `uv add sqlcipher3` and
`import sqlcipher3.dbapi2 as sqlite3` — self-contained wheels, no Homebrew `libsqlcipher`, no
compiler on the user's machine. It is DB-API 2.0 compatible, derived from `pysqlite3`.

**Why SQLCipher rather than encrypting just the message body column:** SQLCipher encrypts
**pages** — that includes the WAL, the freelist, the indexes and the FTS shadow tables.
Encrypting only `items.text` leaves who-talked-to-whom, when, how often and message lengths in
the clear, and for a conversation corpus **that metadata is most of the sensitive signal**.
Application-layer AEAD also needs a `cryptography` dependency, which is the same dependency
cost for a strictly worse result.

**Why not encrypt `social.db` too:** it would tax the ~90% of rows that are public broadcast
content, break `.schema` and every ad-hoc CLI query, and complicate the daily `.backup` — in
exchange for protection against a threat FileVault already covers. **Trigger to revisit:**
`social.db` needs to leave the machine.

**Why not an encrypted APFS sparsebundle as primary:** it is an *operational* control
("remember to unmount"), and a daily LaunchAgent will leave it mounted forever, reducing it to
FileVault. Keep it as the documented option for the **backup copy**:
`hdiutil create -encryption AES-256 -stdinpass -type SPARSEBUNDLE -fs APFS -size 20g backup.sparsebundle`.

### 5.4 The limits, documented verbatim rather than implied

These three sentences belong in the user-facing README, not only here. A design that implies
otherwise creates a **false sense of security that is worse than no encryption**, because it
changes behaviour.

> - **While a conversation crawl runs, the key is in process memory and the database is
>   decrypted for that connection.** SQLCipher covers backup and sync exfiltration. It does
>   **not** cover malware running as your own uid at that moment.
> - **A Keychain item the LaunchAgent can read unattended is a Keychain item any process
>   running as you can also request.** Whether `security -T <binary>` ACLs meaningfully
>   constrain a `uv run python` invocation is **unverified** — test it, and if it does not
>   hold, say so rather than implying protection you do not have.
> - **SQLCipher makes `private.db` opaque to `sqlite3`, DB Browser and `.dump`.** That is the
>   cost, and it is why only conversations pay it.

---

## 6. Retention

**Retention is a sweep, not a daemon.** `retention_sweep(conn, now)` runs at the end of every
`crawl` and at the start of every `export`. A pure function of the clock — frozen-clock
testable, no background thread to debug — and it prints a one-line summary in the run report so
it is never invisible.

| What expires | Class | Age | Mechanism |
|---|---|---|---|
| Parsed rows | `broadcast` | never | — |
| Parsed rows | `joined` | 730 d | hard delete |
| Parsed rows | `conversation` | **no default — set explicitly at enrolment** | hard delete, once a number exists |
| Envelope bodies | `broadcast` | never | — |
| Envelope bodies | `joined` | 90 d | `envelopes.purge_after`, partial index `idx_env_purge` |
| Envelope bodies | `conversation` | 90 d | same |

### 6.1 Conversation retention has no default, and that is the correction

An earlier draft defaulted `conversation.retention_days` to **180 days with an unattended hard
delete**, swept twice a day at 08:05 and 20:35 with no dry run. Read plainly, that default
**quietly destroys the DM archive the tool exists to build**: a Telegram or X-archive DM corpus
would keep roughly six months and then start deleting its own oldest half, forever, on a
schedule, silently. It is the control most likely to be discovered by *finding data gone*.

It also failed this document's own test. A default that destroys the user's primary goal gets
switched off in anger on the day it is noticed — which leaves the entire class ungoverned,
which is a worse outcome than a number the user chose.

So, three changes, and all three are enforced rather than advised:

1. **`conversation.retention_days` is `null` and `retention_days_must_be_explicit: true`.**
   Enrolling your first conversation target makes you type a number.
   `crawler targets add` refuses without `--retention-days`. **`forever` is a legal answer**,
   and so is `365`. The argument for forcing an explicit number here is strictly stronger than
   the one that already forces it for Reddit.
2. **The first destructive sweep requires `retention.confirmed: true` in config.** Before that
   flag is set the sweep runs, prints exactly what it *would* delete, and deletes nothing —
   the same dry-run-first posture `purge` already has. It made no sense that the *less*
   destructive verb was the one defaulting to `--dry-run`.
3. **The numbers are surfaced where a user will actually meet them** — rather than only
   here, in the document nobody reads first.

**Forward-only enrolment and retention are different controls and you need both answers.**
Forward-only (§9.1) bounds **what enters**. Retention bounds **what stays**. "I'll decide
backfill later" is genuinely fine, because later is not lossy for anything after enrolment.
"I'll decide retention later" is not, because the sweep runs unattended twice a day.

`targets.retention_days` overrides the class default per target; `NULL` inherits — except for
`conversation`, where there is nothing to inherit and enrolment is refused.

---

## 7. Purge, and telling the truth about it

### 7.1 The command surface

Flags and exit codes are defined by the CLI itself; this is what the verbs *do*.

```
crawler purge                                   # --dry-run is the DEFAULT; prints a plan
crawler purge --apply
crawler purge --target tg-vn-devs --all         # one target: items + versions + media + envelopes
crawler purge --privacy conversation --older-than 90d
crawler purge --person P-7f3a                   # the redact-a-person operation
crawler purge --before 2025-01-01
crawler forget <target>                         # unenrol + delete + block re-ingest
crawler vacuum                                  # secure_delete sweep + VACUUM + wal_checkpoint
```

`--dry-run` is the default. **Every destructive operation prints a plan first — including
the unattended retention sweep**, which deletes nothing until `retention.confirmed: true`
is set (§6.1).

### 7.2 Redact a person, as one transaction

`authors` joins on **`actor_hmac`**, not on the clear id, precisely so this is one `UPDATE`
that breaks no foreign key and needs no cascade rewrite:

```sql
BEGIN IMMEDIATE;

UPDATE authors
   SET platform_uid = NULL, handle = NULL, display_name = NULL,
       profile_url = NULL, extra = NULL, redacted_at = :now
 WHERE actor_hmac = :hmac;

UPDATE items SET text = NULL, title = NULL, visibility = 'redacted_locally'
 WHERE author_id IN (SELECT id FROM authors WHERE actor_hmac = :hmac);

DELETE FROM item_versions WHERE item_id IN
       (SELECT id FROM items WHERE author_id IN
          (SELECT id FROM authors WHERE actor_hmac = :hmac));

DELETE FROM media WHERE item_id IN
       (SELECT id FROM items WHERE author_id IN
          (SELECT id FROM authors WHERE actor_hmac = :hmac));
-- and unlink the content-addressed bytes under media/ab/cd/<sha256>

DELETE FROM envelopes WHERE seq IN
       (SELECT envelope_seq FROM item_envelopes WHERE item_id IN
          (SELECT id FROM items WHERE author_id IN
             (SELECT id FROM authors WHERE actor_hmac = :hmac)));

INSERT INTO redactions(scope, actor_hmac, block_reingest, reason, requested_at, applied_at,
                       items_hit, envelopes_hit, media_hit)
  VALUES ('person', :hmac, 1, :reason, :now, :now, :i, :e, :m);

COMMIT;
```

Then `VACUUM`, then `PRAGMA wal_checkpoint(TRUNCATE)`.

### 7.3 The single most important line in this document

**`redactions.block_reingest = 1` is not bookkeeping.** Without it, tomorrow's 08:05 run
re-fetches the same conversation and **silently re-creates every row you just deleted**. A
redaction that undoes itself is *worse* than none, because you believe it worked.

Requirements:

1. Every ingest path consults `redactions` **before insert** — indexed by
   `idx_redactions_hmac` / `idx_redactions_cont`, both partial on `block_reingest = 1`.
2. The upsert guards `visibility = 'redacted_locally'` so a **reparse** cannot resurrect purged
   content:
   ```sql
   visibility = CASE WHEN items.visibility='redacted_locally'
                     THEN items.visibility ELSE 'visible' END
   ```
3. **Tested directly:** redact a person, run a fixture crawl that still contains them, assert
   **zero** rows re-created.

### 7.4 The envelope residue, stated rather than hidden

A conversation envelope is a slice containing **many people's messages**. Purging one person
from it is not possible without rewriting the blob. So:

| Scope | Envelope treatment |
|---|---|
| `--person`, `--container`, `--target` | containing envelopes are **deleted whole** — which loses unrelated content in them. **Documented behaviour, not a surprise.** |
| A single upstream delete (`follow`) | the item row, its versions and its media go immediately; the raw residue expires with the conversation's 30-day `envelopes.purge_after` |
| `--item <id> --hard` | deletes the containing envelopes outright. Same loss, explicitly requested |

This is the correct trade and it must be printed, not implied.

### 7.5 Purge tells the truth about SQLite and APFS

Three facts that turn a purge into a false sense of security if you skip them:

1. **`DELETE` does not erase bytes.** Freed pages go on the freelist with content intact,
   trivially recoverable with `strings`. → `PRAGMA secure_delete = ON` **at creation** (§5.2,
   requirement 4).
2. **`VACUUM` rebuilds the file and drops the freelist** — run it after any conversation purge.
   It does **not** shrink the `-wal`. → follow with `PRAGMA wal_checkpoint(TRUNCATE)`.
3. **Neither touches APFS local snapshots or Time Machine.** The pre-purge file is still on
   disk.

So the tool prints this, rather than a clean success line:

```
purged 4,102 items, 118 envelopes, 31 media (conversation, >365d)
vacuumed: private.db 512MB -> 361MB ; wal truncated
note: APFS local snapshots may still contain the pre-purge file.
      `tmutil listlocalsnapshots /` to inspect. Not doing this for you.
```

It does **not** run `tmutil deletelocalsnapshots` itself. That is a system-wide destructive act
and it is the user's call.

### 7.6 `envelopes.seq` is `AUTOINCREMENT`, and that is load-bearing here

Retention pruning, `purge_after` and per-subject redaction all **delete** envelope rows. Plain
rowid reuse would silently break every `WHERE seq > :last_processed` reparse and backfill scan.
One word in the DDL, a real bug avoided.

---

## 8. Upstream deletes

`--respect-upstream-deletes=auto` is the default, resolving per class:

| Class | Default | Why |
|---|---|---|
| `broadcast` | **`tombstone`** — record `deleted_upstream_at`, keep the content | For an archive of a public page, the **fact** of a deletion is often the datum. It was published to the world |
| `broadcast`, **Reddit only** | **`follow`, forced, unoverridable** | Reddit's Data API terms require dropping deleted content **even when de-identified**. `delete_policy_locked = True`; a CLI override is refused with a pointer to why. See §12 |
| `joined` | `follow` | member-only content; a bounded-audience expectation extends to unsending |
| `conversation` | `follow` | an unsend is a **request**, expressed in the only vocabulary the platform gives the other person |

### 8.1 Detection is the hard half, and getting it wrong is catastrophic

Absence is not deletion. It can be a pagination hiccup, a rate limit, a temporarily hidden
post, a reversible moderation action, a shadowban, or losing access to the container.

| # | Requirement | Why |
|---|---|---|
| 1 | **Re-scan window.** Every run re-scans `rescan_window_days` regardless of the watermark (3 broadcast / 7 joined+conversation) | An incremental cursor never revisits old items, so it can **never** detect a delete. This is the actual recurring cost of the feature — budget the requests |
| 2 | **Strikes, not one shot.** `absence_strikes` = 3 broadcast / 2 joined+conversation, tracked in `items.absence_streak` | one hiccup must not tombstone anything |
| 3 | Absence only counts **inside a positively-covered range** | derived from the envelope's `Coverage` claim; an `opaque` claim contributes no absence evidence at all |
| 4 | The sweep is **skipped entirely** unless `containers.access_state = 'ok'` | otherwise leaving a Facebook group marks 4,000 posts deleted |
| 5 | The sweep never runs when `runs.status <> 'ok'` | one Facebook checkpoint would tombstone the archive |
| 6 | `follow` **cascades completely** | `items` → `item_versions` → `media` (rows **and** the content-addressed bytes) → `envelopes` per §7.4. A delete that leaves the original payload in `envelopes` has deleted nothing |

```sql
UPDATE items SET visibility='deleted_upstream', deleted_upstream_at=:now
 WHERE container_id=:c AND absence_streak >= :strikes AND visibility='visible'
   AND (SELECT access_state FROM containers WHERE id=:c) = 'ok';
```

### 8.2 Why the sweep is mandatory even for Telegram

`Cap.DELETE_EVENTS` is **deliberately not set** for Telegram. `MessageDeleted` is documented as
unreliable, and `UpdatesTooLong` / `ChannelDifferenceTooLong` explicitly mean *"I will not
enumerate what you missed."* Under any design that gates the sweep on that flag, **DM deletions
would silently never be detected** — which would make this tool a system that specifically
defeats other people's deletions. That is the worst possible default for a personal DM archive.
See [./sources/telegram.md](./sources/telegram.md) §6.

### 8.3 Facebook upstream deletes are not detectable, and the re-scan window is 0

Requirement 3 above is the whole argument: **absence only counts inside a positively-covered
range, and an `opaque` coverage claim contributes no absence evidence at all.** Facebook
claims `opaque` on every envelope it will ever emit, because a virtualised feed cannot
honestly claim an interval (see [./sources/facebook.md](./sources/facebook.md) §1).

Therefore **a Facebook feed re-scan can never accumulate an `absence_streak`.** Not rarely —
never, by construction. An earlier draft nonetheless gave Facebook a 3-day broadcast / 7-day
joined re-scan window and a `rescan_once_per_day` override, which spent the scarcest and most
dangerous budget in the entire project — 200 posts, 25 minutes, no feedback signal, account
risk as the failure currency — re-scrolling old posts for a signal the design discards on
arrival.

**Decision: `rescan_window_days = 0` for Facebook, and `rescan_once_per_day` is deleted.**
Facebook upstream deletes are simply not detected. That is a permanent property of an opaque
transport, not a gap to close, and it is stated in
[./sources/facebook.md](./sources/facebook.md) §9 rather than implied. [./sources/x.md](./sources/x.md) §3.8 reaches the same conclusion for the same class
of reason and says so out loud; this now matches.

**The absence sweep therefore arrives with Telegram**, which is the first connector whose
coverage claims are `exact` and the first where the sweep can accumulate evidence at all.

If per-post Facebook deletion detection is ever wanted, it is a **different feature**: a
bounded re-navigation to individual permalinks, where "this content isn't available"
genuinely is absence evidence, with its own item budget. Spec it separately; do not conflate
it with a feed re-scan, because the two cost different things and only one of them works.

The general tension it was trying to name is still real and still worth remembering: a
re-scan window is a recurring request cost, and on Facebook request budget is the thing that
gets accounts flagged. On every other connector the re-scan is cheap and the evidence is
real, so the trade only ever bit here.

---

## 9. Third-party data: the controls that actually work

Pages and subreddits are broadcast. DMs and private group chats contain **people who never
agreed to be in anyone's database.** Keeping that proportionate is mostly about *what gets in*,
not about what gets encrypted afterwards.

### 9.1 Forward-only enrolment is the highest-leverage control in the design

| # | Requirement |
|---|---|
| 1 | Enrolling a conversation target sets `targets.enrolled_at = now` and ingests **only after it** |
| 2 | Backfill is **explicit and per conversation**: `crawler targets add tg:@x --ack-third-party --backfill 90d` |
| 3 | The un-backfilled span below the floor is a `gaps` row with `reason='enrolled_floor'` — a **declared** state, not silence |
| 4 | **There is no bulk enrolment command.** No `--all-dms`, no `--all-dialogs`, no `--all-chats`, no `sync-everything`. **The absence is the design** |
| 5 | Dialog enumeration is confined to an interactive `<source> list-chats` that only **prints** candidates for the human to paste |

It costs nothing to implement and it turns corpus size into a **series of deliberate
decisions** rather than one flag someone types once. Every other control in this document is
damage limitation for data already in the database; this one keeps it out.

### 9.2 The rest of the third-party defaults

| # | Requirement | Rationale |
|---|---|---|
| 6 | **No member rosters.** `store_rosters: false`, not configurable | A 500-member group's participant list is a contact graph you did not need and cannot justify. Only people who authored a message you kept get an `authors` row |
| 7 | **Reactions are counts, not identities**, at `joined` and `conversation` | per-person reactor lists are a social graph with ~zero analytical value in a personal archive |
| 8 | **Forwarded messages** carry an original author who was never in your conversation — same pseudonymisation on export; a channel forward keeps the **channel** name (a public entity), not a personal forwarder | |
| 9 | **Service messages store the action *kind*, not its participants** | "X added Y to the group" names a third party who may never have written a message. They must still be *persisted* — see [./sources/telegram.md](./sources/telegram.md) §10.4 for why dropping them breaks the absence sweep |
| 10 | **Your own messages are exempt.** `is_from_self = 1` rows keep clear identity, are exempt from conversation retention expiry, and are **never masked** on export | Your data is yours. The classes exist to protect the other participant |
| 11 | `crawler targets --sensitive` lists every conversation target with row count, date range and last crawl | "What am I actually holding about other people?" must be **one command**, not a SQL exercise |
| 12 | Print that list **unprompted** in the daily run report when a conversation target grew by more than `REVIEW_THRESHOLD = 5000` rows since the last report | |
| 13 | `crawler forget <target>` is a **distinct verb** from `purge`: unenrol, delete all rows, and write a `redactions` row with `block_reingest = 1` | so a stale entry in the config cannot resurrect it |

### 9.3 Cross-platform identity linking is off by construction

**There is no cross-platform identity linking in the schema at v1**, and adding it is a
schema migration *and* an ADR. That pairing is the review checkpoint.

An earlier draft bought the checkpoint with two empty tables — `persons` and
`author_person_links`, carrying `CHECK (linked_by IN ('manual','self'))` with deliberately no
`'inferred'` value, so automated matching could not be added without a migration. The
reasoning was right and the mechanism was expensive: two tables, a composite foreign key and
a `CHECK`, with **zero declared writers**, to buy a tripwire that a written rule buys for
free. The rule now lives in [./DECISIONS.md](./DECISIONS.md) ADR-0023, which also carries
the deferral and its trigger.

The underlying reason is correctness, not caution: Vietnamese given-name distributions are
concentrated enough that name-based cross-platform matching is near a coin flip, and a wrong
link silently poisons every query that reads it with no way to tell which rows are affected.
`authors.actor_hmac` remains the per-source join key and is enough for everything the tool
actually does, including `purge --person`.

### 9.4 Pseudonymisation is pseudonymisation, and the docs must say so

`actor_hmac = HMAC-SHA256(source ‖ platform_uid, pepper)`, pepper = 32 random bytes in the
Keychain, never in the DB.

**This is pseudonymisation, not anonymisation.** Platform user ids are a **low-entropy
enumerable space**; anyone holding both the database and the pepper can rebuild the mapping by
brute force. What it actually buys: it stops casual reading, it stops accidental leakage
through an export, and it makes bulk redaction a one-liner. It is not a defence against a
determined adversary. Do not let a developer think it is anonymity.

**Losing the pepper** makes every pseudonymous join key permanently unrecoverable. Document
that as a feature with a sharp edge, and have `doctor` verify the pepper resolves before any
conversation crawl.

---

## 10. There is no `phone` column, and that is structural

`scrub()` runs **before persistence**, and `assert_clean()` re-checks inside `db.upsert_*` on
every non-broadcast write.

```python
DROP_KEYS = frozenset({
    "phone", "phone_number", "phoneNumber", "contact", "contacts", "vcard",
    "email", "emails",
    "latitude", "longitude", "geo", "gps", "venue", "location",
    "access_hash", "auth_key", "session", "file_reference",
    "online", "status", "last_seen_status", "read_outbox_max_id",
})

# Vietnamese mobile (+84 / 0 followed by 3,5,7,8,9 and 8 digits) plus generic E.164
PHONE_RE = re.compile(r"(?:\+?84|0)(?:3|5|7|8|9)\d{8}\b|\+\d{7,15}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
```

| Key | Why it is dropped for **every** class |
|---|---|
| `access_hash` | **The non-obvious one.** Telegram's `access_hash` is an *account-scoped capability token*: holding it lets your session resolve that stranger later from a peer id alone. Persisting it means storing **the ability to look strangers up**, not merely a record that they spoke |
| `file_reference` | short-lived ticket, useless stored, and it is a capability |
| `latitude` / `longitude` / `venue` | location is **sensitive**-category data under Vietnam's PDPL (§13). A shared pin in a group chat is a sensitive-category record |
| `online` / `status` / read receipts / typing | behavioural surveillance data with **zero** archival value in a personal corpus |
| `phone` / `email` / `vcard` | see below |

**The structural half.** There is **nowhere in the frozen schema to put a phone number**. That
is not a policy note — it is why no connector can accidentally persist one. `assert_clean()`
raises `PIILeak` if one appears in a payload anyway. One regex pass per write; at a few hundred
messages a day it is free, and it converts "the connector should scrub" from a code-review
convention into a **runtime invariant**.

---

## 11. Two audit traps to know about

**Never audit the privacy split with `count(*)` on an FTS table.** External-content FTS5 reads
the **content table's** row count, so a `count(*)` audit will **falsely pass**. Audit with
`MATCH` assertions.

**The FTS delete-trigger corruption trap, which the two-file split makes unrepresentable.** If
an FTS insert trigger is *conditional* while its delete trigger is *unconditional*, the index
receives `'delete'` for rowids it never held. Searches stay correct, `integrity-check` returns
**OK**, `PRAGMA integrity_check` returns **ok** — and then an unrelated write **days later**
fails with `database disk image is malformed`, and every subsequent write to `items` fails.

Because each file holds exactly one privacy half, both triggers are unconditional and the
mismatch cannot exist. Keep it that way: **the FTS delete predicate must be
character-for-character the FTS insert predicate.** `doctor --repair` offers
`INSERT INTO items_fts(items_fts) VALUES('rebuild');`.

---

## 12. Export guardrails

```
$ crawler export --format jsonl --out ~/Desktop/dump.jsonl
exported 12,481 rows from 6 broadcast targets
withheld: 3 joined targets          (2,109 rows)
          4 conversation targets   (18,340 rows)
  add --include-tier joined, or --include-private, to include them
wrote ~/Desktop/dump.jsonl and ~/Desktop/dump.jsonl.manifest.json
```

Eight guardrails, ordered by how much accidental spillage each actually prevents:

| # | Guardrail | Why |
|---|---|---|
| 1 | **Default-deny by class.** `broadcast` only. Not a warning — a filter | |
| 2 | **Always print what was withheld** | a silent filter teaches the user to distrust the tool and reach for raw SQL, which defeats every other control here |
| 3 | **`--include-private` is refused outright when stdin is not a TTY** — not prompted, **refused** | makes it **structurally impossible** for a scheduled job, a pipe or a LaunchAgent to ever emit conversation data. Interactively it requires typing the conversation count |
| 4 | **Third-party names are pseudonymised** (`P-7f3a`) unless `--include-names` is *also* passed. `is_from_self` rows are **never** masked | makes the legitimate common case — grep my own DM history for that link — work without a file full of other people's names |
| 5 | **Output path refusal.** Reject any path resolving under `~/Library/Mobile Documents`, `~/Library/CloudStorage`, `~/Dropbox`, `~/Google Drive`, `~/OneDrive`, or inside a git work tree where it is not gitignored | ~20 lines of code that catch the single most likely real-world leak |
| 6 | **Conversation *envelopes* are not exportable at all. No flag exists** | removing the option removes the accident |
| 7 | **`export` never `ATTACH`es `private.db`** unless guardrail 3 has already passed | an authorisation bug in the query builder cannot leak what was never attached |
| 8 | **Mandatory sidecar `.manifest.json`** | anything downstream — a hook, a script, you in six months — checks one small file instead of parsing a multi-GB dump |

`crawler export` reads `v_items_masked` by default; `--include-names` bypasses the masking.

**Manifest contents:**

```json
{"tool_version": "...", "exported_at": 1756...,
 "classes_included": ["broadcast"], "targets": ["fb:vnexpress", "..."],
 "row_counts": {"broadcast": 12481}, "date_range": ["2025-06-01","2026-09-01"],
 "names_included": false, "sensitivity": "public",
 "constraints": ["telegram: Telegram API terms prohibit using or aggregating this
   data to train, fine-tune or benchmark AI/ML models",
   "reddit: content deleted upstream must not be retained"]}
```

The `constraints` array is how a platform obligation **travels with the data** instead of
living only in a document nobody re-reads. See §13.

**Repo hygiene, same commit:** `.gitignore` gains `data/`, `exports/`, `profiles/`, `*.db`,
`*.db-wal`, `*.db-shm`, `*.session*`, `*.sparsebundle`. Ship a documented optional
`pre-commit` hook that refuses any commit touching `*.db` or a file whose sibling
`.manifest.json` says `"sensitivity": "private"`.

---

## 13. Per-platform terms of service

Short, factual, confidence-labelled. None of this is legal advice.

**Facebook — prohibited by contract; the risk is your account.** *(likely — Meta's ToS §3.2 as
quoted second-hand; the primary page was not fetched for this document.)* Meta's terms prohibit
accessing or collecting data from their products by automated means without prior permission,
and separately prohibit attempting to access data you are not permitted to access. There is no
carve-out for content your own logged-in account can already see. This is a **contract** term,
not a criminal statute: the realistic consequence is account enforcement — checkpoint, feature
block, disablement — not prosecution. *hiQ v. LinkedIn* (9th Cir.) held that scraping genuinely
public data is not CFAA "unauthorized access", but that is US law, says nothing about breach of
contract, and does not reach content behind a login — which is exactly what a private group is.

**Reddit — the only officially clean path, with retention strings attached.** Official Data
API, official OAuth, published terms. Two things reshape this document. First, self-service app
registration is reported closed since November 2025, with new access under a **Responsible
Builder Policy** and manual review. *(unverified at first-party level — `support.reddithelp.com`
and `redditinc.com` both returned HTTP 403 to automated fetches, and `web.archive.org` was
blocked. Confirm in a browser.)* Second, and this is the part that forces code: Reddit requires
that content **deleted on Reddit stop being used**, treats retention of deleted content as a
violation **even if disassociated, de-identified or anonymized**, and reportedly recommends
deleting stored user data within 48 hours. *(unverified — secondary sources only; the primary
Data API Wiki was unreachable.)* **Design consequence, already implemented:**
`on_upstream_delete = 'follow'` is **forced and unoverridable** for Reddit
(`delete_policy_locked = True`), and `retention_days` for Reddit must be set **explicitly and
knowingly** rather than inheriting the broadcast "forever" default.

**X — a pricing question, not a legal one.** Automated access through the official API is
permitted; scraping is prohibited. On price: **the published figures are `[verified]`.** Two
independent recon passes fetched `docs.x.com/x-api/getting-started/pricing` on 2026-09-01 and
agree on all of it — `$0.005` per Post read, `$0.010` per User, `$0.010` per DM Event,
`$0.001` for owned reads, *"capped at 3 million Post reads per monthly billing cycle"*, and
*"deduplicated within a 24-hour UTC day window"*. An earlier version of this paragraph called
every figure third-party reporting; that was stale, and leaving it would have pushed a reader
to re-derive a correct number from a vendor blog, which is the exact failure
[./sources/x.md](./sources/x.md) §3.1 warns against. The operative instruction is unchanged
and is about a different thing: **`console.x.com` is the billing authority, docs lag, and the
cap is typed in dollars** — reconcile there before `enabled: true`. The governance-relevant
fact is
different: **X DMs come exclusively from the free data-archive ZIP, never from the DM Events
API.** It is free, complete (no ~3,200-post ceiling), zero ToS risk, and has **far better
provenance** — an export the user personally requested for their own account, versus an OAuth
scope that could be pointed anywhere. The trap to name loudly: **X reads like a broadcast
source right up until the DM folder lands in the same pipeline.** The archive importer routes
DM containers to `conversation` / `private.db` like any other, and the third-party defaults in
§9 apply to it in full.

**Telegram — officially supported, with one hard boundary.** Telegram publishes MTProto and
issues `api_id`/`api_hash` to individuals; logging in as your own user account is the intended
use. *(likely — the "retractable, limited, non-exclusive, non-transferable and
non-sublicensable" licence wording comes from search extracts of `core.telegram.org/api/terms`,
not a direct fetch.)* Two terms bear on this project: client apps must "guard their users'
privacy with utmost care"; and developers are **prohibited from scraping, indexing, harvesting,
aggregating or using platform data to train, fine-tune, validate, benchmark or deploy AI/ML
models**, with a narrow exception only where every user in the specific chat gives explicit,
informed, affirmative and continued consent scoped to that chat, non-transferably. *(verified
via search across `telegram.org/tos/bot-developers` and `core.telegram.org/api/terms`; body
pages not fetched.)* **If any downstream use of this corpus involves an LLM — summarisation,
embeddings, semantic search over chat history — that is an architectural boundary, not a
footnote.** It is stamped into every export manifest (§12) so the constraint travels with the
data. Residual risk is account limiting for aggressive collection from large public channels,
not for reading your own chats.

**Zalo — no legitimate path for a personal account.** Zalo's developer platform is built
entirely around business Official Accounts; there is no personal-account API. *(verified.)*
Every library that reads a personal Zalo chat is an unofficial reimplementation of the Zalo Web
protocol, and those projects' own READMEs disclaim responsibility for account bans, with
documented enforcement including a wiped friend list in March 2026. **Zalo's specific ToS
clause on automated access could not be located** *(unverified)* — so do not assert what
Zalo's ToS says; the claim that unofficial clients violate Zalo policy comes from the
libraries' own disclaimers and community consensus. Zalo is the one source that **actively
pushes data-subject-rights requests at you** via the `user_withdraw` webhook, which is why the
per-subject purge path in §7 is built generically for every source. See
[./sources/zalo.md](./sources/zalo.md).

---

## 14. Vietnam: Law 91/2025/QH15 — factual note

**Not legal advice.** Factual position as of 2026-09-01, for an individual in Vietnam keeping a
local personal archive.

**Correction to earlier drafts: Decree 13/2023/NĐ-CP is repealed.** *(verified across DLA
Piper, Rajah & Tann, Tilleke & Gibbins, Vietnam Briefing and Viet An Law.)*

| | |
|---|---|
| **Law No. 91/2025/QH15** on Personal Data Protection | passed 26 June 2025, **in force 1 January 2026**. Vietnam's first statute-level (not decree-level) data protection law |
| **Decree No. 356/2025/NĐ-CP** | promulgated 31 December 2025, effective 1 January 2026. The implementing decree; **formally replaces Decree 13/2023/NĐ-CP** |

**Any document in this set citing Decree 13/2023 is citing a repealed rule.**

| Point | Detail |
|---|---|
| Scope | Applies to Vietnamese agencies, organisations **and individuals**, and to foreign entities processing Vietnamese citizens' data. No record-count or revenue threshold |
| Household / purely-personal exemption | **unverified at article level.** One secondary summary suggests private or household use may be exempt "from some or all" requirements; DLA Piper's guide does not list one. **This design does not rely on it** — the tool stores third parties' messages either way |
| Sensitive categories | health and insurance, biometrics, **location**, financial/credit, political and religious views, employment records, children's data. **Phone number is *basic*, not sensitive** |
| Why that matters here | Not the phone number. It is that **private-chat content routinely contains sensitive-category data** — a shared location pin, a photo of a medical result, a bank transfer screenshot — even though the conversation carries no such label. You cannot classify content you have not read, which is the direct argument for conservative **container-level** defaults (§2) |
| Data subject rights | informed, consent/withdraw, access, correction, **deletion, restriction, objection**. `purge --person` (§7) is the technical answer; `redactions.block_reingest` is what makes the answer durable. Controllers must acknowledge requests within **two working days** *(likely)* |
| Penalties | *(likely — secondary sources)* administrative fines up to ~VND 3 billion; up to 5% of prior-year revenue for cross-border transfer violations; up to 10× the illicit gain for trading personal data. **Aimed at organisations.** Nothing found suggesting enforcement against a private individual's local archive |
| Cybersecurity Law 2018 + Decree 53/2022 | **not applicable.** Localisation and retention duties fall on *enterprises providing telecom, internet and value-added services in Vietnam* — service providers, not individuals keeping a local database |

**The line that actually matters.** All of the above stays theoretical while this is a personal
archive on the user's own machine. It stops being theoretical the moment the corpus is
**shared, published, sold, or used for a purpose other than personal**. The export guardrails
in §12 are the technical expression of exactly that line, which is why they are default-**deny**
rather than default-warn.

---

## 15. Invariants — write these as tests before the features

1. An unclassifiable target resolves to `conversation`. **Fail closed.**
2. `assert_clean()` raises on any `joined`/`conversation` write containing a `+84…` phone, an
   email, or a Telegram `access_hash`.
3. Redact a person, run a fixture crawl that still contains them, assert **zero** rows
   re-created.
4. `export` with no class flags emits **zero** rows from any `joined`/`conversation` target,
   and the manifest says so.
5. `--include-private` with a non-TTY stdin **exits non-zero and writes nothing**.
6. One Telegram fixture tick over a channel target and a DM target writes items **and
   envelopes** to `social.db` and `private.db` respectively. **Audit with `MATCH`, never
   `count(*)`** (§11).
7. `crawler targets add --privacy conversation` **refuses** when `fdesetup status` reports
   FileVault off.
8. A `--limit N` run writes no durable cursor (`runs.mode='limit'`).
9. The absence sweep is a **no-op** when `containers.access_state <> 'ok'` or
   `runs.status <> 'ok'`.
10. `on_upstream_delete` cannot be overridden for Reddit (`delete_policy_locked`).
11. **Both** `targets_ack_ins` **and** `targets_ack_upd` ABORT — the insert path *and*
    `UPDATE targets SET ack_third_party = 0` (§3 rule 6). Verified on 3.51.0.
12. **The retention sweep deletes zero rows while `retention.confirmed` is false**, and
    prints its plan (§6.1).
13. `crawler targets add` **refuses** a conversation target with no `--retention-days`.

---

## 16. Defaults summary — implement this directly

```yaml
governance:
  filevault_required_for: [conversation]
  large_group_members: 200          # and only counts with a public join link
  review_threshold_rows: 5000
  store_rosters: false              # not configurable
  first_crawl_since_days: 90        # broadcast + joined; conversation is enrolled_at
  # Keychain service names are RUNTIME IDENTIFIERS. One convention, reverse-DNS,
  # matching the LaunchAgent label.
  pepper_keychain_service:     vn.moonbase.crawler-social.pepper
  private_db_keychain_service: vn.moonbase.crawler-social.private-db

  retention:
    confirmed: false                # the first DESTRUCTIVE sweep requires true.
                                    # until then the sweep prints its plan and deletes
                                    # nothing. See 6.1.

  classes:
    broadcast:
      store: social.db              # plain sqlite3
      retention_days: null          # forever
      raw_mode: full
      raw_purge_after_days: null
      media_mode: link
      media_max_bytes: null
      media_mime_allow: []          # empty at v1: media_mode is `link` everywhere, so the
                                    # allowlist and media_max_bytes only take effect when a
                                    # named target opts into `download`
      on_upstream_delete: tombstone
      absence_strikes: 3
      rescan_window_days: 3
      exportable: true

    joined:
      store: social.db              # plain sqlite3
      retention_days: 730
      raw_mode: full
      raw_purge_after_days: 90
      media_mode: link
      on_upstream_delete: follow
      absence_strikes: 2
      rescan_window_days: 7
      exportable: false             # needs --include-tier joined

    conversation:
      store: private.db             # sqlcipher3 0.6.2, key from Keychain
      retention_days: null          # NO DEFAULT. See 6.1 -- must be set at enrolment.
      retention_days_must_be_explicit: true
      raw_mode: full                # envelopes go to private.db too
      raw_purge_after_days: 90      # was 30; see 2.2(a). Conversations are where parser
                                    # bugs are most likely, so they need a real replay window
      media_mode: link
      on_upstream_delete: follow
      absence_strikes: 2
      rescan_window_days: 7
      exportable: false             # needs --include-private + a TTY
      backfill: false               # forward-only from targets.enrolled_at
      requires_ack: true            # targets.ack_third_party = 1, TRIGGER-enforced (3.6)

  platform_overrides:
    reddit:
      on_upstream_delete: follow
      delete_policy_locked: true        # CLI override is refused
      retention_days_must_be_explicit: true
      # Note: conversation targets carry this flag too (see the class block above). The
      # argument for forcing an explicit number is stronger there than it is here.
    telegram:
      no_ml_training: true              # surfaced in the export manifest
      delete_events: false              # Cap.DELETE_EVENTS unset -> sweep is mandatory
    facebook:
      rescan_window_days: 0             # upstream deletes are NOT detectable here; an
                                        # `opaque` coverage claim yields no absence evidence
                                        # at all, so a re-scan spends the project's scarcest
                                        # budget for nothing. See 8.3.
    x:
      dm_source: archive_zip_only       # never the DM Events API
      rescan_window_days: 0             # same reason as facebook: nothing is ever
                                        # re-observed, so absence_streak never increments
      spend_cap_metric: cost_micros     # the ONLY value; usage_counters.metric is
                                        # CHECK-constrained to it
    zalo:
      experimental: true                # hand-edited enable flag required
      min_privacy: conversation
```

**Command surface this layer adds.** Spellings, flags and exit codes are defined by the CLI
itself — this list names the subset that exists *for* governance and does not define any of
it.

```
crawler init-keys                          # generate the private.db key + the pepper, once
crawler doctor                             # + fdesetup, pepper resolves, private.db opens,
                                           #   FTS5-in-SQLCipher answer, no synced DB path
crawler targets add <spec> --ack-third-party --retention-days N|forever
                          [--backfill 90d|all] [--force-tier X]
                          [--media download --mime image/jpeg --max-bytes N]
crawler targets list --sensitive           # what am I holding about other people
crawler purge [--dry-run|--apply] [--target|--privacy|--person|--before|--older-than]
crawler forget <target>                    # unenrol + delete + block re-ingest
crawler vacuum                             # secure_delete sweep, VACUUM, wal_checkpoint(TRUNCATE)
crawler export [--include-tier joined] [--include-private] [--include-names] --out PATH
```

`--ack-third-party` **and** `--retention-days` are both required for a conversation target;
the command refuses without either, and refuses again if `fdesetup status` reports FileVault
off.

---

## 17. Verify before building

The four rows below are the claims whose *governance* consequence needs stating next to the
policy they affect.

| Claim | Governance consequence if it goes the other way |
|---|---|
| FTS5 in the vendored SQLCipher build | **Not "nothing else changes."** Without FTS5 the statement list cannot be applied to `private.db` at all, which breaks the identical-DDL invariant. The fork is written out in [./DATA-MODEL.md](./DATA-MODEL.md) §14 item 1: same DDL minus the five FTS objects, the divergence recorded in `schema_versions` as a **declared state**, and `crawler search --private` degrading to a `LIKE` scan. Conversations stay searchable either way, which is the flaw ADR-0024 rejected |
| Keychain ACLs (`security -T <binary>`) restricting a `uv run python` caller | If they do not hold, the unattended-read convenience the LaunchAgent depends on is a **real weakening** and §5.4 must say so in those words. Do not imply protection you have not tested |
| Reddit's deletion obligation — the "48 hours" figure and the "even if disassociated, de-identified or anonymized" clause | Read it first-hand **before** fixing Reddit's retention default in code. Ship `on_upstream_delete='follow'` locked regardless: it is the conservative default and costs nothing if the wording turns out milder |
| Whether Vietnam's PDPL carries a purely-personal / household exemption | **The design does not rely on it**, and that is deliberate — the tool stores third parties' messages either way, so the class model, forward-only enrolment, the purge path and export gating are the right controls with or without the exemption |

**The X pricing row is closed.** `docs.x.com` was fetched on 2026-09-01 by two independent
recon passes that agree on every figure (§13). What remains is not verification but
reconciliation: **`console.x.com` is what bills you**, so confirm there before `enabled:
true`, and keep the cap typed in dollars.

---

*Companion documents: [../README.md](../README.md) (what this repo is and the reading order) ·
[./DATA-MODEL.md](./DATA-MODEL.md) (the frozen DDL — the only copy) ·
[./DECISIONS.md](./DECISIONS.md) (why each of these is frozen) · [./sources/](./sources/)
(per-connector evidence).*
