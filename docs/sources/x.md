# Connector: `x`

**Phase 4 — last, and split in two.** One free half that ships. One billed half that ships **disabled**.

---

## VERDICT

**Build the free half. Ship the billed half switched off. Never scrape.**

The old answer — "X is not worth it at personal scale" — was correct under the $200/month Basic floor and is **stale as of 2026-02-06**. It should not be repeated in this plan without the date attached. But the new answer is not "build X"; it is narrower than that:

| | Verdict | Cost | Ships |
|---|---|---|---|
| **`x.archive`** — the user's own data-export ZIP | **BUILD** | **free** | Phase 4a, unconditionally |
| **`x.rest`** — third-party timelines via API v2 | **BUILD, SHIP DISABLED** (`enabled: false`) | `[verified]` **$0.005 per Post read**; **~$15/month** at a realistic 20-account daily poll (arithmetic over that price, §3.2 — not itself a quoted figure) | Phase 4b, behind a deliberate switch |
| Every scraping path — twscrape, RSSHub/RSS-Bridge with cookies, self-hosted Nitter, resellers | **REJECTED PERMANENTLY** | saves ~$15/month | never — §1 |

**Why the archive half is unconditional.** It is free, it is the **only** path to the user's own X DMs, it is the **only** path to their complete own history (no ~3,200-post ceiling), it carries zero ToS risk, and its provenance story is far better than any OAuth scope — an export the user personally requested for their own account, versus a token that could be pointed anywhere. It also lands perfectly on raw-first: **the ZIP *is* the payload.** And it proves `Cap.FILE_IMPORT`, which is the same shape the spool uses and which Facebook and Zalo will both want later.

**Why the billed half ships disabled.** X is the only connector in this project where **a bug costs money rather than time or quota**. A pagination loop that fails to honour `next_token` termination, or a `since_id` watermark that resets to zero, spends real dollars overnight. Its pricing model is seven months old, has changed three times in three years, and everything about it is a pricing question rather than a technical one. So the code exists, the spend governor exists, and the switch is off until the user makes a deliberate decision with a number attached.

**Do not defer the billed half into vapour, and do not pad it into a fake-complete spec.** §3 is exactly as long as it needs to be to enable safely: the pricing table, the spend governor, the defaults that are spending decisions, and the trigger. It is not an endpoint catalogue.

**Confidence convention.** `[verified]` = read from a primary source. `[likely]` = consistent secondary sources, no primary. `[unverified]` = confirm before relying on it. Recon fetched `docs.x.com/x-api/getting-started/pricing` and `docs.x.com/x-api/fundamentals/rate-limits` on **2026-09-01**; those are the `[verified]` price and rate figures below. **The console at `console.x.com` is the only authority on what you will actually be billed** — confirm there before flipping the switch. This document makes no request to `x.com` or any of its subdomains.

---

## 1. Rejection register — permanent, dated, not to be relitigated

Written down with dates precisely so a future self or a future agent cannot quietly reopen it.

| Path | Status | Date | Why |
|---|---|---|---|
| **twscrape** | **rejected permanently** | alive at **0.20.1, 2026-08-25** | `[verified]` It is maintained, and covers *more* surface than the paid API does cheaply — search, user tweets, replies, media, tweet details, threads, retweeters, followers, lists, communities, bookmarks. **That is what makes it a trap.** Its own README: *"twscrape requires authorized X/Twitter accounts"* and *"X/Twitter's Terms of Service discourage using multiple accounts."* Its entire rate-limit strategy is **account rotation** — lock the limited account, move to the next — which presumes disposable accounts. The account at risk is whichever one's `auth_token` you paste in. For a personal tool that is the user's real account, with their DMs, history and identity. |
| **RSSHub / RSS-Bridge** | **rejected permanently** | 2026 | `[verified]` RSS-Bridge's TwitterBridge has been broken since X removed guest-token access (issues #3239, #3469, #3603, #3806 — a graveyard of 400/403/404). RSSHub's twitter route requires `TWITTER_AUTH_TOKEN` / `TWITTER_COOKIE` (`auth_token` + `ct0`) from a real logged-in account, with users reporting failures even on fresh tokens. **It is not an RSS alternative to scraping — it *is* scraping, with your own session cookie, wearing an RSS costume**, and it inherits 100% of the suspension risk with none of the control. |
| **Nitter, including self-hosting** | **rejected permanently** | **2026-08-24** | `[verified]` X Corp sent a cease-and-desist to Nitter's maintainer with a 2026-08-25 5pm EST deadline, alleging *"unlawful use and circumvention of X's Application Programming Interface (API) and associated data"* and citing the Texas Harmful Access by Computer Act and the Lanham Act. The demand explicitly covered **"a permanent takedown of Nitter instances and the project's repository"** — self-hosted instances and the GitHub codebase, not just nitter.net. nitter.net went offline; XCancel went down with it. **This closed the last "run it yourself quietly" option one week before this plan was written.** |
| **Third-party API resellers** (e.g. twitterapi.io) | **rejected permanently** | — | `[unverified]` vendor self-reported $0.00015/read, a claimed 33× saving — **$14.55/month** at median load. Not a number worth taking risk for. They are not licensed redistributors; they are scraping operations at scale, so you inherit the exact legal and continuity exposure you avoided by going official, laundered through a third party with a Stripe checkout. Post-Nitter their continuity risk is not theoretical. And it routes the user's reading interests through an unaccountable third party, which cuts against the entire point of a local, private, self-owned tool. |
| **Community Archive** (`TheExGenesis/community-archive`) | **not a data source** | — | `[verified]` Open, public-domain, user-donated Twitter archives with an API — a legitimate research corpus built largely around one community. It will not contain the 20 accounts this user cares about except by coincidence. **Keep one line for it as prior art: its parser is a reference implementation for the export-ZIP format** (§2.3). |

