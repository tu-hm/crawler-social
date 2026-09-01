# Source: Telegram

**Transport:** MTProto user API (Telethon) · **Store:** both `social.db` and `private.db` ·
**Phase:** 2 (first connector after Facebook) · **Cost:** free

---

## 1. This is an official API, and that changes six things

`api_id` / `api_hash` are **self-issued** at my.telegram.org → "API development tools", against
your own phone-numbered account. No review, no approval queue, no business entity, no ticket.
Telegram publishes the protocol, the schema, the error table and the offset semantics. Logging
in as your own user account is the *intended* use of MTProto — third-party clients are the
reason the API exists.

Concretely, versus the Facebook connector:

| | Facebook (tolerated scrape) | Telegram (official API) |
|---|---|---|
| Content shape | obfuscated DOM (`x1y1aw1k`), rotates | typed TL objects, schema-versioned by layer |
| Timestamps | "3h ago" → `published_prec='relative'` | exact unix seconds → `published_prec='exact'` |
| Cursor | approximate content watermark + a behavioural stop rule | exact per-peer message id; resume needs zero overlap re-read |
| Rate feedback | none — you learn you were too fast by getting checkpointed | `FloodWaitError.seconds`, server-supplied and authoritative |
| Coverage claim | `opaque`, forever | `exact` over a message-id interval |
| Breakage mode | DOM rotation, silent field loss, `field_stats` canary | TL layer bump, loudly typed |

So Telegram gets `Cap.EXACT_CURSOR`, Facebook never will. Browser automation against
web.telegram.org would be strictly worse on every row of that table and is not considered.

**The two things being official does *not* buy you.** Neither is negotiable:

1. **The license is retractable.** Telegram's API terms grant access on a "retractable,
   limited, non-exclusive, non-transferable and non-sublicensable basis." *(likely — quoted
   from search extracts of core.telegram.org/api/terms; the page was not fetched. Verify.)*
2. **No ML training on Telegram data.** Telegram prohibits scraping, indexing, harvesting,
   aggregating or using platform data to train, fine-tune, validate, benchmark or deploy
   AI/ML models. There is a narrow exception where every user in the specific chat gives
   explicit, informed, affirmative, continued consent scoped to that chat, and that consent
   is non-transferable. *(verified via search of telegram.org/tos/bot-developers and
   core.telegram.org/api/terms; body pages not fetched — re-read before relying on the
   exception.)* If any downstream use of this corpus involves an LLM — summarisation,
   embeddings, semantic search over chat history — that is a hard architectural boundary,
   not a footnote. See [./docs/GOVERNANCE.md](../GOVERNANCE.md) §10; the constraint is
   stamped into every export manifest so it travels with the data.

---

## 2. Library choice: Telethon `==1.44.0`

Pinned exactly. Installed **from PyPI, never from a GitHub git URL**.

| Package | Latest | Released | Status |
|---|---|---|---|
| **`Telethon`** | **1.44.0** | **2026-06-15** | **use this** — maintenance mode, still tracking TL layers |
| `cryptg` | 0.6.0 | 2026-04-12 | **use this** — Rust AES-IGE, materially faster media/large slices |
| `Pyrogram` | 2.0.106 | 2023-04-30 | **dead** — repo archived 2024-12-23 |
| `Kurigram` | 2.2.25 | 2026-08-21 | live Pyrogram fork; legitimate second choice, different API |
| `pyrofork` | 2.3.69 | 2025-12-10 | maintained, further behind |
| `pyrotgfork` | 2.2.24 | 2026-05-16 | maintained, further behind |
| `TgCrypto` | 1.2.5 | 2022-11-11 | stale (Pyrogram-era) |

*(Telethon 1.44.0, cryptg 0.6.0 and the absence of any 2.x release re-verified against the
PyPI JSON API on 2026-09-01. The remaining rows are carried from recon at `verified`.)*

**Telethon relocated; it did not die.** `github.com/LonamiWebs/Telethon` was **archived
2026-02-21** and is read-only; its README says "Moved to
https://codeberg.org/Lonami/Telethon. The GitHub repository may be deleted in the future."
Development is on Codeberg, branch `v1`, with commits through late August 2026 and TL
**layer 229**. Do not let a stale search result convince you the library is abandoned, and do
not point a dependency, an issue link or a CI checkout at the GitHub mirror.

**Telethon v2 is not a candidate.** `docs.telethon.dev/en/v2/` renders as "Telethon 2.0.0a0"
with a breaking type-safe rewrite, but PyPI has **zero** 2.x releases of any kind. Installing
it means tracking a moving alpha branch from a git checkout. Revisit only when a 2.x lands on
PyPI.

**Maintenance-mode is the right risk profile here.** Telethon's own text: "Telethon v1 is for
the most part in maintenance mode. New layers are still updated to when released, bug fixes
are welcome, and some small additions may still be added from time to time." An ingestion tool
wants an API surface that does not move under it while still tracking the wire protocol.

**Containment.** Every Telethon symbol lives inside `connectors/telegram/`. No Telethon type
crosses the `Envelope` boundary. This is the same isolation argument PLAN.md §2 makes for
`stealth.py`, and it is what makes a Kurigram swap a one-directory change rather than a
rewrite. Vendor the wheel; a "may be deleted in the future" mirror is not a supply chain the
08:05 job should depend on.

**Python version.** Telethon declares `requires_python = ">=3.5"` and its classifiers stop at
3.8 — both are stale metadata, not a real ceiling; the repo carries a Python 3.14 CI commit
(2025-12-17). The project targets Python 3.13. **Smoke-test 1.44.0 on the actual uv-managed
interpreter before pinning** — see §16.

---

## 3. Bot API vs user API — decided on capability, not preference

| | Bot API (10.0) | MTProto user API (Telethon) |
|---|---|---|
| **History before the client existed** | **none.** `getUpdates`/webhooks deliver only events after the bot joined. There is no `getChatHistory`. | **full**, back to the first message, for every peer the account can see |
| Private 1:1 chats | **impossible** — a bot cannot be added to someone else's DM | yes |
| Groups | privacy mode **ON by default**: sees only commands, replies to itself, and `@mentions`. Disabling requires remove + re-add per group | sees everything the account sees |
| Public channels | must be an admin/member to receive posts | read by username |
| Auth | bot token from BotFather | `api_id`/`api_hash` self-issued + phone login |
| File download | ~20 MB via `getFile` (~2 GB with a self-hosted local Bot API server) *(likely, secondary sources)* | 2 GB free / 4 GB Premium *(likely, secondary sources)* |
| Identity | a bot, visibly | your own account |
| Fit for this project | **rejected** | **selected** |

Rejected on capability, not on taste: a tool whose purpose is *archiving what already
happened* cannot be built on a transport that structurally cannot see what already happened.