**The asymmetry, stated once.** The scraping path saves at most $15/month and risks **permanent suspension of the account holding the user's real identity** — unilateral, instant, requiring no legal theory and with no appeal worth the name. X also rotates guest tokens, GraphQL `doc_id`s, rate limits and detection heuristics roughly every 2–4 weeks, so it carries permanent maintenance tax on top. Paying $15/month to make an entire risk category disappear is the easiest trade in this project.

**The tempting middle path is the worst option available.** *"Use twscrape as a fallback when the API budget is exhausted"* means the scraper runs precisely when you are least watching, and puts the account at risk to avoid an overage of a few dollars. **If the budget is exhausted, the correct behaviour is `STOP` and log.**

`[verified]` **Legal note, for calibration not comfort.** *X Corp v. Bright Data* (N.D. Cal., No. 23-cv-03698-WHA): Judge Alsup dismissed **all** of X's claims in May 2024 — breach of contract, trespass, misappropriation, unjust enrichment, tortious interference — holding the Copyright Act preempts X's state-law contract claims and warning X's theory *"risks the possible creation of information monopolies that would disserve the public interest."* Parties reported a settlement in principle in June 2025; the case was stayed. So there is **no merits precedent** establishing that scraping public X data is unlawful. That is cold comfort here: nobody sues an individual over a 20-account daily poll. The realistic threat is (a) account suspension, and (b) demonstrated willingness to send repository-takedown C&Ds. A personal local tool is not a plausible defendant — and also has zero leverage, while the thing it stands to lose is the thing X can take without asking anyone.

---

## 2. `x.archive` — the free half (ships Phase 4a)

### 2.1 What it is

`[verified]` Settings → Your Account → **"Download an archive of your data"** produces a ZIP containing tweet text, timestamps, media files, follower and following lists, **DMs (text only)**, and account settings. `[likely]` Turnaround is typically ~24h (up to 48h for very large accounts); the download link expires **7 days** after generation.

| Property | Value |
|---|---|
| Cost | **$0** |
| ToS risk | **none** — it is X's own export feature |
| Post ceiling | **none** — complete own history, unlike the ~3,200 timeline cap |
| DMs | **yes, text** — the only free path, and the only path this project will use |
| Cadence | manual, a few times a year |
| Transport | `Cap.FILE_IMPORT` — `fetch()` over a local path |
| `sources.transport` | `'archive'` |

Even X's cheapest API path loses to this: `[verified]` "owned reads" (your own data) are $0.001/resource, and free beats $0.001.

### 2.2 Why DMs come exclusively from here

**Never call the DM Events API.** `[verified]` DM Events are priced at **$0.010 per resource** and `[unverified]` historically required elevated OAuth scopes (`dm.read`, OAuth 2.0 user context) and possibly a higher access level. The archive is free, complete, and — the part that matters most — **has far better provenance**: an export the user personally requested for their own account, versus an OAuth scope that could be pointed anywhere.

**This is the second design tension in miniature, and it is easy to miss because X reads like a broadcast source right up until the DM folder lands in the same SQLite file.** The archive contains full DM history with named counterparties who never consented to being in anyone's database. So:

```python
def propose_privacy(self, draft: TargetDraft) -> str:
    if draft.kind == "dm":
        return CONVERSATION          # -> data/private.db, SQLCipher
    if draft.kind in ("timeline", "profile"):
        return BROADCAST             # -> data/social.db, plain
    return CONVERSATION              # fail closed
```

Consequences, all of which fall out of the frozen design with **zero X-specific code**:

- DM containers get `shape='conversation'`, land in `private.db`, and **their envelopes land there too**. Routing parsed DMs to the encrypted file while the raw ZIP slice sits in `social.db` would defeat the whole separation — raw-first means the payload contains everything the parsed rows do and more.
- `containers.viewer_account_id NOT NULL` is enforced by CHECK. "By what right do I hold this?" as a constraint.
- `targets.ack_third_party = 1` is required by CHECK before a conversation target can be enrolled.
- `enrolled_at` is the forward-only floor. For an archive import the user is deliberately choosing to backfill, so `backfill_from` is set explicitly — **per conversation, never in bulk.** There is no `--all-dms`.
- `crawler add` **refuses** to enrol a conversation target if `fdesetup status` reports FileVault off.
- Conversation envelopes are **not exportable at all**. No flag exists.

The one X-specific decision: **a single ZIP produces both privacy halves in one import run.** That is the same property that made Telegram the architecture's decisive test case (one connector, one session, broadcast channels *and* private DMs), arriving a second time. If the two-file router works for Telegram in Phase 2, X's archive importer needs no new machinery in Phase 4 — which is a good reason to build them in that order.

### 2.3 Parsing — and what is honestly unknown

`[unverified]` **The ZIP's internal layout was not verified.** Recon confirmed only *which data categories* the export contains, and that `TheExGenesis/community-archive` is a working reference parser for the format. It did **not** confirm file names, directory structure, or the JS-assignment wrapper that the export is widely described as using.

**Do not write the parser from memory. Open one ZIP first.** Concretely, before Phase 4a:

1. Request the archive. Wait ~24h.
2. `unzip -l` it and record the actual manifest of files.
3. Read `TheExGenesis/community-archive`'s parser as prior art.
4. *Then* write `parse()`, and capture the fixtures from that real ZIP with `crawler capture --redact`.

This is the single largest unknown in the archive connector and it is cheap to close.

### 2.4 Envelope design

The ZIP is the payload, but a 2 GB ZIP is not one envelope. Split at the file boundary inside the archive:

| `envelopes.kind` | Contents | Privacy | Coverage |
|---|---|---|---|
| `x.archive.manifest` | the file listing + account settings | `broadcast` | `OPAQUE` |
| `x.archive.tweets` | one envelope per tweets part-file | `broadcast` | `EXACT` over `[oldest, newest]` in that part |
| `x.archive.dm` | **one envelope per conversation** | **`conversation`** | `EXACT` over that conversation's range |
| `x.archive.follows` | follower / following lists | `broadcast` | `OPAQUE` |

`envelopes.privacy` is stamped at fetch time and decides the file **before any write**. One envelope belongs to exactly one target, and a target maps to exactly one container — so routing is total and trivial.