**The Business-connection side-door, named and rejected.** Bot API `business_message` /
`edited_business_message` / `deleted_business_messages` updates with `can_read_messages`
rights look like a way to reach private chats. They are real-time only — no history — and
grammY's documentation states the bot "is NOT able to forward messages from the chat, or copy
them elsewhere," which is precisely the operation this tool performs. It also requires a
Premium/Business subscription on the connecting account. *(likely — grammy.dev/advanced/business.)*
Not viable.

*(Bot API version 10.0 is sourced from python-telegram-bot 22.8's PyPI description, 2026-06-12,
not from core.telegram.org/bots/api-changelog. The exact number changes no decision here.)*

---

## 4. Auth, and protecting the credential that matters

Two credentials, two entirely different storage answers.

### 4.1 `api_id` / `api_hash` → macOS Keychain

```
service                              account
vn.moonbase.crawler-social.telegram  api_id
vn.moonbase.crawler-social.telegram  api_hash
```

```bash
security add-generic-password -U \
  -s vn.moonbase.crawler-social.telegram -a api_hash \
  -w '<hash>' -T /usr/bin/security
```

Resolved by `core.secret()` in the order **env `CRAWLER_TELEGRAM_API_HASH` → Keychain → loud
failure that prints the literal `security add-generic-password` command to run**. Never a
silent fallback to a file. These are per-application credentials and Telegram's terms forbid
sharing or reselling them.

### 4.2 The `.session` file — a bearer credential equal to password + 2FA

Telethon's default `SQLiteSession` writes `<name>.session`: a SQLite file holding the DC
address, the **MTProto authorization key**, and a cache of entity ids and access hashes.
Telethon's own docs, on the string-encoded equivalent: *"Keep this string safe! Anyone with
this string can use it to login into your account and do anything they want to."*

Rules, all of them load-bearing:

| Rule | Why |
|---|---|
| `~/Library/Application Support/crawler-social/telegram/<label>.session`, file `0600`, dir `0700` | not guessable, not group-readable |
| **Outside the git worktree** | so no `.gitignore` mistake can ever stage it. `*.session*` is gitignored anyway, as belt and braces |
| **Never in a synced directory** — `~/Library/Mobile Documents`, `~/Library/CloudStorage`, `~/Dropbox`, `~/Google Drive`, `~/OneDrive` | `doctor` refuses to start if the resolved path is under one |
| **Excluded from Time Machine** (`tmutil addexclusion`) | a backup of this file is a backup of full account access |
| **FileVault on** — checked by `doctor` via `fdesetup status` | see [./docs/GOVERNANCE.md](../GOVERNANCE.md) §5 |
| **`flock` before the client is constructed** (`Cap.SINGLE_FLIGHT`) | one process, always |
| **Never copied to a second machine.** A second machine does a second login and gets its own session | see below |

**`AUTH_KEY_DUPLICATED` (406) is permanent and is the most likely way this connector breaks.**
Telethon's `errors.csv`, verbatim: *"The authorization key (session file) was used under two
different IP addresses simultaneously, and can no longer be used. Use the same session
exclusively, or use different sessions."* The realistic causes are mundane: a copy on a VPS
while the laptop job still runs, an iCloud-synced Application Support directory, or an
overlapping tick. The `flock` handles the last one; the first two are handled by refusing to
put the file anywhere that syncs.

### 4.3 Login is interactive, once, and never from a job

`crawler login --source telegram` walks phone → code → password (raising
`SessionPasswordNeededError` when 2FA is on). This is the `Connector.login(io)` method in the
frozen contract, and **nothing in the scheduled lane may call it**. A scheduled run with no
valid session must fail loudly — exit 78 (`EXIT_CONFIG`) — and must never block on stdin. A
job blocked on a hidden password prompt is a job that silently stops collecting.

`SESSION_REVOKED` (the user terminated sessions from Telegram → Settings → Devices) is
`HUMAN`, exit 77, and stays disarmed until `crawler resume`. Never retried.

---

## 5. What is reachable, and the limit on each

| Target | Reachable | Limit / caveat | Proposed privacy |
|---|---|---|---|
| Public channel (`@name`) | full history, oldest to newest | *(unverified)* whether history reads without **joining** — see §16 | `broadcast` |
| Private/invite-only channel you are in | full history | membership required; loss of access → `access_state` | `joined` |
| Public supergroup with a username, >200 members | full history | conversation-shaped, broadcast-privacy — see §9 | `joined` |
| Private supergroup, any size, no public link | full history | no public link → `conversation` regardless of size | `conversation` |
| Basic group (`Chat`) | full history | ≤200 members by construction | `conversation` |
| Private chat / DM (`User`) | full history | both sides' messages | `conversation` |
| Forum topic (supergroup `forum=True`) | via `reply_to.forum_topic` / `top_msg_id` | modelled as `kind='forum_topic'`, `parent_id` → the supergroup | inherits parent |
| Channel comments | via the **linked discussion group** | `iter_messages(channel, reply_to=post_id)` works only in broadcast channels and their linked megagroups; a plain chat raises `PeerIdInvalidError` | that group's own class |
| Participant lists | **not collected** | `store_rosters: false`, not configurable — see [./docs/GOVERNANCE.md](../GOVERNANCE.md) §9 |  |
| Contacts, phone numbers, presence, read receipts | **never collected** | dropped by `scrub()` before persistence — §12 |  |

**A channel and its linked discussion group are two containers**, joined by
`containers.parent_id`. That is why the frozen schema has that column, and it is why a
channel-post-plus-comments view is a join rather than a cross-table special case. Note the two
containers may land in **different files**: a public channel is `broadcast` → `social.db`; its
discussion group is usually `joined` → also `social.db`, but a private one is `conversation`
→ `private.db`. Nothing crosses; edges degrade to unresolved `*_ref` text.

---

## 6. Capabilities

```python
caps = (Cap.NEEDS_SESSION      # bounded, supervised, expensive to open
        | Cap.SINGLE_FLIGHT    # the .session file; AUTH_KEY_DUPLICATED is permanent
        | Cap.NEEDS_HUMAN_LOGIN
        | Cap.BACKFILL         # full history, no ceiling
        | Cap.EXACT_CURSOR     # per-peer message id; resume needs no overlap re-read
        | Cap.MUTABLE_METRICS  # views/forwards/reactions drift forever
        | Cap.CONVERSATIONS)   # reaches third parties' private data
```

Deliberately **not** set:

| Flag | Why not |
|---|---|
| `Cap.DELETE_EVENTS` | `MessageDeleted` is documented as unreliable and `UpdatesTooLong` / `ChannelDifferenceTooLong` explicitly mean "I will not enumerate what you missed." Setting this flag would gate off the absence sweep, and DM deletions would then **never** be detected. This is a deliberate correction to an earlier draft that set the flag and contradicted its own body text. |
| `Cap.PUSH` | v1 is poll-only — §7 |
| `Cap.NEEDS_GUI` | no browser anywhere in this connector |
| `Cap.PARALLEL_TARGETS` | one session, one flock, strictly serial targets |
| `Cap.BACKFILL_CAPPED` | there is no ceiling; backfill is bounded by policy, not by the platform |
| `Cap.COMMENT_TREE` | replies are adjacency edges, not a tree endpoint. Channel comments are just the linked group's history |
| `Cap.BILLED` | free |

```python
def capabilities_note(self) -> str:
    return (
        "backfill: full history, no platform ceiling; bounded by --since and by "
        "targets.enrolled_at for conversations (forward-only by default).\n"
        "cursor: exact. Per-peer message id, monotonic. Resume re-reads nothing.\n"
        "deletions: NOT event-driven. MessageDeleted is documented unreliable; "
        "detection is the periodic ids-refresh sweep (500 ids broadcast / 2000 or "
        "30 days conversation).\n"
        "metrics: views, forwards, replies and per-emoji reactions all mutate "
        "forever and there is no 'changed since' query. The refresh window is a "
        "permanent cost, not a startup cost.\n"
        "media: metadata only by default. There is no durable URL for Telegram "
        "media; file_reference expires and access_hash is deliberately not stored.\n"
        "read-only: enforced by absence. No send/forward/join/read-mark method is "
        "imported anywhere in this connector."
    )
```

---

## 7. Poll, do not listen — and the recommendation is concrete

**Decision: v1 polls on the ordinary tick. No persistent listener, no `Receiver`, no spool.**

The tempting argument is that a long-lived `run_until_disconnected()` gives lower latency and
"catches deletions". Both halves are weaker than they look.

**The update stream is gap-tolerant by design.** Telethon's own update state machine
(`telethon/_updates/messagebox.py`) carries `NO_UPDATES_TIMEOUT = 15 * 60` and explicitly
handles `UpdatesTooLong`, `UpdateChannelTooLong`, `updates.DifferenceTooLong` and
`updates.ChannelDifferenceTooLong` — a family of responses that all mean *"I will not
enumerate what you missed; here is a new pts, go re-read history yourself."* So a listener
**never removes the id-cursor reconciler**. It only makes the reconciler find less.

**Deletion events are documented unreliable.** `events.MessageDeleted` — per Telethon's docs,
*"isn't 100% reliable, since Telegram doesn't always notify the clients that a message was
deleted"* — and it carries only `deleted_id`/`deleted_ids`, with the chat known only for
channel deletions. So deletion detection cannot be built on it either, which is exactly why
`Cap.DELETE_EVENTS` is unset (§6).

What a listener actually costs:

| Cost | Detail |
|---|---|
| An always-on process | a second supervised lifetime, a second failure class, a second thing that can be silently down |
| Real `AUTH_KEY_DUPLICATED` exposure | the listener holds the `.session`; one manual `crawler tick` against the same target destroys it **permanently** |
| WAL contention | a continuous writer against a bursty batch job |
| The reconciler anyway | it still runs, just on a timer inside the process |

What it buys: minutes of latency on a **nightly personal archive**. That is not a trade.

**Deferred, with a trigger.** Listen mode is a strict superset of the pull path — same
envelopes, same store, same reconciler — so switching later costs no migration. The
architecture already has the slot: a `Receiver` fsyncs signed bytes into `spool/telegram/` and
the ordinary scheduled `fetch()` drains that directory, so a listener never opens SQLite.
**Trigger:** the user says nightly latency is not good enough. **Hard rule if it ships:**
never run a listener and a pull run against the same `.session` file; that is the
`AUTH_KEY_DUPLICATED` gun, and it is loaded.

---

## 8. History iteration, cursors and coverage

### 8.1 The primitive

Verified signature, Telethon v1 `telethon/client/messages.py:347`:

```python
def iter_messages(self, entity, limit=None, *, offset_date=None,
                  offset_id=0, max_id=0, min_id=0, add_offset=0,
                  search=None, filter=None, from_user=None,
                  wait_time=None, ids=None, reverse=False,
                  reply_to=None, scheduled=False)
```

Semantics that matter, from the source and docstring:

- Default order is **newest → oldest**. `reverse=True` gives oldest → newest **and inverts**
  the meaning of `offset_id` / `offset_date`; under `reverse`, `min_id` becomes equivalent to
  `offset_id`.
- `offset_id` and `offset_date` are **exclusive**.
- **`_MAX_CHUNK_SIZE = 100`** (`messages.py:10`). Every 100 messages is one
  `messages.getHistory` round trip. `ids=[...]` fetches also page at 100 per request.
- `ids=[...]` takes precedence over everything else, and **missing messages come back as
  `None` in position**, so ids zip to results one-to-one.
- Setting `search`, `filter` or `from_user` switches Telethon from `messages.getHistory` to
  `messages.Search` — a different endpoint with a different flood profile. **The connector
  uses neither**; filtering happens in `parse()`, which is pure.
- Telethon emulates `min_id`/`max_id` client-side. Source comment: *"Telegram doesn't like
  min_id/max_id. If these IDs are low enough (starting from last_id - 100), the request will
  return nothing. We can emulate their behaviour locally by setting offset = max_id and simply
  stopping once we hit a message with ID <= min_id."* Consequence: **do not paginate with
  `min_id`** — it wastes requests walking backwards. Use `reverse=True` + `offset_id`.

### 8.2 The cursor

```json
{"kind": "watermark", "axis": "source_seq", "value": "{\"last_id\": 84213}", "axis_value": 84213}
```

`Cursor(kind=CURSOR_WATERMARK, axis=AXIS_SEQ, value=..., axis_value=last_id)`. It goes in
`cursors`, is monotonic, and is **durable**. Telegram never needs `CURSOR_PAGE` — there is no
opaque page token to persist and therefore no `expires_at` problem.

Forward walk: `iter_messages(entity, reverse=True, offset_id=last_id, wait_time=2.0)`.
Oldest-first from the watermark, so every committed envelope advances the watermark and a
crash leaves a monotonically advancing high-water mark rather than a hole. Combined with
core's rule that the envelope commits before the cursor, `kill -9` mid-run costs at most one
100-message slice and zero correctness.

**Cursors are rebuildable.** `crawler cursors --rebuild` re-derives the watermark from stored
envelopes (`SELECT target_id, cursor_json, max(seq) FROM envelopes WHERE cursor_json IS NOT
NULL GROUP BY target_id`). For Telegram this actually works cleanly, because the axis value is
a message id that also appears in `items.source_seq`.