**One envelope per DM conversation, not one per part-file.** This is deliberate and it is what makes `crawler forget <conversation>` and `purge --person` clean operations: deleting one conversation deletes whole envelopes rather than orphaning parsed rows whose raw bytes still contain the person. The residual purge problem that Reddit has (one listing envelope holds 100 authors' posts) does not arise here, and it is worth the extra rows to avoid it.

The original ZIP itself is **not** stored in `envelopes`. Keep it wherever the user keeps it, outside the worktree; the import records its `sha256` in `runs.params` so a re-import is recognisable as a re-import.

`Cap.FILE_IMPORT` means `fetch()` reads a local path. There is no network, no credential, no rate limit and no budget beyond `max_items`. `classify()` maps a missing file or a bad ZIP to `Verdict(HUMAN)` / exit 78 — a config problem a person must fix, never a retry.

### 2.5 Run modes

| Mode | Behaviour |
|---|---|
| `crawler import --source x --file ~/Downloads/twitter-2026-09-01.zip` | `runs.mode='import'`; parses every part; upserts; idempotent on re-run (`UNIQUE(container_id, platform_item_id)`) |
| `--limit N` | bounds items parsed; does not advance any watermark |
| daily | **not scheduled.** There is nothing to poll. The archive is a manual, occasional act — which is exactly what the one-time run mode is for. |

Re-importing a newer archive months later is the normal upgrade path and is safe: the upsert `COALESCE`s every optional field, so a second import adds new rows and cannot wipe good ones.

---

## 3. `x.rest` — the billed half (ships disabled)

### 3.1 The pricing model

`[verified]` On **2026-02-06** X killed tiered pricing for new developers. Official docs: *"The X API uses pay-per-usage pricing. No subscriptions—pay only for what you use."* Credits are purchased upfront in the Developer Console and deducted per request. Free tier discontinued for new developers. Legacy Basic ($200/mo) and Pro ($5,000/mo) remain **only** for existing subscribers — a new signup cannot buy them. Enterprise (~$42k+/mo, contract) is the only other door. No minimum spend, no contract, start/stop anytime.

`[verified]` **Reads are charged per resource returned, not per request:**

| Resource | Price |
|---|---|
| **Posts** | **$0.005** |
| Users | $0.010 |
| DM Events | $0.010 |
| Following / Followers | $0.010 |
| Lists, Spaces, Communities, Notes, Profile Updates | $0.005 |
| Likes, Mutes/Blocks | $0.001 |
| **Owned reads (your own data)** | **$0.001** |

Writes (per request): Post creation $0.015 · **Post containing a URL $0.200** · DM / user interactions $0.015 · List creation $0.010. **This connector performs no writes, ever.** There is no send, post, like or follow method in it — enforced by absence, the same way the Telegram connector is read-only.

`[verified]` Monthly cap: official docs say **3,000,000 Post reads per billing cycle**. `[unverified]` Multiple vendor blogs say 2,000,000. Either is ~250× a personal workload, so the conflict is operationally irrelevant — **but it is a useful signal that vendor blogs about X pricing are unreliable, and the console is the only source of truth.** Do not cite a vendor blog as authority for anything billable.

### 3.2 Realistic cost

Billing scales with **how much those accounts actually post**, not with request count.

| Load | Posts/day | Posts/month | Cost/month |
|---|---|---|---|
| Light (2/account/day, 20 accounts) | 40 | 1,200 | **$6.00** |
| **Median (5/account/day, 20 accounts)** | **100** | **3,000** | **$15.00** |
| Heavy (20/account/day, 20 accounts) | 400 | 12,000 | **$60.00** |

Plus one-time username → numeric-id resolution: 20 × $0.010 = **$0.20**, cached forever in `containers.extra`. Profile-metadata refresh **weekly, not daily**: 20 × $0.010 × 4.3 = **$0.86/month** (daily would be $6/month for almost no signal).

`exclude=retweets` cuts cost materially on RT-heavy accounts — retweets bill as Post reads like anything else.

For comparison: legacy Basic was $200/mo for ~10k–15k reads. Pay-per-usage at median load is roughly **13× cheaper**. That single fact is what flips the verdict, and it is why any plan text written before February 2026 needs a date check.

### 3.3 Every default is a spending decision

| Knob | Default | Cost if wrong |
|---|---|---|
| `exclude` | `retweets` | RT-heavy accounts can double the bill |
| **Replies / thread reconstruction** | **OFF** | **the blowup risk.** Requires recent search with `conversation_id:`, reaches back only 7 days, and bills **every single reply** at $0.005. One viral post with 2,000 replies is **$10** — and the cost scales with *other people's engagement*, which the user does not control. Off by default; per-post opt-in with a hard max-replies bound. |
| Backfill depth | **200 posts/account (~$1/account)** | full 3,200 × $0.005 = **$16/account**; 20 accounts = **$320 one-time**. A real number the plan states out loud. |
| Media | metadata yes, binaries opt-in | `[unverified]` media may ride free on the Post read via expansions (§13) |
| Profile refresh | weekly | daily is 7× the cost for near-zero signal |
| `--limit N` | bounds **posts fetched** | posts fetched is *the billable unit*, so `--limit` doubles as the spend brake. Not pages, not accounts. |
| Full-archive search | **not built** | `[unverified]` availability is contested (§13); nothing in the design needs it |

You also pay a systematic-incompleteness penalty on replies even when you do enable them: the 7-day recent-search window means late-arriving replies are lost anyway, so you pay a lot for a picture that is incomplete by construction.

### 3.4 The spend governor — a counter, not a bucket

**X's monthly allowance is money, not rate.** A bucket smooths a rate; a counter enforces a budget. A bucket with a 0.004/s refill rate cannot answer "how much have I spent this month". So X gets both, and they do different jobs:

```yaml
rate:
  x:
    mode: quota              # a comment for humans. NO CODE BRANCHES ON THIS.
    buckets:
      timeline: { capacity: 20, refill_per_sec: 1.0 }    # nowhere near binding, §3.5
    spend_cap:
      period: month
      metric: cost_micros
      limit:  25_000_000     # $25.00/month, ~1.7x the median estimate
      on_cap: stop_and_alert # STOP, not WAIT. Never fall back to anything.
```

```sql
-- checked BEFORE the run starts, not during
SELECT value FROM usage_counters
 WHERE source='x' AND period=strftime('%Y-%m','now') AND metric='cost_micros';
```

```python
Budget(
    max_items    = args.limit,     # posts fetched == billable units
    max_requests = 200,
    deadline_ts  = None,
    spend_units  = remaining_this_run,   # X is the ONLY connector that fills this
    bucket       = Bucket(capacity=20, refill_per_sec=1.0),
)
```

`runs.cost_micros` records per-run spend (every other connector writes 0), so **cost drift is visible in the data rather than only on a statement.** `crawler status` surfaces month-to-date.

**Set the cap in the X console *as well as* locally.** A local cap protects against your own bug; a console cap protects against a unilateral repricing. They defend different things and you want both.

### 3.5 Rate limits are irrelevant — the governor is cost

`[verified]` From `docs.x.com/x-api/fundamentals/rate-limits` (fetched 2026-09-01):

| Endpoint | Per app / 15 min | Per user / 15 min |
|---|---|---|
| `GET /2/users/:id/tweets` | 10,000 | 900 |
| `GET /2/tweets` (lookup by id) | 3,500 | 5,000 |
| `GET /2/tweets/search/recent` | 450 | 300 |
| `GET /2/tweets/search/all` | 300, **and 1/sec** | — |
| `GET /2/users/:id/mentions` | 450 | 300 |

A 20-account daily poll is **20 requests/day**. Headroom is roughly four orders of magnitude. **Spending more credits does not lift rate limits** — they are a separate axis. `[unverified]` These values may have drifted (the rate-limits doc carries no visible revision date and X has changed them repeatedly), but drift of even 10× would not affect the design.

### 3.6 The 24-hour UTC dedup rule — and why it picks the cron time

`[verified]` Verbatim: *"All resources are deduplicated within a 24-hour UTC day window. If you request and are charged for a resource (such as a Post), requesting the same resource again within that window will not incur an additional charge."*

Three consequences, in order of usefulness:

1. **A failed run can be retried any number of times within the same UTC day at zero additional cost.** So the retry policy is bounded by the **UTC day**, not by an attempt count.
2. An author expansion resolving the same 20 users across 300 posts is charged for **20** User reads, not 300.
3. **Straddling midnight UTC re-bills everything.** The daily job must not span 00:00Z.

**Therefore the run is scheduled at 01:00 UTC = 08:00 ICT.** That gives 23 hours of free retry headroom inside one billing window and lands at a natural morning-digest time for a UTC+7 user. This is a case where the **billing model, not operational convenience, picks the cron time** — and the config carries a comment saying so, because it otherwise reads as arbitrary:

```yaml
# 01:00 UTC == 08:00 ICT. NOT arbitrary: X deduplicates billing within a
# 24-hour UTC window, so this run plus 23 hours of retries sits inside ONE
# billing day. A run that straddles 00:00Z is billed twice for the same posts.
```

### 3.7 What is reachable, and what is not

| Reachable | Endpoint | Cost |
|---|---|---|
| User timeline, ~3,200 most recent posts | `GET /2/users/:id/tweets` | $0.005/post |
| Last 7 days across all of X | `GET /2/tweets/search/recent` | $0.005/post |
| Quote posts | `referenced_tweets` expansion on a post you already have | the quoted post bills separately if expanded |
| Media URLs | `expansions=attachments.media_keys` | `[unverified]` — §13 |
| Author metadata | `expansions=author_id` | $0.010/user, `[unverified]` whether per-instance or per-unique |

| **Not reachable** | Why |
|---|---|
| **More than ~3,200 posts of any third-party timeline** | `[verified]` long-standing platform limit, independently corroborated by twscrape's README. **No amount of money fixes this via the timeline endpoint.** |
| Older than 7 days without full-archive search | `[unverified]` whether pay-per-usage unlocks `/2/tweets/search/all` — see §13 |
| Edits | invisible to `since_id` polling; detecting them costs $0.005/post/day (§3.8) |
| Deletions | invisible for the same reason |
| Protected accounts | not visible to any credential you hold |
| The user's own DMs | **deliberately not fetched here** — §2.2 |

**The 3,200 ceiling is a `gaps` row, not a footnote.** When a backfill stops at the ceiling with history still older than the oldest post retrieved, the connector emits:

```python
Coverage(kind=COVER_PARTIAL, axis=AXIS_TIME,
         lo=0, hi=oldest_retrieved_created_at,
         note="timeline_ceiling")
```

and core writes `gaps(reason='timeline_ceiling')`. **This gap can never be filled** by the timeline endpoint — `filled_by` stays NULL forever unless full-archive search is ever enabled. That is the honest state, and it is exactly why the archive is permanently incomplete for any account other than the user's own. Surface it in the plan rather than discovering it mid-backfill.

### 3.8 Cursor design

`[verified]` Post IDs are Snowflake — monotonically increasing with time — so **`since_id = MAX(post_id) seen for that author`** is the correct high-water mark, and it is strictly better than `start_time`: no clock skew, no timezone bugs, no boundary double-fetch. `since_id` is **exclusive**.

| | Durable | Ephemeral |
|---|---|---|
| Value | `since_id` (Snowflake) | `next_token` / `pagination_token` |
| Lives in | `cursors` (`kind='watermark'`, `axis='published_at'`, `axis_value = created_at` of that post) | **`runs.params` — never `cursors`** |

The loop is `while next_token and posts_fetched < limit`. `[verified]` `since_id` must be `< until_id` when both are given; both are numeric strings up to 19 digits; `start_time`/`end_time` are ISO-8601 with a hard floor of **2010-11-06**.

**What `since_id` cannot do, and why the design accepts it:**

- **Edits are invisible.** X posts carry `edit_history_tweet_ids`, and re-reading by ID to detect edits costs $0.005 per post per UTC day — **at 3,000 stored posts that is $15/day.** Do not do this. Store what you saw, timestamped, raw-first, and accept that the archive is a **point-in-time capture, not a mirror.**
- **Deletions are invisible** for the same reason: a deleted post simply stops appearing. Arguably the correct behaviour for an archive.
- **`Cap.MUTABLE_METRICS` is NOT set for X**, unlike Reddit and Facebook. `public_metrics` do drift, but re-observing them costs $0.005/post/refresh, which is a spending decision the user has not made. Metrics are captured once, at ingest, and never refreshed. `metric_schedule` gets no X rows. **This is the single largest fidelity concession in the connector and it is purely economic.**

The daily poll itself costs nothing beyond the resources returned — an account that posted nothing since the last poll returns an empty result set and bills $0.

**Absence sweep:** `Cap.DELETE_EVENTS` is unset, so the sweep is nominally mandatory — but with no metric refresh and no rescan window, X items are never re-observed and `absence_streak` never increments. **Set `rescan_window_days = 0` for X and be explicit that upstream deletes are simply not detected.** Pretending otherwise would be worse than saying so. `on_upstream_delete` stays `tombstone` for broadcast timelines (never `follow`) because nothing ever triggers it.

### 3.9 Capability flags

```python
# x.archive
caps = (Cap.FILE_IMPORT | Cap.BACKFILL | Cap.CONVERSATIONS)

# x.rest
caps = (Cap.BACKFILL_CAPPED | Cap.EXACT_CURSOR | Cap.PARALLEL_TARGETS | Cap.BILLED)
```

`Cap.BILLED` is the flag that earns its place by making core do something: check `usage_counters` **before** the run starts and refuse to begin if the month's cap is spent. If nothing in core branched on it, it would be documentation and would belong in the README.

`capabilities_note()` for `x.rest`:

> `timelines cap at ~3,200 posts per account and there is no page beyond it; that gap is permanent. Replies OFF by default (they bill per reply and only reach back 7 days). Metrics captured once at ingest and never refreshed — refresh costs $0.005/post/day. Edits and deletions are not detectable. Billed per resource returned; --limit bounds POSTS, the billable unit.`

`capabilities_note()` for `x.archive`:

> `the user's own export ZIP: complete own history with no 3,200 ceiling, plus own DMs (text only). Manual, a few times a year. DM conversations are privacy=conversation and land in private.db. Not schedulable — there is nothing to poll.`

---

## 4. Error and failure mapping

`classify(exc) -> Verdict` is pure and fixture-testable.

| Condition | Verdict | `runs.status` | Exit | Note |
|---|---|---|---|---|
| 200, resources returned | `OK` | `ok` | 0 | increment `usage_counters` and `runs.cost_micros` |
| 200, empty result set | `OK` | `empty` | 0 | normal for an account that did not post; bills $0 |
| 5xx, connection reset, timeout | `RETRY` | `error` | 69 | **free within the same UTC day** — retry generously, then stop at 00:00Z |
| 429 | `WAIT`, `retry_after` from `x-rate-limit-reset` (epoch) | `rate_limited` | 75 | should be unreachable at this scale; if it fires, something is looping |
| 401 invalid / revoked bearer | `HUMAN` | `needs_human` | 77 | re-auth via `crawler login --source x` |
| Missing bearer token in Keychain | `HUMAN` | `needs_human` | 78 | print the `security add-generic-password` line |
| **Monthly spend cap reached** | **`STOP`** | `blocked` | **86** | `sources.state='stopped'`; disarmed until `crawler resume --source x`. **Never falls back to anything.** |
| Account suspended | `STOP` | `blocked` | 86 | |
| Post deleted / account protected or suspended (per-target) | `DROP` | `ok` | 0 | mark dead, advance past it |
| Timeline ceiling reached during backfill | *not an error* | `partial` | 0 | `Coverage(PARTIAL, note='timeline_ceiling')` → permanent `gaps` row |
| **`x.archive`**: file missing / not a ZIP / unreadable | `HUMAN` | `needs_human` | 78 | a person must fix it; never retried |
| **`x.archive`**: an expected part-file absent from the ZIP | `OK` + diagnostic | `partial` | 0 | X's export contents vary; record it in `ParseResult.diagnostics`, do not fail the run |

**The X-specific guard that must exist before the switch is ever flipped:** an integration test asserting **the fetch loop terminates on a mocked infinite cursor.** Every other connector's runaway loop wastes time or quota. This one spends money.

---

## 5. Mapping onto the unified schema

Full DDL in [../DATA-MODEL.md](../DATA-MODEL.md).

### 5.1 Containers

| Column | `x.rest` timeline | `x.archive` DM |
|---|---|---|
| `source` | `'x'` | `'x'` |
| `platform_container_id` | `'@handle'`, lowercased — **must be derivable purely from the spec string**, because `resolve()` is pure and cannot spend $0.010 looking up the numeric id | the conversation id from the export |
| `kind` | `'timeline'` | `'dm'` |
| `shape` | `'feed'` | `'conversation'` |
| `privacy` | `'broadcast'` | **`'conversation'`** |
| `viewer_account_id` | may be NULL | **NOT NULL** (CHECK-enforced) |
| `extra` | `{"user_id": "44196397", "resolved_at": …}` — **the numeric id is cached here forever; resolving it costs $0.010 and must never repeat** | `{"participants": [...]}` |
| store | `data/social.db` | **`data/private.db`** |

Caching the numeric user id in `containers.extra` is not an optimisation, it is a cost control: without it, every run re-resolves 20 handles for $0.20/day = $6/month, which is 40% of the entire budget for information that never changes.

### 5.2 Items

| Column | `x.post` | `x.dm` |
|---|---|---|
| `platform_item_id` | the numeric post id | the message id |
| `item_type` | `'x.post'` | `'x.dm'` — provenance only, core never branches on it |
| `parent_ref` | `referenced_tweets[type='replied_to'].id`, **always written even when unresolvable** | NULL |
| `root_ref` | derive from `referenced_tweets`, **not from `conversation_id`** — `[verified]` for a **retweet**, `conversation_id` equals the retweet's own id, not the original's | NULL |
| `source_seq` | NULL | NULL |
| `published_at` / `published_prec` | `created_at` / `'exact'` | export timestamp / `'exact'` |
| `text` | `text` | message text |
| `is_from_self` | author == the user | sender == the user |
| `permalink` | `https://x.com/<handle>/status/<id>` — **stored, never fetched** | NULL |
| `extra` | `{"lang", "possibly_sensitive", "edit_history_tweet_ids", "reply_settings"}` | `{}` |

**Quote and retweet are not parent edges.** They go to `item_relations`:

| `item_relations.rel` | From |
|---|---|
| `'quote_of'` | `referenced_tweets[type='quoted']` |
| `'repost_of'` | `referenced_tweets[type='retweeted']` |

`to_ref` is **always** written; `to_item_id` resolves only if that post was also ingested. A reply to a protected or deleted post is a permanently unresolved `parent_ref` — legal, expected, and exactly why adjacency-with-a-text-ref is truth and `thread_path` is only an index.

### 5.3 Metrics

`public_metrics` → `metric_observations` with keys `likes`, `quotes`, `bookmarks`, `comments`, `views`, `shares`. `approximate = 0` (X reports exact integers, unlike Reddit's fuzzing and Facebook's `1.2K` rounding). `source_kind = 'live'`.

**Written once, at ingest, and never refreshed** (§3.8). The change-log table handles this naturally — one row per metric per item, no further rows ever. `metrics_current` is set in the same transaction and then frozen.

### 5.4 Bookkeeping

`runs.cost_micros` is X's column. `usage_counters(source='x', period='2026-09', metric='cost_micros')` is X's table. Every other connector leaves both at zero. Together they are the reason a pricing model that changes again is a config edit and a dashboard line, not a surprise on a statement.

---

## 6. ToS position, stated factually

> Not legal advice. Facts and their engineering consequences.

`[verified]` Automated access **through the official API is permitted** and is what the credits are for. Scraping is prohibited, and X has demonstrated in the last week (§1) that it enforces that outside court as well as in it.

`sources.tos_class`:

| Connector | Value | Reason |
|---|---|---|
| `x.archive` | `'official_api'` | it is X's own export feature, used by its owner, for their own data |
| `x.rest` | `'official_api'` | paid, credentialed, within published terms |
| every rejected path in §1 | `'prohibited'` | recorded as a **queryable fact**, not a comment, so it can never be quietly enabled |

`[verified]` There is **no merits precedent** that scraping public X data is unlawful (*Bright Data*, §1). The operative risk is account suspension and repository takedown, not litigation. That distinction matters because it means the mitigation is behavioural, not legal: don't scrape, and there is nothing to enforce against.

**Third-party data.** The X archive contains full DM history with named counterparties. Whatever encryption-at-rest, default-scope, retention and export policy the messaging connectors get **applies to the X archive importer identically** — see [../GOVERNANCE.md](../GOVERNANCE.md). This is the easiest place in the whole project to get it wrong, precisely because X presents as a broadcast source.

---

## 7. Triggers

| Trigger | Action |
|---|---|
| **Enable `x.rest`** | The user confirms, at `console.x.com`: the current per-Post read price, that a spend cap can be set console-side, and the minimum credit purchase. Then set `enabled: true` and the local `spend_cap`. **Both caps, or neither.** |
| **Enable replies / thread reconstruction** | Explicit per-post opt-in with a max-replies bound. Never a global flag. Off by default because it bills every reply, reaches back only 7 days, and its cost scales with other people's engagement. |
| **Enable full-archive search** (`/2/tweets/search/all`) | `[unverified]` availability confirmed at `console.x.com`, **and** the user asks for third-party history beyond the ~3,200 ceiling, **and** accepts $0.005 per post. Docs and vendor blogs directly contradict each other on whether pay-per-usage unlocks it. |
| **Enable media download** | `media_mode='download'` is per-target only, with a mime allowlist and `max_bytes` set at the same time. **Anti-trigger, stated plainly: never enable it globally.** Media is the only unbounded cost in the project. |
| **Re-verify pricing** | Quarterly, and on any `usage_counters` anomaly. Pricing changed in Feb 2023, Feb 2026, and reportedly again around April 2026. Anything written here has a short half-life. |
| **Kill `x.rest` entirely** | A month's actual spend exceeds 3× the estimate with no change in target count. That is a bug or a repricing, and either way the switch goes off while it is diagnosed. |
| **Reconsider any rejected path in §1** | **No trigger exists.** Rejected permanently, with dates, in §1. |

---

## 8. Verify before building

| # | Claim | Confidence | How to settle it | Blocks |
|---|---|---|---|---|
| 1 | **The export ZIP's internal layout** — file names, directory structure, the JS-assignment wrapper | `[unverified]` | **request one archive, `unzip -l` it, read `TheExGenesis/community-archive`'s parser.** ~24h turnaround. | **all of Phase 4a** — this is the biggest unknown in the half that actually ships |
| 2 | Whether pay-per-usage unlocks `/2/tweets/search/all` | `[unverified]` | `console.x.com`. Docs say *"Full-archive search requires Self-serve or Enterprise access"*; a vendor blog (Apr 2026) says pay-per-use is "7-day search window only". **Direct contradiction.** | nothing — the default design needs neither search endpoint |
| 3 | Whether `expansions` bill as separate resources, and per-instance vs per-unique | `[unverified]` | one request with `expansions=author_id`, then **read the credit ledger** | if per-instance, costs roughly triple. Do not enable expansions in the daily job until this is settled. |
| 4 | Whether media objects (`expansions=attachments.media_keys`) are billed | `[unverified]` | credit ledger. No media line item exists in the price list, which *suggests* free-with-the-post — **absence of a line item is not a documented exemption** | media metadata capture |
| 5 | Minimum credit purchase / top-up floor | `[unverified]` | `console.x.com`. Docs specify none; the console may enforce one (e.g. a $10 block) | cash-flow granularity only, not viability |
| 6 | Monthly post-read cap: 3,000,000 (official) vs 2,000,000 (vendors) | official `[verified]`, conflict `[unverified]` | irrelevant at ~250× headroom | nothing — recorded because it proves vendor blogs are unreliable on X pricing |
| 7 | Whether DM Event reads need approval beyond pay-per-usage | `[unverified]` | **moot** — DMs come from the archive (§2.2) | nothing |
| 8 | Current rate-limit values | `[verified]` as of 2026-09-01, but the doc carries no revision date | headers on the first live request | nothing — 4 orders of magnitude of headroom |
| 9 | Current legal status of the Nitter C&D | `[unverified]` | as of 2026-09-01 nitter.net was offline and the maintainer was seeking counsel; no filed lawsuit reported | nothing — the rejection in §1 stands either way |
| 10 | **Every price figure, before flipping the switch** | `[verified]` from docs 2026-09-01 | `console.x.com` is the only authority on what you are billed | **`enabled: true`** |

---

## 9. Known risks

| Risk | Why it matters here |
|---|---|
| **Pricing volatility is the top risk.** Pay-per-usage is seven months old and replaced a model that itself replaced free access in Feb 2023 — three changes in three years, always toward extracting more. | Mitigation: a hard spend cap **in the X console**, not just in config, so a unilateral repricing cannot produce a surprise bill; and `runs.cost_micros` so drift shows up in the data. |
| **A runaway loop costs real money.** Unlike every other connector in this project. | A `next_token` termination bug or a `since_id` reset to zero spends hundreds of dollars overnight. Mitigation: the local counter, a per-run `spend_units` ceiling independent of `--limit`, and the mocked-infinite-cursor test. |
| **Reply ingestion cost is unbounded and demand-driven** — it scales with other people's engagement. | One viral post in a followed timeline can 100× a month's spend. Off by default, per-post opt-in, hard bound. |
| **The ~3,200 ceiling is permanent** for any account other than the user's own, and no money fixes it via the timeline endpoint. | It is a `gaps` row with `filled_by` NULL forever. Say so before someone starts a backfill expecting completeness. |
| **Edits and deletions are undetectable, and metrics are never refreshed.** | The archive is a point-in-time capture, not a mirror. This is a documented property, not a bug — but it must be legible in the data, which is what `envelopes.captured_at` and the one-shot `metric_observations` rows provide. |
| **The DM folder lands in the same tool as the timelines.** | X reads as broadcast right up until it doesn't. `propose_privacy()` returning `CONVERSATION` for `kind='dm'`, and the CHECK constraints behind it, are what stop this being a quiet governance failure. |
| **Concentration on one credential.** | One OAuth app tied to one X account. Losing that account for any reason — including reasons unrelated to this tool — stops ingestion and may strand purchased credits. Low probability on the API path; it is the *scraping* path that makes this likely. |
| **Research recency.** The Nitter C&D is one week old; the pay-per-usage model is seven months old. Both are live situations. | Everything in this document is date-stamped for exactly this reason. A future reader should re-verify rather than trust it indefinitely. |

---

*See also: [./reddit.md](./reddit.md) — the other REST connector, and the instructive contrast: Reddit's governor is a **quota** corrected by response headers; X's is a **spend counter** checked before the run starts. Same `TokenBucket`, same `Budget`, different fields filled. · [../DATA-MODEL.md](../DATA-MODEL.md) · [../ARCHITECTURE.md](../ARCHITECTURE.md) · [../GOVERNANCE.md](../GOVERNANCE.md) for the conversation-class handling the archive importer inherits.*