**`--limit N` must not poison the cursor.** A sampled run records `runs.mode='limit'` and
writes no durable watermark. This is not Telegram-specific but it bites hardest here, because
the Telegram cursor is *exact* and a poisoned exact cursor silently skips real content forever.

### 8.3 Coverage claims

| Pass | `cover_kind` | axis | lo / hi | note |
|---|---|---|---|---|
| Forward history walk | `exact` | `source_seq` | first / last id in the slice | every message that exists in `[lo,hi]` is in these bytes |
| ids-refresh window | `exact` | `source_seq` | window bounds | `None` entries are absence evidence, not gaps |
| Backfill below `enrolled_at` (conversation, not backfilled) | — | — | — | core writes a `gaps` row, `reason='enrolled_floor'` |
| `UpdatesTooLong` (listen mode only, if ever) | `opaque` | — | — | `gaps` row, `reason='outage'` |

`exact` is honest here in a way it is not for Facebook: message ids are per-peer and dense
enough that "every id in this range that still exists came back" is a claim the transport
actually supports. **The claim only holds if service messages are persisted** — see §10.4.

**The SUSPECT rule bites here for a real reason.** Rule (a) — claimed `exact` over a non-empty
interval with `found == 0` — fires on Telegram's silently-empty history after the account is
removed from a group. Telegram does not error; it returns nothing. Without the rule, the
cursor advances past content that exists and the archive quietly stops growing.

---

## 9. Two shapes, one connector, one persistence path

This is why Telegram is connector two: it is the exact case that proves the container carries
privacy and shape, not the connector.

**Shape and privacy are orthogonal axes, and Telegram is the source that demonstrates it.**

| TL entity | `containers.kind` | `shape` | `propose_privacy()` | file |
|---|---|---|---|---|
| `Channel(broadcast=True)`, public username | `channel` | `feed` | `broadcast` | `social.db` |
| `Channel(broadcast=True)`, invite-only | `channel` | `feed` | `joined` | `social.db` |
| `Channel(megagroup=True)`, public username, >200 members | `group` | **`conversation`** | **`joined`** | `social.db` |
| `Channel(megagroup=True)`, no public link | `group` | `conversation` | `conversation` | `private.db` |
| `Chat` (basic group) | `chat` | `conversation` | `conversation` | `private.db` |
| `User` (DM) | `dm` | `conversation` | `conversation` | `private.db` |
| forum topic | `forum_topic` | `conversation` | inherits parent | follows parent |
| anything unclassifiable | — | `conversation` | **`conversation`** | `private.db` |

A large public supergroup is **conversation-shaped** (read in `source_seq` order, near-zero
child fan-out) but **joined-privacy** (goes to the plain file, is searchable in the global
index). If `shape` and `privacy` were one field that would be inexpressible.

```python
LARGE_GROUP_MEMBERS = 200

def propose_privacy(self, draft: TargetDraft) -> str:
    t = draft.params.get("peer_type")            # set by resolve(), from cached metadata
    if t == "broadcast_channel":
        return BROADCAST if draft.params.get("username") else JOINED
    if t == "megagroup":
        if draft.params.get("username") and \
           (draft.params.get("member_count") or 0) > LARGE_GROUP_MEMBERS:
            return JOINED
        return CONVERSATION                       # no public link -> conversation, any size
    if t in ("user", "basic_chat"):
        return CONVERSATION
    return CONVERSATION                           # fail closed
```

Pure. Derived from platform metadata, never from a config default. Core may only **raise** the
result; lowering requires `--force-tier` and stamps `privacy_source='forced'` forever.

**One tick, two files.** A Telegram tick over twelve targets writes eight `runs` rows into
`social.db` and four into `private.db`; `crawler status` reads both. The persistence path is
identical — one `items` writer, one `commit_envelope`, zero branching on shape or source. The
only thing that differs is which connection `open_store()` handed back, and that was resolved
before the first byte was fetched.

**Conversation *envelopes* go to `private.db` too.** A `tg.slice` from a DM contains the other
person's text in full. Routing parsed messages to the encrypted file while raw TL slices sit
in the plain one defeats the entire separation and is the specific mistake this section exists
to prevent.

---

## 10. Message anatomy → schema

### 10.1 The mapping

Verified against `telethon/tl/custom/message.py`. Column semantics are owned by
[../DATA-MODEL.md](../DATA-MODEL.md); this table is only the Telegram side of the mapping.

| TL field | Lands in | Notes |
|---|---|---|
| `id` | `items.source_seq` **and** `items.platform_item_id` (as text) | per-peer monotonic |
| `peer_id` | `items.container_id` (resolved) | one envelope → one target → one container |
| `from_id` / `post_author` | `AuthorDraft` → `items.author_id` | channel posts may have no `from_id` |
| `out` | `items.is_from_self` | never masked on export |
| `date` | `items.published_at`, `published_prec='exact'` | |
| `edit_date` | `items.edited_at` | set on edit only, **not** on metric drift |
| `message` | `items.text` | |
| `entities` | `item_versions.entities_json`, **raw** | §10.2 |
| `reply_to.reply_to_msg_id` | `items.parent_ref` — **always written** | resolved to `parent_id` by the repair pass |
| `reply_to.reply_to_top_id` | `items.root_ref` | "the message that started this thread" — forum topics and channel comment threads |
| `reply_to.forum_topic`, `top_msg_id` | `items.extra` | |
| `post` (bool) | `item_type` = `tg.channel_post` \| `tg.message` | provenance only; core never branches on it |
| `pinned` | `items.is_pinned` | |
| `fwd_from` | `item_relations(rel='forward_of', to_ref=...)` | never a write into another container |
| `grouped_id` | `item_relations(rel='album_member')` | an album is N rows reassembled at read time |
| `via_bot_id`, `noforwards` | `items.extra` | |
| `views` | `MetricDraft('views')` | |
| `forwards` | `MetricDraft('forwards')` | |
| `replies.replies` | `MetricDraft('comments')` | |
| `reactions` | `MetricDraft('reactions.<emoji>')` + `reactions.total` | §10.3 |
| `media` | `MediaDraft` | §11 |
| — | `items.title` | always `NULL`; Telegram messages have no title |

`published_prec` is `'exact'` on every Telegram row. That is worth a moment: it means a
cross-source timeline query can order Telegram against Facebook honestly, and a chart can
distinguish "3h ago, rounded" from "2026-08-31T14:22:07Z."

### 10.2 Entities are stored raw and never flattened

`MessageEntity.offset` and `.length` are measured in **UTF-16 code units** — not bytes, and
not Python string indices. *(verified — core.telegram.org/api/entities and the
python-telegram-bot reference. This is a deliberate correction to the phrase "UTF-16 byte
offsets" used elsewhere in the frozen design: a UTF-16 code unit is two bytes, so reading it
as a byte offset is off by a factor of two.)* Telegram also specifies that an entity's length
must **not** include trailing newlines or whitespace — entities are right-trimmed — while the
next entity's offset **does** include any preceding whitespace.

Flattening entities to Markdown at ingest is a lossy parse you cannot undo, which is exactly
what raw-first storage exists to prevent. Rules:

- The envelope keeps the entity list as delivered.
- `item_versions.entities_json` carries a JSON projection alongside the text of that version.
- Any Markdown/HTML rendering happens at **read time**, in the query layer, never at ingest.
- If you ever do convert, remember Vietnamese: `ế` is one code point but combining forms and
  emoji are surrogate pairs in UTF-16, so naive Python slicing on the offsets is wrong.

### 10.3 Edits and metrics are different signals with different destinations

| Signal | Destination | Written when |
|---|---|---|
| `message` or `entities` changed | `item_versions(item_id, content_hash, observed_at, text, entities_json)` | `content_hash = sha256(text ‖ canonical(entities))` differs from every existing row |
| `views` / `forwards` / `replies` / any reaction count changed | `metric_observations` | the value **moved**; an unchanged re-fetch writes nothing |
| latest values for the timeline query | `items.metrics_current` (JSONB) | same transaction |

Critically: **`edit_date` does not move when a view counter does.** So `content_hash` is
computed over text and entities only. Without this split, a Telegram edit, an edited Facebook
post and an X edit would all overwrite `text` in place and be recoverable only by hand from
raw envelopes.

**Per-emoji reactions are the reason `metric_observations` is a long table and not columns.**
A message can carry an arbitrary set of custom emoji reactions. There is no column design that
survives that, and the change-log rule (write only when the value moved) is what keeps the
table from becoming ~180M rows/year.

`MetricDraft.approximate` is `0` for Telegram — the counts are integers, not UI-rounded
`"1.2K"` strings, unlike Facebook. `source_kind` is always `'live'`.

### 10.4 Service messages must be persisted, or the absence sweep breaks

`MessageService` objects (someone joined, a message was pinned, the title changed) occupy real
ids in the per-peer sequence. If `parse()` drops them:

- `res.found` comes in below the count the coverage claim implies, and core's
  claimed-vs-found check starts flagging healthy runs as `partial`.
- The ids-refresh pass sees an id it has no row for and, on repeat, accumulates absence
  strikes toward a **false** `deleted_upstream` tombstone.

So: persist them as `item_type='tg.service'` with `text = NULL` and the action type in
`extra`. **Do not persist the action's payload** for conversation containers — "X added Y to
the group" names a third party who may never have written a message, and
[./docs/GOVERNANCE.md](../GOVERNANCE.md) §9 keeps rosters out. Store the action *kind*,
not its participants.

`MessageEmpty` is the opposite case: it is what a deleted id returns inside a range. Map it to
absence evidence, never to an item row.

---

## 11. Media: metadata only, and be honest about why

**Default `media_mode = 'link'` for every privacy class at v1**, per
[./docs/GOVERNANCE.md](../GOVERNANCE.md) §2. For Telegram, "link" needs an asterisk:

**There is no durable URL for Telegram media.** Downloading requires `id` + `access_hash` +
`file_reference`, and:

- **`file_reference` expires.** `FILE_REFERENCE_EXPIRED` is a documented error; the fix is
  re-fetching the message where the media appeared. *(verified that the error and the refresh
  procedure exist, per core.telegram.org/api/file-references; the actual validity duration is
  **not documented** and the design deliberately does not depend on a number.)*
- **`access_hash` is deliberately not stored.** It is an account-scoped **capability token** —
  holding it lets your session resolve that peer or file later from an id alone. Persisting it
  means storing *the ability to look strangers up*, not merely a record that they spoke. It is
  in `scrub()`'s `DROP_KEYS` for every privacy class, including `broadcast`. See §12.

So for Telegram, `media_mode='link'` stores **metadata only**: `kind`, `mime`, `byte_len`,
`width`/`height`, `duration_s`, `caption` — and `MediaDraft.url = None`.

`media.url_sha256` is `NOT NULL`, so it needs a synthetic key. Use the media's own **`id`**,
which is a stable identifier that is *not* a capability token (it cannot fetch anything
without the hash and reference):

```
url_sha256 = sha256("tg:document:" || id)      # or "tg:photo:" || id
```

Stable across re-fetches, so `UNIQUE (item_id, url_sha256)` dedupes correctly, and it leaks
nothing.

**`media_mode = 'download'`** is per-target opt-in behind a mime allowlist and `max_bytes`.
State the cost plainly before enabling it: a 200k-message channel is tens of MB of text and
potentially **hundreds of GB** with media, and every download is also a wire request against
the same flood budget. Telethon's `download_media(message, file=...)` handles the
`file_reference` refresh dance internally. Bytes go content-addressed to disk
(`media/ab/cd/<sha256>`), never as SQLite BLOBs, and are reachable from the redaction index so
a purge removes them too.

**The replay guarantee covers structured content only.** Say it in the README, not just here.

---

## 12. `scrub()` — what never reaches disk

Runs **before serialisation**, so the stored envelope is already clean. Dropped for every
privacy class:

| Key | Why |
|---|---|
| `access_hash` | account-scoped capability token — the ability to look people up |
| `file_reference` | short-lived ticket, useless stored, and it is a capability |
| `auth_key`, `session` | never |
| `phone`, `phone_number`, `contact`, `vcard` | **there is no `phone` column anywhere in the schema** — see below |
| `email` | |
| `latitude`, `longitude`, `geo`, `venue` | location is **sensitive**-category data under Vietnam's PDPL |
| `online`, `status`, `last_seen_status` | presence is behavioural surveillance with no archival value |
| `read_outbox_max_id`, read receipts, typing | same |

`assert_clean()` re-checks on every non-broadcast write, inside `db.upsert_*`. Defence in
depth: a connector that forgets to scrub still cannot write a phone number into a conversation
row, because the persistence layer re-applies the policy. One regex pass per write, free at a
few hundred messages a day.

**The structural half.** There is nowhere in the frozen schema to put a phone number. That is
not a policy note — it is the reason no connector can accidentally persist one.

---

## 13. Envelope, parse and the honest raw-first caveat

The `Envelope` / `Cursor` / `Coverage` contract itself is specified in
[../ARCHITECTURE.md](../../ARCHITECTURE.md); this section is only what Telegram puts in it.

### 13.1 Kinds

| `envelopes.kind` | `content_type` | Produced by | Coverage |
|---|---|---|---|
| `tg.slice` | `application/x-msgpack` | forward history walk, ≤100 `Message`/`MessageService` objects | `exact` on `source_seq` |
| `tg.ids` | `application/x-msgpack` | ids-refresh window, ≤100 ids | `exact` on `source_seq`; `None`/`MessageEmpty` = absence evidence |

`envelopes.meta` carries `{"tl_layer": 229, "lib": "telethon-1.44.0", "dc_id": …, "pass": "history"|"ids"}`.

### 13.2 msgpack, not `to_json()`

Telethon offers `TLObject.to_json()`, whose docstring notes that *"bytes and datetimes cannot
be represented in JSON, so if those are found, they will be base64 encoded and ISO-formatted,
respectively."* **msgpack preserves both natively**, so it is simultaneously smaller and less
lossy than the JSON path. The pipeline:

```
TL object → .to_dict() → scrub() → msgpack.packb(use_bin_type=True) → Envelope.body
```

`msgpack` is a **connector-group dependency**, declared under the `telegram` group and never
imported by core. Core sees `bytes`.

### 13.3 The caveat, stated once and plainly

**A Telegram envelope is not the wire.** The wire is encrypted and already deserialised by
Telethon before you see it, and `scrub()` removes capability tokens on the way in. So the
raw-first guarantee for Telegram is precisely:

> Same envelope in → same `ParseResult` out, forever, with no network, no credentials and no
> Telegram library newer than the one that wrote it.

It is **not** "these are the bytes Telegram sent." Recording `tl_layer` and `lib` in `meta` is
what makes the first guarantee auditable. Anyone who wants the stricter property would have to
store TL wire bytes plus the layer, and would then be unable to scrub — which is a worse
trade for a corpus containing other people's messages.

### 13.4 `parse()` is pure

No network, no DB, no clock (use `env.captured_at`), no self-mutation. Enforced by
`test_parse_needs_no_secrets`, which constructs the connector with `secrets={}` and parses
every fixture. Telegram makes this easy and there is no excuse to break it: everything the
parser needs is inside the slice.

### 13.5 zstd with a trained dictionary lands with this connector

A 100-message `tg.slice` is exactly the small-record case where zlib's ~1.8× loses badly to
zstd-with-dictionary's ~5.5× — there is no window to find repetition in a 754-byte record, but
a dictionary trained on `(telegram, tg.slice)` carries the TL field names. The `codec` column
and the `zdict` table are in the frozen DDL from day one, so this is a flag flip, not a
migration. Retrain per `(source, kind)` on drift; **never delete an old dictionary**, because
`dict_id` is on the blob.

---

## 14. Rate limiting, errors and the exact backoff

### 14.1 Pacing

The only published figure is Telethon's own hedged docstring: *"Telegram's flood wait limit
for GetHistoryRequest seems to be around 30 seconds per 10 requests, therefore a sleep of 1
second is the default for this limit (or above)."* Note the hedge. Telegram publishes no
numeric history-read rate limit.

Telethon's actual defaults are a trap:

- `wait_time=None` sleeps 1s between GetHistory requests **only when `limit > 3000`**. Below
  that it does not throttle at all.
- For `ids=[...]` fetches, it waits 10s only above **300 ids**.
- `flood_sleep_threshold` defaults to **60**: a `FloodWaitError` under 60s is slept
  transparently; longer ones are raised. Values above one day are clamped to a day.

So **`wait_time` must be passed explicitly**, never trusted.

| Pass | `wait_time` | Effective ceiling |
|---|---|---|
| Backfill history walk | **2.0** | ~3,000 msgs/min — ≈50% of the hedged figure |
| Daily delta walk | 2.0 | irrelevant; a day's delta is 1–3 requests |
| ids-refresh | **1.0** | Telethon does not auto-wait below 300 ids |
| Takeout backfill | 0.5 *(unverified — instrument the first run)* | Telethon says only that "some" calls get lower limits, with no numbers |

**`Budget.bucket` is `None` for Telegram, deliberately.** Telethon sleeps *inside* its own
generator, between the 100-message chunks core never sees, so core cannot get between
requests. `wait_time` **is** the pacer, expressed in the library that owns the request loop.
`Budget` still enforces `max_items` (`--limit N`), `max_requests` and `deadline_ts`, and
`fetch()` must stop on `budget.exhausted()`. This is exactly why the frozen contract declares
`bucket: "Bucket | None" = None`.

Cost model: a channel posting 20 messages/day costs 1 history request + ~5 ids-refresh
requests ≈ **6 requests/day**, well under a second of wire time. A 200k-message backfill is
2,000 requests ≈ 67 minutes at `wait_time=2.0`. **The daily job is free; the budget only
matters on backfill.**

### 14.2 `classify(exc) -> Verdict`

Pure, fixture-testable, written **before** the first line of fetch code.

| Exception | Verdict | Exit | Rationale |
|---|---|---|---|
| `FloodWaitError` | `WAIT(retry_after=e.seconds)` | 75 if > 300s | **Server-supplied and authoritative. Obey it exactly.** Over 300s: checkpoint the cursor, exit `EX_TEMPFAIL`, let the next tick resume |
| `SlowModeWaitError` | `WAIT(retry_after=e.seconds)` | — | same shape |
| `ServerError` (-500), `rpc_call_fail`, DC migrate | `RETRY` | 69 | transient |
| `TimedOutError`, connection reset | `RETRY` | 69 | |
| **`PeerFloodError`** | **`STOP`** | **86** | *"The account is limited (spam-reported)… account-wide with no defined duration."* **Cannot be waited out.** A retry loop here is how a limit becomes an account loss |
| **`AuthKeyDuplicatedError`** (406) | `HUMAN` | 77 | session **permanently** destroyed; needs a fresh login. Never retried |
| `SessionRevokedError`, `AuthKeyUnregisteredError` (401) | `HUMAN` | 77 | |
| `SessionPasswordNeededError` in a scheduled run | `STOP` → config | 78 | login belongs to `crawler login`, never to a job |
| `UserDeactivatedBanError`, `PhoneNumberBannedError` | `STOP` | 86 | |
| `ChannelPrivateError` **after** having had access | `DROP` | 0 | `containers.access_state='no_access'`; the absence sweep is then **skipped** for that container |
| `ChatIdInvalidError`, `PeerIdInvalidError` | `DROP` | 0 | |
| `TakeoutInitDelayError` | `HUMAN` (backfill lane only) | 77 | up to 24h; surfaced to the operator. Never in the scheduled lane |
| `TakeoutInvalidError` | `HUMAN` | 77 | another export session (often Telegram Desktop) invalidated yours |

**`STOP` disarms the schedule.** It writes `sources.state='stopped'`; every later scheduled run
for that source is an immediate no-op exit 0 until `crawler resume --source telegram`. launchd
keeps firing on time and nothing happens. That is the whole point: a retry loop is how a
temporary limit becomes a permanent one.

**Keep `flood_sleep_threshold=60`.** Short waits stay invisible; long ones surface to your own
checkpoint-and-exit logic rather than parking the process for an hour.

---

## 15. Run books

### 15.1 Daily: one public channel (`broadcast` → `social.db`)

```
 1. flock  ~/Library/Application Support/crawler-social/locks/telegram.lock
    held? -> exit 0. A run is already going.
 2. client = TelegramClient(session_path, api_id, api_hash,
                            flood_sleep_threshold=60, catch_up=False)
    await client.connect()
    not is_user_authorized() -> exit 78. Do NOT prompt. Do NOT block on stdin.
 3. entity = await client.get_input_entity(ref)     # cached in the session after run one
    persist the resolved marked chat_id so later runs never re-resolve a username
 4. cursor = cursors[target].axis_value             # 0 on first run
 5. FORWARD WALK
    async for msg in client.iter_messages(entity, reverse=True,
                                          offset_id=cursor, wait_time=2.0):
        accumulate up to 100 -> Envelope(kind='tg.slice',
                                         coverage=exact[lo=first_id, hi=last_id],
                                         cursor_after=watermark(last_id),
                                         privacy='broadcast')
        yield it.  Core commits the envelope, THEN the cursor, in one transaction.
        budget.take(items=n); stop on budget.exhausted()
 6. IDS REFRESH  (mutable fields; there is no "changed since" query)
    ids = last 500 ids for this container, chunked 100
    async for m in client.iter_messages(entity, ids=chunk, wait_time=1.0):
        m is None / MessageEmpty -> absence evidence
        else                     -> Envelope(kind='tg.ids', coverage=exact[window])
 7. Core: parse -> upsert items, item_versions (only if content_hash is new),
    metric_observations (only if the value moved), absence_streak bookkeeping
 8. ABSENCE SWEEP  -- gated: containers.access_state='ok' AND runs.status='ok'
    absence_streak >= 3 -> visibility='deleted_upstream'   (broadcast: tombstone)
 9. runs.status = ok | empty | suspect ; release lock ; disconnect
```

### 15.2 Daily: one private group (`conversation` → `private.db`)

Same skeleton, six deliberate differences:

| # | Difference | Why |
|---|---|---|
| 1 | `open_store(STORE_PRIVATE)` — **envelopes and items both** | §9 |
| 2 | `fetch()` never reads below `gov.enrolled_at` | forward-only enrolment; the floor is a `gaps` row with `reason='enrolled_floor'`, not silence |
| 3 | ids-refresh window widens to **2,000 ids or 30 days**, whichever is smaller | participants delete messages and you must honour that |
| 4 | `absence_strikes = 2`, `on_upstream_delete = 'follow'` | an unsend is a request in the only vocabulary the platform gives the other person |
| 5 | one `AuthorDraft` per sender on first sight; **no roster**, no phone, no presence | §12, and [./docs/GOVERNANCE.md](../GOVERNANCE.md) §9 |
| 6 | removal from the group → `ChannelPrivateError` → `DROP` + `access_state`, **not** a loop | otherwise one removal tombstones the whole conversation |

Enrolment itself is gated: `targets.ack_third_party = 1` is a `CHECK` constraint, not a
warning, and `containers.viewer_account_id` is `NOT NULL` for conversations — "by what right
do I hold this?" as a schema constraint.

### 15.3 One-time backfill (supervised, never scheduled)

```
crawler backfill --source telegram --target @x --since 2015-01-01 --takeout
```

`client.takeout(...)` wraps every request in `InvokeWithTakeoutRequest`. Telethon: *"Some of
the calls made through the takeout session will have lower flood limits… Only some requests
will be affected, and you will need to adjust the wait_time."*

```python
async with client.takeout(finalize=False,          # keeps takeout_id for a resumable run
                          chats=True, megagroups=True, channels=True,
                          users=<only if DMs are in scope>,
                          files=<only if media is enabled>,
                          max_file_size=MAX_BYTES) as t:
    async for msg in t.iter_messages(entity, reverse=True,
                                     offset_id=0, wait_time=0.5):
        ...
```

Lifecycle traps, all real:

- `account.initTakeoutSession` raises `TakeoutInitDelayError` with `e.seconds` — Telegram
  blocks brand-new sessions from exporting for **up to 24 hours**. You can skip the wait by
  approving the export prompt from another logged-in device.
- `TAKEOUT_INVALID` means another export session invalidated yours — including the user
  clicking **Export chat history** in Telegram Desktop.

**Therefore takeout is never wired into the daily lane.** A 24h init delay and a
single-active-export constraint are hostile to cron, and a daily job that inherits them
acquires a failure mode it cannot recover from unattended.

**Named and rejected as the default path:** Telegram Desktop's own "Export chat history"
produces JSON/HTML offline with no API budget at all. Keep it as a documented plan-B for a
genuinely enormous one-off; it is manual, unschedulable, produces a lossier
presentation-oriented shape than `to_dict()`, and collides with programmatic takeout.

### 15.4 Enrolment is a printing command, not a syncing one

```
crawler telegram list-chats          # interactive; PRINTS candidates. Writes nothing.
crawler targets add tg:@somechannel
crawler targets add tg:-1001234567890 --ack-third-party --backfill 90d
```

`iter_dialogs()` is confined to `list-chats`. **There is no `--all-dms`, no `--all-dialogs`,
no `sync-everything` anywhere in the CLI.** The absence is the design — a flag that can be set
to true is a flag that will be. See [./docs/GOVERNANCE.md](../GOVERNANCE.md) §9.

---

## 16. Read-only, enforced by absence

The connector imports **no** send, forward, join, delete, edit or read-mark method, and a unit
test asserts it by AST-walking `connectors/telegram/` for a forbidden-symbol set:

```
send_message  send_file  forward_messages  delete_messages  edit_message
JoinChannelRequest  ImportChatInviteRequest  LeaveChannelRequest
mark_read  send_read_acknowledge  ResolvePhoneRequest  ImportContactsRequest
GetParticipantsRequest
```

A toggle is a thing that can be set to true. Absence of capability is the only setting that
holds — and the stakes are specific: `PeerFloodError` is account-wide, has no defined
duration, and cannot be waited out.

Two entries deserve a note. `mark_read` is excluded because the archive must not alter the
account's read state — otherwise the tool visibly changes the user's own Telegram UI, which is
both annoying and an automation signal. `GetParticipantsRequest` is excluded because roster
enumeration is a contact graph the project deliberately does not build.

Telethon's own FAQ guidance, for context: use the library "only on well-established accounts",
avoid "actions that could be seen as abuse", avoid "calling many requests really quickly."
Reading history of chats you are already in is what every official client does. Bulk
`resolveUsername`, mass-joining and participant enumeration are the medium-risk operations,
and none of them are in this connector.

---

## 17. Testing

Fixtures are `raw_payloads`-shaped, not bespoke test artifacts, and are captured by
`crawler capture --redact` — **never hand-copied**.

```
tests/fixtures/telegram/
  channel_slice_100.msgpack        + meta.json    happy path, broadcast
  channel_slice_with_service.msgpack              §10.4 — service messages present
  channel_ids_refresh.msgpack                     views/reactions moved
  channel_ids_with_empty.msgpack                  MessageEmpty -> absence evidence
  album_grouped_id.msgpack                        item_relations rel='album_member'
  forward_with_noforwards.msgpack                 content protection flag
  entities_vietnamese.msgpack                     UTF-16 offsets over combining marks
  edit_same_text_new_views.msgpack                MUST write a metric row and NO version row
  empty_history_after_removal.msgpack             the SUSPECT case
  floodwait_420.repr                              classify() -> WAIT(retry_after=n)
  peerflood.repr                                  classify() -> STOP
  authkeyduplicated.repr                          classify() -> HUMAN
  channelprivate.repr                             classify() -> DROP
```

**No real DM or private-group message enters the repo — redacted or otherwise.** Conversation
fixtures are synthetic or come from a test group the user owns outright.

Tests worth writing before the features they cover:

1. `test_parse_needs_no_secrets` — construct with `secrets={}`, parse every fixture.
2. Golden-parse snapshot: `sha256(canonical_json(ParseResult))` per fixture per
   `parser_version`. Bumping the version must change the snapshot **deliberately**.
3. `test_read_only_by_absence` — the AST walk in §16.
4. `test_edit_vs_metric` — same text, higher `views` → one `metric_observations` row, **zero**
   `item_versions` rows. Then changed text → one `item_versions` row.
5. `test_service_message_counts_as_found` — a slice containing `MessageService` must not
   produce a coverage shortfall.
6. `test_two_files_one_tick` — one fixture run over a channel target and a DM target writes
   items to `social.db` and `private.db` respectively, and **`private.db` contains the DM
   envelope too**. Audit with `MATCH`, **never `count(*)`** on an FTS table — external-content
   FTS5 reads the content table's row count and a `count(*)` audit will falsely pass.
7. `test_privacy_fails_closed` — an unclassifiable peer proposes `conversation`.

`crawler tick --fixture tests/fixtures/telegram/` runs the entire production pipeline — budget
accounting, commit ordering, coverage checks, gap recording, upserts, sweeps, SUSPECT
detection — with no network and no session file.

---

## 18. Verify before building

Nothing below blocks the design; each is a specific check with a specific consequence.

| # | Claim | Status | How to settle it |
|---|---|---|---|
| 1 | Telegram API ToS wording — the "retractable, limited…" licence and the `recover@telegram.org` recourse | **unverified** — search extracts only; core.telegram.org was not fetched | Read core.telegram.org/api/terms and /api/obtaining_api_id in a browser |
| 2 | The ML-training prohibition and its narrow consent exception | **likely** — corroborated across telegram.org/tos/bot-developers and /api/terms via search; body pages not fetched | Same. This one is load-bearing if any LLM use is contemplated |
| 3 | Whether a **public channel's history reads without joining** | **unverified** — sources ambiguous | Resolve one throwaway public channel and `iter_messages(limit=5)` without joining. Decides whether the tool must ever perform a join (a medium-risk action) |
| 4 | Whether `iter_messages` sends `messages.readHistory` | **unverified** | Read one unread channel, then check the unread badge in the Telegram app. If it does mark read, the archive is visibly mutating account state |
| 5 | Telethon 1.44.0 on Python 3.13 / 3.14 | **unverified on this machine** — metadata is stale (`>=3.5`), repo has a 3.14 CI commit | Smoke-test on the actual uv-managed interpreter before pinning |
| 6 | Takeout's flood-limit multiplier | **unverified** — Telethon gives no numbers | Instrument the first backfill; `wait_time=0.5` is a guess |
| 7 | Bot API file limits (20 MB / 50 MB / ~2 GB self-hosted) and user limits (2 GB / 4 GB Premium) | **likely** — secondary sources | Only matters if media download is ever enabled |
| 8 | `file_reference` validity duration | **unverified** — the error and refresh path are documented; the duration is not | Design does not depend on it. Do not introduce a dependency |
| 9 | Message-id namespace rule for private chats and basic groups (per-chat vs per-account) | **unverified** — sources conflicted | Irrelevant to correctness: `(container_id, platform_item_id)` is the key either way and ids are monotonic within a chat. **Do not write a plan sentence asserting the finer rule** |
| 10 | Whether the vendored SQLCipher in `sqlcipher3` 0.6.2 has FTS5 | **unverified** | `SELECT * FROM pragma_compile_options();` at M0. Blocks `private.db` full-text search, nothing else |
| 11 | Bot API version 10.0 | **likely** — from python-telegram-bot 22.8's PyPI text | Changes no decision |
| 12 | Datacenter/VPS IPs drawing more anti-abuse scrutiny | **unverified** — one anecdotal 2025 blog | Do not design around it. It happens to point the same way as running on the user's own machine |

---

## 19. Phase 2 delivery checklist

Telegram is connector two because it tests the architecture exactly where the architecture was
chosen: one connector, one session file, emitting **broadcast channels and private
conversations** through one persistence path. If the two-file design is wrong, this is where
you find out — at connector two, not connector four.

Machinery that arrives with this phase (specified but unbuilt before it):

- [ ] `private.db` + SQLCipher (`sqlcipher3` 0.6.2, key from Keychain, `PRAGMA key` first)
- [ ] The two-file router: `GovernanceProfile` resolved **before any write**, envelopes routed too
- [ ] `containers.shape` / `privacy` / `privacy_source`, and the `viewer_account_id` `CHECK`
- [ ] `targets.enrolled_at` as an enforced ingest floor, with the `gaps` row that admits it
- [ ] `item_versions` and the content-hash rule
- [ ] `metric_observations` change-log + `items.metrics_current`
- [ ] The absence/tombstone sweep, gated on `access_state` and `runs.status`
- [ ] Retention sweep and `purge`
- [ ] zstd + trained dictionaries, keyed `(telegram, tg.slice)`
- [ ] `scrub()` + `assert_clean()` with the Telegram `DROP_KEYS`

Not built here: the spool, the `Receiver`, the billed counter, `Cap.FILE_IMPORT`.

Every one of these is exercised by a source that is **free**, whose credentials are
**self-issued with no approval gate**, and where a mistake costs a session file rather than
money or an irreplaceable account. That is the cheapest possible place to be wrong.
