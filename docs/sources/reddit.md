# Connector: `reddit`

**Phase 3.** Ships after Facebook (Phase 1) and Telegram (Phase 2). Transport is REST over OAuth2. There is no browser in this connector and no scraping fallback — see [ToS position](#11-tos-position-stated-factually).

> **Verdict.** Reddit is the cheapest connector in the project by a wide margin: a realistic daily run over 25 subreddits costs **~2,600 requests**, unattended, with zero account-ban risk — about 26 minutes against the reported 100 QPM ceiling, ~87 minutes at the 30 QPM this design actually paces at (§8; the ceiling itself is `[likely]`, not verified). The binding constraint is not throughput, it is **credentials**. Self-service OAuth app registration ended in November 2025; every new client goes through manual review. That is why **R0 — apply for a script app — is Phase 0, day one, in parallel with everything else**. It costs one form and it is the highest-variance assumption in the plan.
>
> This connector is also where the core's coverage/gaps machinery finally does real work. The ~1,000-item listing wall becomes a `gaps` row written **by core** from the connector's coverage claim, with the connector contributing zero gap-detection code.

**Confidence convention.** Every factual claim below is tagged `[verified]` (read from a primary source), `[likely]` (multiple consistent secondary sources, no primary), or `[unverified]` (must be confirmed before it is relied on). Nothing is upgraded. Reddit's own policy pages were unreachable to automated fetches during recon (`support.reddithelp.com` → HTTP 403; `redditinc.com` and `web.archive.org` blocked; `reddit.com` off-limits by project rule), so **every quotation from Reddit policy in this document is second-hand.** See §14.

---

## 1. Access path and auth

### 1.1 Transport

| | |
|---|---|
| Base URL | `https://oauth.reddit.com` |
| Auth | OAuth2 bearer, code-grant **refresh token** |
| Every request | `raw_json=1`, always, no exceptions |
| `sources.transport` | `'rest'` at v1 (`'feeds'` only under the degraded path, §12) |
| `sources.tos_class` | `'official_api'` — the only connector in the project that gets this value |
| Browser | never, not even as a fallback |

`raw_json=1` stops Reddit HTML-escaping `&`, `<` and `>` in body text. Without it every re-parse forever has to un-escape, which is exactly the kind of lossy transport artefact raw-first storage exists to avoid. It costs one query parameter.

### 1.2 R0 — the credential spike (Phase 0, day one)

`[likely]` Reddit introduced the **Responsible Builder Policy** in early November 2025 and killed self-service OAuth app creation at the same time. The old flow (prefs → apps → "create app" → instant `client_id`/`client_secret`) is gone. "Create App" is now a submission form routed to manual, ticket-based review. You describe the use case, the data you need, which subreddits you will touch, and expected request volume. **Reddit's stated target is a ~7-day response.**

`[unverified]` Multiple 2026 write-ups report that the dominant outcome for small personal read-only projects is a generic "not compliant or lacked details" decline with no appeal, or silence. Approval rates could not be confirmed from any primary source. Treat the *tone* of those reports as anecdotal; the *mechanism* (manual approval, no self-service) is corroborated by the existence of the official help article `support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy` (title and URL confirmed via search; body returned 403).

`[likely]` Apps and tokens created before November 2025 are grandfathered and still work. If those credentials are ever revoked there is no self-service replacement path.

**R0 is a spike, not an assumption.** Fire it on day one. The answer is then in hand by the time Phase 3 arrives, and the entire fork below costs nothing if it fails.

```
R0 outcome  →  approved   →  build §1.3–§10 as written, sources.transport = 'rest'
            →  declined   →  build §12 instead,        sources.transport = 'feeds'
            →  no answer  →  after the stated ~7-day window + 14 days grace, treat as declined
                             (record the date; re-apply is free and can run in the background)
```

`[likely]` Separately: on **5 August 2026** Reddit posted "Our plans for the future of Reddit's public data API" in r/redditdev, asking every existing app owner to **register their app by 30 September 2026**. That is a registration deadline, not a migration deadline, and no hard cutoff for the classic Data API has been published. The post is reported to say "None of this is changing today. It honestly won't happen this year." **If the user holds a grandfathered app, check this deadline directly — it is this month.** Registering costs nothing and preserves optionality.

**Devvit is not a path for this tool.** Developer Platform apps run on Reddit's infrastructure and are installed to subreddits by moderators. They cannot pull data into a local SQLite file except by making outbound HTTP calls to an endpoint you host, and they require mod install rights on each target subreddit. Named here so it is not proposed later as the "official" answer.

### 1.3 Auth flow — code grant, not password grant

| Flow | Reaches private subs? | Survives 2FA? | Stores a password? | Verdict |
|---|---|---|---|---|
| **Code grant → refresh token** | yes (user context) | **yes** | **no** | **chosen** |
| Password grant ("script" app) | yes (user context) | no, in practice | **yes** | rejected |
| Implicit / installed app | yes | yes | no | rejected — no refresh token, wrong shape for an unattended job |
| Application-only (read-only) | **no** | n/a | no | rejected — no user context, cannot see private subs, subscriptions or saved |

`[verified]` PRAW's own documentation notes on password grant + 2FA: *"For such an app there is little benefit to using 2FA. The token must be refreshed after one hour; therefore, the 2FA secret would have to be stored along with the rest of the credentials in order to generate the token, which defeats the point."* Password grant and 2FA do not coexist cleanly. The refresh-token flow costs one interactive browser authorization at setup and then never again.

`[likely]` **Private subreddits** are reachable only through a user-context token for an account that is already a member. There is no API trick that gets you into a private sub you have not joined — the API returns 403, same as the browser would.

`[unverified]` **Scopes.** The standard answer is `read` (listings and threads) + `mysubreddits` (enumerate your subscriptions) + `history` (your own saved/upvoted). PRAW's authentication docs do not enumerate this. **Confirm empirically on the first token** and record the working set in `accounts.extra`.

### 1.4 Secrets

Keychain, via `core.secret()`, resolution order env → Keychain → loud failure printing the literal `security add-generic-password` command. Never a file fallback.

```
service                                 account          rotates?
vn.moonbase.crawler-social.reddit       client_id        no
vn.moonbase.crawler-social.reddit       client_secret    no
vn.moonbase.crawler-social.reddit       refresh_token    on revocation only
```

Reddit is the easy case: nothing here rotates per-use (contrast Zalo's single-use refresh token) and nothing needs a 0600 file outside the worktree (contrast the Telegram `.session` and the Chrome profile). One `accounts` row, `auth_kind = 'oauth2'`, `credential_ref` = the Keychain service name, never the token.

`login()` mints the refresh token interactively and is **only** ever called by `crawler login --source reddit`. A scheduled run with a missing or dead refresh token fails loudly with exit **78** (`EXIT_CONFIG`) and never blocks on stdin.

---

## 2. Client library

`[verified]` Read from the PyPI JSON API, not from memory. Dates are upload dates.

| Package | Latest | Uploaded | `requires_python` | Role here |
|---|---|---|---|---|
| **`prawcore`** | **4.0.0** | 2026-06-13 | `>=3.10` | **auth only** — token minting + refresh |
| **`httpx`** | **0.28.1** | 2024-12-06 | `>=3.8` | **the wire** — every data request |
| `praw` | 8.0.3 | 2026-08-12 | `>=3.10` | **dev-only**, never in the ingest path |
| `asyncpraw` | 8.0.3 | 2026-08-12 | `>=3.10` | not used |

**Maintenance status: healthy.** prawcore went 3.0.2 (Feb 2025) → 3.1.0 → 3.2.0 → 3.2.1 → 4.0.0, all within June 2026. PRAW went 7.7.1 → 8.0.0 (2026-06-14) → 8.0.3 (2026-08-12). This is an actively maintained line, not a project coasting. httpx 0.28.1 has been the head release for ~21 months — that is maturity, not abandonment; it is the most widely deployed async HTTP client in Python and the API has stabilised.

### 2.1 Why PRAW is banned from the ingest path

PRAW parses responses into model objects and **does not hand you the response bytes**. Raw-first storage is a hard project requirement (`envelopes.body` must hold verbatim transport bytes so a parser rewrite can be replayed without re-fetching). Storing a re-serialised PRAW model dict and calling it "raw" would break the premise outright. `prawcore` is the same maintained codebase minus the model layer: it gives you token minting, refresh and rate-limit header plumbing, and leaves the bytes alone.

PRAW stays installed in the `dev` dependency group as an interactive spelunking tool (`uv run python -c "import praw; ..."` to eyeball a field before writing a parser for it). A test asserts `'praw' not in sys.modules` after a `crawler tick --source reddit` — the same mechanical enforcement the project uses for `'selenium' not in sys.modules`.

`[verified]` If you do use PRAW interactively, note **8.0.0 made all positional args keyword-only** (`subreddit.top(time_filter="all")`), added `created_datetime`/`edited_datetime`/`updated_datetime` tz-aware properties and `py.typed`, dropped Python 3.8–3.9, and **removed** `APIException`, `token_manager`, award/gild methods and `random_subreddit`.

### 2.2 The dependency cost, stated honestly

`[verified]` **prawcore 4.0.0 declares exactly one runtime dependency: `requests<3.0,>=2.34.2`.**

So the `reddit` dependency group pulls in **two HTTP stacks**: `requests` (transitively, used only by prawcore's `Requestor` to hit the token endpoint) and `httpx` (used for every data request). That is the real, unglamorous cost of the frozen "prawcore for auth + httpx for the wire" decision. It is acceptable — token minting happens once an hour at most and `requests` is inert the rest of the time — but it should be a known fact rather than a surprise in the lockfile.

**Non-blocking note, not a re-opened decision:** the token endpoint is a single `POST /api/v1/access_token` with HTTP Basic auth and `grant_type=refresh_token`. Roughly 30 lines of httpx would remove `prawcore` and `requests` both. The frozen design chose prawcore because it already handles refresh timing and 429 correctly and hand-rolling OAuth is a classic own-goal. If the double stack ever causes an actual conflict in `uv.lock`, that is the escape hatch — and the trigger for it is a real conflict, not aesthetics.

### 2.3 Sketch — illustrative, not a file to write

```python
# connectors/reddit/wire.py -- ILLUSTRATIVE
from __future__ import annotations
import prawcore, httpx

BASE = "https://oauth.reddit.com"
UA   = "macos:vn.moonbase.crawler-social:0.1 (by /u/<username>)"

class Wire:
    """Owns the bytes. prawcore owns only the bearer token."""
    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        auth = prawcore.TrustedAuthenticator(
            prawcore.Requestor(UA), client_id, client_secret)
        self._authz = prawcore.Authorizer(auth, refresh_token=refresh_token)
        self._c = httpx.Client(timeout=30.0, headers={"User-Agent": UA})

    def get(self, path: str, **params) -> tuple[bytes, httpx.Headers, int]:
        if not self._authz.is_valid():
            self._authz.refresh()
        r = self._c.get(f"{BASE}{path}",
                        params={**params, "raw_json": 1},
                        headers={"Authorization": f"bearer {self._authz.access_token}"})
        return r.content, r.headers, r.status_code   # bytes are what lands in envelopes.body
```

`fetch()` yields an `Envelope` wrapping `r.content` verbatim. It does not call `json.loads`. Parsing happens in `parse()`, which never sees a credential — enforced by `test_parse_needs_no_secrets`, which constructs the connector with `secrets={}` and parses every fixture.

---

## 3. What is reachable, and what is not

| Reachable | Endpoint | Notes |
|---|---|---|
| Public subreddit listings | `/r/<sub>/new`, `/hot`, `/top`, `/rising` | 100/page, **1,000 total** (§4) |
| Private subreddit you are a member of | same | needs user-context token `[likely]` |
| Comment trees | `/comments/<id>` + `/api/morechildren` | cost model in §5 |
| Batch metadata refresh | `/api/info?id=t3_a,t3_b,…` | ~100 fullnames/call `[likely]` |
| Your own subscriptions | `/subreddits/mine/subscriber` | needs `mysubreddits` scope `[unverified]` |
| Your own saved / upvoted | `/user/<me>/saved` | needs `history` scope `[unverified]`; **privacy: `joined`, not `broadcast`** |
| Subreddit search | `/r/<sub>/search` | inherits the 1,000 ceiling |
| `upvote_ratio`, `edited`, `removed_by_category`, flair | on the submission object | **not available from any non-API surface** |

| Not reachable | Why |
|---|---|
| A private subreddit you have not joined | 403; the API is not a bypass |
| Anything beyond ~1,000 items in a listing | hard cap, no page 11 (§4) |
| Reliable historical backfill from Reddit itself | cloudsearch time-slicing is not dependable (§4.2); Arctic Shift instead (§6) |
| Reddit chat / DMs | out of scope; the Data API does not serve them and this project has no reason to want them |
| Per-user vote history of other people | never exposed |
| Unauthenticated anything | `.json` returns 403 since ~28 May 2026 `[likely]` |

**Reddit produces `broadcast` containers, with one exception.** `propose_privacy()` returns `BROADCAST` for a public subreddit, `JOINED` for a private/restricted subreddit the account is a member of, and `JOINED` for the account's own saved/upvoted listings. It never returns `CONVERSATION`, so **the Reddit connector never writes to `private.db`** and `Cap.CONVERSATIONS` is not set. If a subreddit's privacy cannot be determined from the payload, `propose_privacy()` fails closed to `CONVERSATION` per the contract, which will route it to the encrypted store and require `ack_third_party` — a loud, wrong-looking outcome that is exactly the right failure mode for an unclassifiable target.

---

## 4. The 1,000-item listing cap

### 4.1 What it is

`[likely]` Reddit listings page at **max 100 items per request, max ~1,000 items total** via `after`/`before` fullname cursors. Past 1,000 deep the cursor stops returning entries. There is no page 11. This applies uniformly to `/new`, `/hot`, `/top`, `/r/<sub>/search`, user history, saved and upvoted. `Subreddit.search()` inherits the same ceiling (10 pages × 100).

### 4.2 What does not work around it

`[unverified]` The classic workaround was CloudSearch time-slicing — `syntax=cloudsearch&q=timestamp:1373932800..1474019200` — splitting a query into windows each under 1,000 results. PRAW still exposes `syntax="cloudsearch"|"lucene"|"plain"` on `Subreddit.search()`, but PRAW's default was changed to `lucene` "so that PRAW's default is aligned with Reddit's default", and **PRAW keeping the parameter proves nothing about the server.** Reddit's search backend was migrated off CloudSearch years ago and no 2026 confirmation that `timestamp:` ranges still function could be found.

**Decision: do not build backfill on cloudsearch.** Treat it as a 10-minute experiment the user runs once against a real credential. If `timestamp:` ranges work it is a bonus fast path and nothing depends on it. The load-bearing backfill plan is Arctic Shift (§6).

### 4.3 When it actually bites

The cap only matters when a subreddit produces **more than ~1,000 posts between polls**. At twice-daily cadence that means >1,000 posts per 12 hours — r/AskReddit territory. For "tens of subreddits, daily", the cap is a **gap-recovery** concern (what happens after a week of downtime, or after a laptop lid stays closed), not a steady-state one.

This matters operationally because of a launchd property: **launchd defers a missed `StartCalendarInterval` until wake and coalesces multiple misses into one invocation.** A week away yields *one* run, not seven. So the cap and the scheduler interact directly, and the connector must catch up by cursor and never by "fetch yesterday".

### 4.4 Consequences for backfill — the honest version

| Situation | Outcome |
|---|---|
| Steady daily polling, normal sub | 1–2 listing pages, cap never touched, `Coverage(EXACT)` |
| Resume after 3 days, busy sub | may exceed 1,000 → `Coverage(PARTIAL)` → **core writes a `gaps` row** |
| First enrolment, `--since 90d` | almost certainly hits the cap immediately on any active sub |
| "Give me this subreddit's whole history" | **not possible from Reddit.** Arctic Shift or nothing. |

### 4.5 How the cap becomes one `gaps` row instead of a bespoke bug

This is the point of the coverage contract. The connector makes an honest claim per envelope; core does the bookkeeping.

```python
# connectors/reddit/fetch.py -- ILLUSTRATIVE
# Normal page: we saw every post between the oldest and newest item on it.
Coverage(kind=COVER_EXACT, axis=AXIS_TIME,
         lo=oldest_created_utc, hi=newest_created_utc)

# Page 10 reached and the oldest item is STILL newer than the durable watermark:
# we did not close the loop, and we know it.
Coverage(kind=COVER_PARTIAL, axis=AXIS_TIME,
         lo=watermark_created_utc, hi=oldest_created_utc_on_page_10,
         withheld=0, note="listing_cap")
```

Core, on commit, writes:

```sql
INSERT INTO gaps(target_id, axis, lo, hi, reason, opened_at, opened_by_envelope)
VALUES (:t, 'published_at', :watermark, :oldest_on_page_10, 'listing_cap', :now, :seq);
```

and — critically — **does not advance `complete_since`.** The gap-drain job later fills it from Arctic Shift and sets `filled_by='arctic_shift'`, `filled_at`. A run that silently skips a week while reporting success is the worst failure mode this tool has, because it is invisible until analysis time. A `gaps` row makes it visible in `crawler status`.

The connector contributes **zero** gap-detection code. It only tells the truth about what its bytes contain.

### 4.6 What the user sees

`capabilities_note()` renders free text that no enum can carry, and core never branches on it:

> `listings hard-stop at ~1000 items via 'after' fullnames; there is no page 11. Backfill beyond that comes from Arctic Shift (archive, ~36h stale metrics), not from Reddit. Deep history for a specific subreddit: pull the monthly .zst dump.`

`Cap.BACKFILL_CAPPED` is set, not `Cap.BACKFILL` — and **the capped flag implies the plain one for CLI argument validation**, so `crawler crawl --source reddit --target r/vietnam --since 2y` is accepted, prints the ceiling and proceeds rather than being rejected at argument parsing or silently returning a truncated archive. Command spellings are normative in [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §11.

---

## 5. Comment-tree expansion cost

`[verified]` Read directly from PRAW's source (`praw/models/comment_forest.py`, `praw/models/reddit/more.py`, `praw/models/reddit/submission.py` on `main`).

### 5.1 The mechanics

- Initial fetch: `GET /comments/<id>` with `{"limit": comment_limit, "sort": comment_sort}`. PRAW defaults `comment_limit = 2048`, `comment_sort = "confidence"`. Everything not rendered collapses into `MoreComments` nodes carrying `count` (hidden total) and `children` (ids).
- `replace_more(limit=32, threshold=0)` — docstring, verbatim: **"Each replacement requires 1 API request"** and **"each replacement will discover at most 100 new Comment instances"**.
- It pops the **largest** `MoreComments` first (a heapq inverted into a max-heap), so a bounded budget spends itself on the biggest hidden subtrees. Good default behaviour; replicate it.
- Each replacement is `POST /api/morechildren` with `children=<comma-joined ids>`, `link_id=<t3_…>`, `sort=…`.
- `count == 0` means a "continue this thread" link; PRAW re-fetches that comment's permalink instead.
- `replace_more` raises `prawcore.TooManyRequests` if used concurrently, and `DuplicateReplaceException` if called twice after a refresh.

### 5.2 The cost model

```
requests(thread) ≈ 1 + ceil((N - rendered_initial) / 100)
```

| Thread size | Reddit requests | Arctic Shift requests | % of a 10-min budget (Reddit) |
|---|---|---|---|
| 40 comments | 1 | 1 | 0.1% |
| 700 comments | ~7 | 1 | 0.7% |
| 2,000 comments | ~20 | 1 | 2% |
| 5,000 comments (megathread) | **~50** | **1** | **5%** |
| 20,000 comments | ~200 | 1 | 20% |

### 5.3 The frozen policy

| Knob | Value | Reason |
|---|---|---|
| `MORE_BUDGET` | **32 `morechildren` calls per post** | matches PRAW's own default; bounds the megathread blast radius |
| Order | descending `count` | biggest hidden subtree first, so a truncated budget loses the least |
| Record | `items.more_remaining` = sum of unexpanded `count` | **a partial tree that says it is partial is a correct result; a silently truncated one is a bug** |
| Deep/old route | >1,500 comments **or** older than 7 days → Arctic Shift | 1 request instead of ~15–50, at the cost of ~36h-stale scores |
| Comment refetch trigger | post is new, **or** `num_comments` moved by > `COMMENT_DELTA` (default **5**) since the last comment fetch | avoids re-walking static threads daily |

`more_remaining > 0` on an item is a first-class declared state, queryable and reportable — the same principle as the `gaps` table applied one level down.

`[unverified]` Whether `/api/morechildren` still returns nested `more` placeholders in 2026, and its current per-request child cap. The design handles either behaviour because unresolved children are written with `parent_ref` set and `parent_id` NULL, and the parent-repair pass resolves them later — see §10.3.

---

## 6. Historical data — Arctic Shift, not Pushshift

### 6.1 The landscape

| Source | Status | Use here |
|---|---|---|
| **Pushshift** | `[verified]` public access revoked May 2023; moderators only | **none. Do not design around it.** |
| **Arctic Shift** | `[verified]` current, dumps 2005-06 → 2026-07 | **the backfill plan** |
| PullPush.io | `[likely]` ~30 req/min, documented reliability problems and post-2023 coverage gaps | cross-check only, never a foundation |
| Academic Torrents / Watchful1 dumps | `[likely]` exist | no advantage over Arctic Shift at personal scale |

### 6.2 Arctic Shift, verified

`[verified]` From the repo and its API README.

- **Bulk dumps** cover **2005-06 → 2026-07**, monthly releases since 2024-04, `.zst` newline-delimited JSON, plus a single-torrent 2005-06 → 2025-12 bundle and per-subreddit subsets. Helper scripts in-repo need only `zstandard` and Python ≥3.10.
- **HTTP API** at `https://arctic-shift.photon-reddit.com`, **no auth**.

| Endpoint | Params that matter | Limit |
|---|---|---|
| `GET /api/posts/search`, `/api/comments/search` | `subreddit`, `author`, `after`, `before` (epoch s/ms, ISO-8601, or relative `7d`), `sort=asc\|desc` by `created_utc`, `fields=`, `format=json\|rss\|jsonfeed` | `limit` 1–100 or `"auto"` (100–1000 by server load) |
| `GET /api/comments/tree` | `link_id` (required), `parent_id`, `start_breadth`, `start_depth` | `limit` 1–**25000**, default 50 — **full tree in one call** |
| `GET /api/posts/ids`, `/api/comments/ids` | `ids` | **max 500 per call** |
| `GET /api/time_series` | `key=r/<sub>/posts/count`, `precision=day`, `after`, `before` | cheap coverage/QA signal |
| `GET /api/posts/search/aggregate` | `aggregate`, `frequency`, `min_count` | coverage QA |

- **Rate limits are dynamic, not a fixed number.** Verbatim: *"If you're a normal user and only make a couple requests per second, you have nothing to worry about."* Excessive requests may be limited; limits are *"calculated dynamically based on server load and request complexity."* 429 returns `X-RateLimit-Reset` and `X-RateLimit-Reset-At`. The docs say explicitly: **"If you want to process massive amounts of data, use the monthly dumps instead."**
- **The metric-lag caveat, verbatim:** *"After roughly 36 hours, all data is updated and should then be the same as that released in .zst dumps. Before that, `score`, `num_comments`, etc. will be 1 or 0, since data is archived initially the moment it was posted."*

Arctic Shift is an **ingest-at-creation archive with a delayed metric backfill**. Its scores are a ~36h-old snapshot, not live, and freshly-posted items carry 0/1.

### 6.3 The schema consequence, and why it is not optional

Every metric row sourced from Arctic Shift is written with `metric_observations.source_kind = 'archive'`. Mixing archive-sourced and live-sourced metrics in one series **without** that column silently corrupts the time series — a 36h-stale value looks like a real observation and produces a phantom dip. This is why `source_kind` is in the frozen DDL rather than being added when someone notices.

Items whose content came from the archive are still ordinary `items` rows. Provenance is the envelope: `envelopes.kind = 'reddit.arctic.search' | 'reddit.arctic.tree'`, `envelopes.meta.lib = 'arctic_shift'`, joined through `item_envelopes` with `role='primary'`.

### 6.4 Operational posture

`[verified]` Arctic Shift is **one volunteer's donation-funded free service with no uptime or performance guarantee**, and this design gives it a load-bearing backfill role. Mitigations, in order:

1. **The dumps are the artifact; the API is convenience.** *Arctic Shift bulk `.zst` dumps pulled to local disk* is a deferred item in [../../PLAN.md](../../PLAN.md) §11 with a stated trigger: **an unfilled `gaps` row older than 30 days, or Arctic Shift's API becoming unreliable.** Honour it.
2. `[unverified]` **Spot-check coverage before trusting it for a specific subreddit** — `GET /api/time_series?key=r/<sub>/posts/count&precision=day` against your own item counts.
3. Never let a Reddit gap-drain failure fail the Reddit tick. It is a separate run (`runs.mode = 'backfill'`) and its failure is `RETRY`, not `STOP`.

---

## 7. Cursor and incremental sync

### 7.1 Two cursors, two storage locations — this is the whole design

| | Durable watermark | Ephemeral page token |
|---|---|---|
| What | `created_utc` of the newest post confirmed stored | Reddit `after=t3_…` fullname |
| Survives deletion of the item it points at? | **yes** — it is a timestamp | **no** — a fullname whose post was deleted breaks the cursor |
| Lives in | `cursors` (`kind='watermark'`, `axis='published_at'`, `axis_value=created_utc`) | **`runs.params` — never `cursors`** |
| TTL | none | valid only within one paging session |

The frozen `CHECK (kind <> 'page' OR expires_at IS NOT NULL)` on `cursors` is the belt-and-braces guard. A page token persisted forever is the bug that silently breaks daily runs months later, and Reddit's `after` is the canonical example: it is the *only* pagination token Reddit accepts, and it is *opaque and mortal*.

`cursors.value` carries the connector's own JSON shape and **core never parses it**:

```json
{"newest_created": 1756684800, "newest_fullname": "t3_1abcdef", "complete_since": 1748908800}
```

`cursors.axis_value` mirrors `newest_created` as an integer so core can compare it for gap detection without opening the JSON.

### 7.2 Why not a fullname-only or base36-ordinal cursor

Reddit ids are base36 and monotonic, so `int(id, 36)` would be a usable ordinal. It is **not** used, for two reasons: it is globally monotonic, not per-container, so it is not the `source_seq` the schema means; and it fails under deletion exactly like the raw fullname does. `created_utc` is the axis the `gaps` table already uses and the axis `local_day` derives from. One axis, everywhere. `items.source_seq` stays **NULL** for Reddit.

### 7.3 The daily tick, step by step

Assume one enrolled target = one subreddit.

1. Read `cursors` → `newest_created`, `newest_fullname`, `complete_since`. First run: all NULL.
2. `GET /r/<sub>/new?limit=100&raw_json=1` (+ `after=` from `runs.params` when continuing).
3. Yield **one `Envelope`** per response: verbatim bytes, `kind='reddit.listing'`, `content_type='application/json'`, `coverage=Coverage(EXACT, lo=oldest, hi=newest)`, `cursor_after=Cursor(WATERMARK, …)` on the last page only. **Core commits the envelope, then the cursor, in one transaction.** `fetch()` never parses and never touches the DB.
4. **Stop condition:** stop paging when the oldest post on the page has `created_utc < newest_created - OVERLAP`, with **`OVERLAP` = 6 hours**. Cheap insurance against clock skew, sticky posts, and remove-then-reinstate. Otherwise page on with `after=<last fullname>`, up to the hard 10-page wall.
5. **Cap detection:** page 10 reached and the oldest item is still newer than `newest_created` → emit `Coverage(PARTIAL, …, note="listing_cap")`. Core writes the `gaps` row. Do not advance `complete_since`.
6. **Comments:** for each post that is new, or whose `num_comments` moved by more than `COMMENT_DELTA`, `GET /comments/<id>?limit=500&sort=new&raw_json=1` → `kind='reddit.comments'`.
7. **Bounded expansion:** `POST /api/morechildren`, descending `count`, at most 32 calls → `kind='reddit.morechildren'`. Record `more_remaining`.
8. **Deep/old threads** skip 6–7 and go to Arctic Shift `GET /api/comments/tree?link_id=t3_<id>&limit=9999` → `kind='reddit.arctic.tree'`, metrics tagged `source_kind='archive'`.
9. **Metric re-observation:** batch due items from `metric_schedule` through `GET /api/info?id=…` → `kind='reddit.info'`.
10. **Absence sweep** (core-owned, not connector code): see §11.2.
11. Advance the watermark; advance `complete_since` **only if no gap was recorded this run**.

`--limit N` bounds items and **does not advance the durable watermark** — `runs.mode='limit'` records it as a distinct run kind. A sampled run must never poison an incremental cursor.

### 7.4 SUSPECT — the failure that looks like success

Both core rules fire on Reddit and each catches what the other cannot:

- **(a)** claimed `EXACT` over a non-empty interval with `found == 0`. Fires on **run one**. Catches a Reddit auth-error page returned with a 200-shaped body, and an empty listing from a sub that just went private.
- **(b)** zero items from a target that produced items in each of its last three runs. Catches what (a) cannot see — a genuinely-empty-looking response with a valid coverage claim.

Either rolls back the cursor write and sets `runs.status='suspect'`. Two consecutive escalate to `HUMAN`. Rule (b) is blind on a brand-new target and for three runs after a reparse, which is exactly why both exist.

### 7.5 Capability flags

```python
caps = (Cap.BACKFILL_CAPPED      # implies BACKFILL for CLI argument validation
        | Cap.NEEDS_HUMAN_LOGIN   # `crawler login --source reddit` mints the refresh token
        | Cap.COMMENT_TREE
        | Cap.MUTABLE_METRICS
        | Cap.PARALLEL_TARGETS)
```

Not set, deliberately:

| Flag | Why not |
|---|---|
| `Cap.BACKFILL` | the 1,000 wall — `BACKFILL_CAPPED` is the honest value |
| `Cap.NEEDS_SESSION`, `NEEDS_GUI`, `SINGLE_FLIGHT` | stateless HTTP; nothing to serialise, no GUI, no file lock |
| **`Cap.EXACT_CURSOR`** | **not set, deliberately.** The flag means *resume needs no overlap re-read at all* — and this connector keeps a **6-hour overlap** on purpose (§7.3 step 4) as cheap insurance against clock skew, sticky posts and remove-then-reinstate. An earlier draft set the flag and redefined it locally to mean "no *unbounded* re-read", which would have had core skip the very window the design relies on. **The coverage claim is a separate axis:** listing envelopes still claim `Coverage(EXACT)` on their own honesty about what their bytes contain, and that is unaffected |
| `Cap.FILE_IMPORT` | no archive ZIP. *(There is no `Cap.PUSH` in the enum at all — ARCHITECTURE §6.)* |
| `Cap.DELETE_EVENTS` | Reddit reports no deletion events — **the absence sweep stays mandatory** |
| `Cap.BILLED` | free tier; nothing to meter in `usage_counters` |
| `Cap.CONVERSATIONS` | never writes `private.db` (§3) |

`Cap.PARALLEL_TARGETS` is set — subreddits are independent and there is no session to contend for — but the token bucket is shared across them, so parallelism buys latency, not throughput.

---

## 8. Rate-limit budget

### 8.1 The published numbers

`[likely]` Consistently reported across independent 2026 sources; **never seen in Reddit's own words.**

| | Value |
|---|---|
| OAuth-authenticated | **~100 QPM per `client_id`**, metered as a rolling ~10-minute average (~1,000 req / 10 min) |
| Bursts | short bursts above 100 QPM tolerated; sustained overage is not |
| Unauthenticated | ~10 QPM historically → effectively zero since May 2026 |
| Over-limit | HTTP 429 with `Retry-After` |
| Headers on every response | `X-Ratelimit-Used`, `X-Ratelimit-Remaining`, `X-Ratelimit-Reset` (seconds to period end) |

`[likely]` A free non-commercial tier still exists (personal projects, bots, mod tools, academic research) at that ceiling. What triggers paid is **commercial use** — reselling or serving Reddit data to third parties, or sustained volume above the free ceiling. `[unverified]` Reported commercial rate $0.24 per 1,000 calls with contract minimums around $12,000 (sources disagree on whether that is monthly for ~50M calls or an annual floor). **Reddit publishes no rate card.** Irrelevant to a personal tool; recorded so nobody quotes it as fact.

### 8.2 The config, and the feedback loop

```yaml
rate:
  reddit:
    mode: quota                # a comment for humans. NO CODE BRANCHES ON THIS.
    buckets:
      default: { capacity: 30, refill_per_sec: 0.5 }   # 30 QPM sustained = 30% of ceiling
    respect_headers: [x-ratelimit-remaining, x-ratelimit-reset]
    on_429: honor_retry_after
    on_429_then: halve_rate_for_rest_of_run
```

**Target 30 QPM sustained — 30% of the reported budget.** The configured number is a *prior*; the headers are the *observation*. After each response core drains the bucket to match reality when reality is stingier:

```
if X-Ratelimit-Remaining < 0.20 * (Used + Remaining):
    stretch interval to  Reset / max(Remaining, 1)
```

This is why getting the 100-vs-60 QPM question wrong costs nothing — it is self-correcting on the first response. Contrast Facebook, which publishes no headers at all and therefore gets a paranoia budget with no feedback possible. Same `TokenBucket`, same `Budget`, different constants and a different source of truth. Zero code branches on `mode`.

### 8.3 The worked daily budget

25 subreddits, ~40 new posts/day each (~1,000 new posts/day), 3,000 posts in the active metric-tracking window.

| Step | Requests/day | Driver |
|---|---|---|
| Listing pages (25 subs × 1–2 pages) | ~40 | subreddit count |
| Comment fetch (1,000 new posts) | ~1,000 | **new-post volume — the dominant term** |
| `morechildren` (avg ~1.5 calls/post) | ~1,500 | thread depth |
| Metric re-observation (3,000 tracked, batched 100/call) | ~30 | tracked-item count ÷ 100 |
| Deletion / absence sweep (batched through `/api/info`) | ~30 | rescan window size ÷ 100 |
| **Total** | **~2,600** | |

| At | Wall clock | % of ceiling |
|---|---|---|
| 100 QPM (the ceiling) | 26 min | 100% |
| **30 QPM (chosen)** | **~87 min** | **30%** |

87 minutes unattended, twice a day if you want it, on a laptop that is awake anyway. **Reddit is the cheapest connector in the project.**

**Sensitivity, because the shape matters more than the total:** comments plus `morechildren` are ~96% of the budget. Halving the subreddit count barely moves it; halving `MORE_BUDGET` or raising `COMMENT_DELTA` moves it a lot. If the budget ever needs cutting, cut comment expansion — not targets.

Batching is what makes steps 4 and 5 free: `[likely]` `/api/info` takes **~100 fullnames per call**, so 3,000 tracked posts cost ~30 requests, not 3,000. `[unverified]` The exact cap is widely cited as 100 but was not confirmed from a Reddit primary source. If it is lower, those two rows scale proportionally and the total stays small.

### 8.4 The `Budget` object

```python
Budget(
    max_items    = args.limit,          # --limit N; None on a daily tick
    max_requests = 4000,                # hard ceiling per run, well above the 2,600 estimate
    deadline_ts  = None,                # Reddit has no session-length problem (contrast Facebook)
    spend_units  = None,                # Reddit costs no money (contrast X)
    bucket       = Bucket(capacity=30, refill_per_sec=0.5),
)
```

Three of the five fields are `None`. That is the point of a single `Budget` type: Facebook fills `deadline_ts`, X fills `spend_units`, Reddit fills neither, and none of them needs a bespoke pacing object.

---

## 9. Error and failure mapping

`classify(exc) -> Verdict` is **pure and fixture-testable**. Fixtures live in `tests/fixtures/reddit/` and are captured by `crawler capture`, never hand-written.

| Condition | Verdict | `retry_after` | `runs.status` | Exit | Notes |
|---|---|---|---|---|---|
| 200, items parsed | `OK` | — | `ok` | 0 | |
| Connection reset, DNS, timeout, 500/502/503/504 | `RETRY` | — | `error` | 69 | jittered backoff, retry this envelope |
| **429** | `WAIT` | `Retry-After` header, else `X-Ratelimit-Reset` | `rate_limited` | 75 | honour the header **literally**; halve the target rate for the rest of the run |
| `X-Ratelimit-Remaining` → 0 without a 429 | `WAIT` | `X-Ratelimit-Reset` | `rate_limited` | 75 | pre-emptive; checkpoint and let the next tick resume |
| 401 after a successful refresh | `HUMAN` | — | `needs_human` | 77 | refresh token revoked → `crawler login --source reddit` |
| 400 `invalid_grant` on refresh | `HUMAN` | — | `needs_human` | 77 | same |
| Missing `refresh_token` in Keychain | `HUMAN` | — | `needs_human` | 78 | config error; print the `security add-generic-password` line |
| 403 on a **target** (sub went private, banned from sub) | `DROP` (that target) | — | `blocked` | 0 | set `containers.access_state='no_access'`; **suppresses the absence sweep for that container** |
| 403 sitewide / account suspended | `STOP` | — | `blocked` | 86 | `sources.state='stopped'`; **never auto-retries** until `crawler resume --source reddit` |
| 404 on a permalink / `SUBREDDIT_NOEXIST` | `DROP` | — | `ok` | 0 | mark dead, advance past it |
| Item absent from `/api/info` response | *not an error* | — | — | — | feeds `absence_streak`; see §11.2 |
| 200 with zero items, coverage claimed `EXACT` | `SUSPECT` (core-detected) | — | `suspect` | 0 | connector **never raises this**; core rolls back the cursor |
| Arctic Shift 429 | `WAIT` | `X-RateLimit-Reset` | `rate_limited` | 75 | separate run; failure never fails the Reddit tick |
| Arctic Shift 5xx / unreachable | `RETRY` | — | `error` | 69 | gap stays open; `crawler status` shows it |

`DISARMING = {77, 78, 86}`. A `STOP` writes `sources.state='stopped'` and every subsequent scheduled run becomes an immediate no-op exit 0 — launchd keeps firing on time and nothing happens. That is deliberate: a retry loop is how a temporary block becomes a permanent one.

**The Reddit-specific subtlety worth writing a test for:** a 403 on one subreddit and a 403 sitewide arrive at the same HTTP status. Distinguish them by whether *other* targets in the same run succeeded. If every target 403s, it is the account; if one does, it is the sub. Getting this backwards either disarms the whole connector over one banned sub, or hammers a suspended account.

---

## 10. Mapping onto the unified schema

Full DDL in [../DATA-MODEL.md](../DATA-MODEL.md). Everything below lands in **`data/social.db`** (plain SQLite) because `propose_privacy()` never returns `CONVERSATION` (§3).

### 10.1 Containers and targets

| Column | Value | Note |
|---|---|---|
| `containers.source` | `'reddit'` | |
| `containers.platform_container_id` | `'r/vietnam'`, lowercased | **must be derivable purely from the spec string** — `resolve()` is pure, no network, so it cannot know the `t5_` fullname |
| `containers.kind` | `'subreddit'` | |
| `containers.shape` | `'feed'` | never `'conversation'` |
| `containers.privacy` | `'broadcast'` / `'joined'` | from the payload's `subreddit_type` |
| `containers.privacy_source` | `'connector'` | config may raise, never lower |
| `containers.handle` | `'r/vietnam'` | |
| `containers.url` | `https://www.reddit.com/r/vietnam/` | stored, never fetched |
| `containers.extra` | `{"t5": "t5_2qh1p", "subreddit_type": "public", "over18": false}` | the `t5_` fullname is cached here on first sight |
| `containers.access_state` | `'ok'` → `'no_access'` on a target 403 | **gates the absence sweep** |
| `targets.enrolled_at` | now | no ingest floor semantics for broadcast; forward-only matters for conversations |
| `targets.backfill_from` | explicit only | default first crawl is `--since 90d`, not "everything" |

### 10.2 Items

| Column | Submission | Comment |
|---|---|---|
| `platform_item_id` | **`t3_1abcdef`** (the fullname, with prefix) | **`t1_jn24wv6`** |
| `item_type` | `'reddit.submission'` | `'reddit.comment'` — **provenance only; core never branches on it** |
| `parent_ref` | NULL | `parent_id` verbatim (`t3_…` for top-level, `t1_…` for a reply) |
| `root_ref` | NULL | `link_id` (`t3_…`) |
| `published_at` | `created_utc` | `created_utc` |
| `published_prec` | `'exact'` | `'exact'` |
| `edited_at` | `edited` when truthy | same |
| `title` | `title` | NULL |
| `text` | `selftext` (may be `''`) | `body` |
| `url` | `url` when it is a link post; NULL for self-posts | NULL |
| `permalink` | `https://www.reddit.com` + `permalink` | same |
| `is_pinned` | `stickied` | `stickied` |
| `is_from_self` | `author == <my username>` | same |
| `source_seq` | **NULL** | **NULL** (§7.2) |
| `more_remaining` | sum of unexpanded `more.count` | 0 |
| `extra` (jsonb) | `{"link_flair_text", "subreddit_type", "over_18", "spoiler", "num_crossposts", "is_self", "distinguished", "removed_by_category"}` | `{"distinguished", "controversiality", "is_submitter"}` |
| `local_day` | generated, STORED, `+7 hours` | same |

**Storing the fullname with its prefix in `platform_item_id` is a deliberate decision and it removes a whole class of bug.** Reddit's `parent_id` is *already* a fullname, so `parent_ref` matches `platform_item_id` with no string surgery, and the parent-repair `UPDATE` in the frozen schema works verbatim with no Reddit special case:

```sql
UPDATE items SET parent_id = (SELECT p.id FROM items p
  WHERE p.container_id = items.container_id AND p.platform_item_id = items.parent_ref)
 WHERE parent_id IS NULL AND parent_ref IS NOT NULL;
```

**Promotion path if a Reddit field becomes hot** — one line, no detail table, no migration of existing rows:

```sql
ALTER TABLE items ADD COLUMN flair TEXT
  GENERATED ALWAYS AS (extra ->> '$.link_flair_text') VIRTUAL;
CREATE INDEX idx_items_flair ON items(flair) WHERE flair IS NOT NULL;
```

Only `VIRTUAL` can be added later; `STORED` cannot. Nothing Reddit needs is STORED, so this stays available.

### 10.3 Threading

`parent_id` (resolved) is truth. `parent_ref` is **always** written even when unresolvable. `thread_path` / `root_id` / `depth` are derived and **may be NULL** — correctness falls back to the recursive CTE, never to the path column.

Reddit is the source that makes this non-negotiable: **`morechildren` returns comments whose ancestors you have not loaded.** Under a path-only design those rows are unwritable. Here they land with `parent_id NULL`, `parent_ref='t1_b3'`, `thread_path NULL`, get found by `idx_items_dangling`, and are repaired on the next pass.

Deleted-parent case: if the ancestor was itself deleted and never ingested, `parent_ref` stays unresolved **forever**. That is correct and it is the honest state. `idx_items_dangling` will keep listing it; `crawler doctor` should report the count and not treat it as an error.

### 10.4 Authors

| Column | Value |
|---|---|
| `authors.source` | `'reddit'` |
| `authors.platform_uid` | `author_fullname` (`t2_…`) when present; else `'name:' + author` |
| `authors.handle` | `author` |
| `authors.display_name` | NULL — Reddit has no separate display name |
| `authors.actor_hmac` | `HMAC-SHA256('reddit' || platform_uid, pepper)` — **the join key, always present** |
| `authors.label` | `'P-' || substr(actor_hmac,1,4)` |
| `authors.is_bot` | heuristic off `extra.distinguished == 'moderator'`? **no** — leave 0 unless the payload says so |

`author == '[deleted]'` produces **no author row**. `items.author_id` stays NULL and `visibility` moves per §11.2. Do not mint a synthetic `[deleted]` author — it would collect thousands of unrelated people under one identity.

Identity is stored `clear` — a frozen decision, and there is no per-target `identity_mode` column (ADR-0026). Reddit usernames are public handles; storing them clear in `social.db` is the correct default, masking happens in `v_items_masked` at export time, and `authors.actor_hmac` still makes `purge --person` a one-liner.

### 10.5 Metrics

Change-log, not sample-log. A row is written **only when the value moved**.

| `metrics.key` | Source field | `approximate` | Notes |
|---|---|---|---|
| `score` | `score` | **1** | `[unverified]` Reddit has historically vote-fuzzed individual scores. Assume it still does. |
| `upvote_ratio` | `upvote_ratio` × 1000 | **1** | stored ×1000 because `value` is INTEGER |
| `comments` | `num_comments` | 0 | also drives the `COMMENT_DELTA` refetch trigger |

`approximate = 1` on `score` is load-bearing, not decoration: it tells any downstream chart that a ±3 delta is noise, not signal. Without it a plot implies precision the data does not have.

`metric_schedule` drives re-observation on the decaying schedule `+1h / +6h / +24h / +72h / +7d`, then stops. Batched through `/api/info`, so 3,000 tracked posts cost ~30 requests.

`items.metrics_current` (jsonb) is updated in the same transaction so the timeline query never needs a correlated `MAX`.

### 10.6 Envelopes

| `envelopes.kind` | `content_type` | Coverage claim |
|---|---|---|
| `reddit.listing` | `application/json` | `EXACT` over `[oldest, newest]`, or `PARTIAL` + `note='listing_cap'` |
| `reddit.comments` | `application/json` | `PARTIAL` when any `more` node survives; `withheld` = sum of `more.count` |
| `reddit.morechildren` | `application/json` | `PARTIAL`, `withheld` = remaining |
| `reddit.info` | `application/json` | `EXACT` over the requested id set (`axis=AXIS_SEQ` is meaningless here — use `OPAQUE` if the id set is not an interval) |
| `reddit.arctic.search` | `application/json` | `EXACT` over the requested `[after, before]` |
| `reddit.arctic.tree` | `application/json` | `EXACT` unless the 25,000 limit was hit |

`envelopes.codec` = `'zlib'` at v1. Reddit listings are small JSON records — exactly the case where the deferred zstd + trained dictionary work (`zdict` keyed on `(source, kind)`) shows the measured 5.5× vs zlib's 1.8×. The `codec` and `dict_id` columns exist from day one so that switch is a flag flip, not a migration. `[likely]` Trigger: connector two lands (Telegram), per the deferred list.

`envelopes` (one row per **distinct byte sequence**, `sha256` UNIQUE) is split from `envelope_fetches` (one row per **fetch event**). **Reddit is the connector that exercises this split**, and it is the worked example in [../DATA-MODEL.md](../DATA-MODEL.md) §7 for that reason. Be precise about when it fires, though: a `/api/info` batch is byte-identical only while **nothing in it moved** — scores are mutable and possibly vote-fuzzed (§10.5), so a batch of week-old posts is commonly identical and a batch of fresh ones rarely is. That is exactly the shape the decaying re-observation ladder assumes. Without the split, "when did I last confirm this post still existed" — the exact input to the absence sweep — is destroyed on the identical-response case.

**Envelope growth, with the arithmetic done.** An earlier draft of this section claimed "3,000 tracked items re-observed daily is over a million envelope rows a year" and used it to justify a deferred compaction pass. That number contradicted this document's own budget table 170 lines earlier and was wrong by roughly two orders of magnitude. The honest figures:

- **Metric re-observation is batched** at ~100 fullnames per `/api/info` call, so 3,000 tracked posts cost **~30 requests a day** (§8.3) — and one request is one envelope. ~11,000 envelope rows a year, not 1.1M.
- **The schedule stops.** `+1h / +6h / +24h / +72h / +7d, then stop` means each item generates **five** refresh events total. Nothing is re-observed daily forever.
- **Byte-identical responses create `envelope_fetches` rows (~40 bytes), not `envelopes` rows.** That is the entire point of the split above.

So the growth term that matters is `envelope_fetches`, plus Facebook's HTML snapshots at ~50 KB compressed per scroll step. **The compaction pass is dropped, not deferred** ([../../PLAN.md](../../PLAN.md) §11): the retention sweep and `envelopes.purge_after` already bound the store, and a trigger sized against a hundredfold-inflated number is worse than no trigger.

### 10.7 Bookkeeping

`runs` (one per target per tick, `mode` ∈ `once|limit|daily|backfill|reparse|repair`), `cursors`, `gaps`, `field_stats`, `metric_schedule`. `usage_counters` stays empty — Reddit costs no money.

`field_stats` is the canary. Per run, per field, `seen` vs `filled`. A collapse in the `published_at` fill rate on Facebook means the DOM rotated; the identical mechanism catches a Reddit JSON field disappearing after an API change. Track at minimum: `published_at`, `author_uid`, `text`, `permalink`, `score`.

---

## 11. ToS position, stated factually

> Not legal advice. Facts and their engineering consequences.

### 11.1 The position

`[likely]` Reddit's Data API is an official, documented, OAuth-gated surface with published terms. A personal, local, non-commercial, read-only tool sits inside the intended free-tier use. This is **the only connector in this project with `tos_class = 'official_api'`.**

`[likely]` Three 2026 changes remove every casual non-API path and settle the browser question permanently:

| Date | Change | Source claim |
|---|---|---|
| ~28 May 2026 | Unauthenticated `.json` endpoints deprecated, then 403, no grace period | Reddit r/modnews: *"These endpoints can be used to scrape Reddit without accountability"*; *"logged-in and authenticated access won't be impacted"* |
| ~30 June 2026 | `old.reddit.com` requires login | a Reddit admin called the logged-out old-Reddit experience *"a significant source of abusive scraping and automated traffic"* |
| 2026 | Sitewide Rule 8 clarified | *"No unauthorized scraping. Accessing or collecting Reddit data without explicit permission continues to be a violation of our terms."* |

Context: robots.txt tightened to Google/Bing-only (March 2025); Wayback Machine cut to homepage-only (August 2025); Reddit sued Anthropic in June 2025 over crawling.

**Therefore browser automation of Reddit is never justified for this tool.** It is not merely inferior — post-2026 it requires driving a logged-in session against a surface Reddit explicitly hardened against automation, carrying the *same* account-ban risk profile as the Facebook connector while returning *strictly less* data than a free API call (no `upvote_ratio`, no clean `edited` timestamp, no `removed_by_category`, no fullname cursors, no batch `/api/info`). This is the concrete case that proves the capability contract must not assume Selenium.

### 11.2 The deletion obligation — and why it is structural, not documentary

`[unverified]` **Primary sources unreachable.** `support.reddithelp.com` returns 403 to automated fetches, `redditinc.com` and `web.archive.org` are blocked, `reddit.com` is off-limits by project rule. What follows is second-hand and **must be read first-hand before Phase 3 code is written.**

Reported requirements of the Data API Terms:

- Delete content that **users delete or moderators remove**. The Data API Wiki reportedly *"recommends routinely deleting any stored user data and content within 48 hours."*
- On account deletion, delete all related user-id info and author-identifying references (author id, name, profile URL, avatar URL, user flair) from their posts and comments.
- *"Retention of content and data that has been deleted — even if disassociated, de-identified or anonymized — is a violation of Reddit's terms and policies."*
- **No ML training** on Reddit content without a separate data-licensing agreement.
- No commercial redistribution at scale without a licence.

**Engineering reading, not moralising.** "Raw-first, keep the payload forever" and "propagate deletions" are in genuine tension, and the resolution lives in the schema:

```
GovernanceProfile(
    privacy              = BROADCAST,          # or JOINED for a private sub
    store                = STORE_SOCIAL,       # data/social.db, plain
    on_upstream_delete   = DEL_FOLLOW,         # NOT tombstone
    delete_policy_locked = True,               # the CLI override is REFUSED
    absence_strikes      = 3,
    rescan_window_days   = 3,
    retention_days       = <must be set explicitly for reddit; retention_days_must_be_explicit>,
    media_mode           = MEDIA_LINK,
    exportable           = True,
)
```

Reddit is **the only source where `on_upstream_delete` is `follow` and locked**. Every other broadcast source defaults to `tombstone`, because for a public page the *fact* of a deletion is often the datum. Reddit's terms remove that option. `--respect-upstream-deletes=tombstone --source reddit` is refused with a pointer to this section, not silently honoured.

`retention_days` must be set **explicitly and knowingly** for Reddit targets rather than inheriting the broadcast "forever" default. A Reddit archive that keeps posts indefinitely is, as those terms read, out of compliance. State the chosen number in config; do not let it be an accident.

**Detection mechanics.** Reddit reports no deletion events (`Cap.DELETE_EVENTS` unset), so the absence sweep is mandatory. It runs daily after ingest, re-checking a rolling window through `/api/info`:

| Signal | Result |
|---|---|
| `author == "[deleted]"` | `visibility='deleted_upstream'`, null `text`/`author_id` |
| `removed_by_category` set | `visibility='removed_by_mod'` |
| 404 / absent from the `/api/info` response | `absence_streak += 1`; at 3 → `deleted_upstream` |

`[likely]` `/api/info` **does not guarantee the response matches the request** — deleted or removed items simply do not appear. That is what makes absence the signal, and also what makes a *single* absence untrustworthy. Hence three strikes.

**Reddit distinguishes itself for free.** `[deleted]` (author) vs `[removed]` (moderator) map to different `visibility` values. Facebook does not; it just vanishes. Use the distinction — it is real information the other connectors would kill for.

**Three guards that must all hold before the sweep touches anything** (frozen, from the shared schema):

```sql
UPDATE items SET visibility='deleted_upstream', deleted_upstream_at=:now
 WHERE container_id=:c AND absence_streak >= 3 AND visibility='visible'
   AND (SELECT access_state FROM containers WHERE id=:c) = 'ok';
-- and the run itself must have status='ok'
```

Without the `access_state` guard, one 403 from a sub that went private tombstones every post you have from it.

**`redactions.block_reingest = 1` is not bookkeeping.** Every purge writes a `redactions` row that every ingest path consults before insert. Without it, tomorrow's 08:05 run re-creates every row you just deleted. Tested directly: redact, crawl a fixture that still contains the item, assert zero rows.

**`follow` must cascade completely.** A delete that clears `items.text` but leaves the original listing JSON in `envelopes.body` has deleted nothing. Cascade: `items` → `item_versions` → `metric_observations` → `media` → the linked `envelopes` rows → `secure_delete` sweep → `VACUUM` → `wal_checkpoint(TRUNCATE)`. Test it by grepping the raw DB file for the fixture string afterwards.

**The residual, stated plainly:** one `reddit.listing` envelope contains 100 posts from many authors. Purging one author's content deletes the whole envelope, losing unrelated posts in it. Those posts survive as parsed `items` rows but become un-reparseable. That is the correct trade and it must be documented behaviour, not a surprise discovered during a purge.

### 11.3 ML training

`[likely]` Reddit's terms reportedly prohibit training ML models on Reddit content without a separate data-licensing agreement. `[verified]` Telegram's API terms carry an equivalent prohibition. If any downstream use of this corpus involves an LLM — summarisation, embeddings, semantic search over stored posts — that is a hard architectural boundary. It is surfaced in the **export manifest** so the constraint travels with the data rather than living in a README nobody re-reads.

---

## 12. The degraded transport — specified, unbuilt

Ships **only if R0 is declined or ghosted**. `sources.transport = 'feeds'`. One backend or the other, chosen once — **no three-way abstraction is built.** (The frozen ruling overrides the recon's three-interchangeable-backends proposal: speculative generality for a source that may never authenticate.)

### 12.1 Shape

**Authenticated Atom feeds for live-edge discovery + Arctic Shift for everything else.**

`[likely]` Reddit's `.rss` endpoints survived the 2023 repricing because they were never part of the priced surface. Working patterns as of June 2026: `/r/{sub}/new.rss`, `/r/{sub}/.rss`, `/r/{sub}/top.rss?t={hour|day|week|month|year|all}`, `/r/{sub}/rising.rss`, `/r/{a}+{b}+{c}/.rss`, `/r/{sub}/comments.rss`, `/user/{name}/.rss`, `/search.rss?q=…&sort=new`, `/r/{sub}/search.rss?q=…&restrict_sr=1`. Params: `limit` (default 25, max 100), `sort`, `t`, `restrict_sr=1`. Output is **Atom 1.0** (`<entry>`, not `<item>`).

`[likely]` **But** around 11–12 June 2026 Reddit silently cut the unauthenticated feed rate limit from ~100 requests/10 min to **~1 request/minute**, breaking RSS readers with no announcement. `[unverified]` The working fix is per-account feed credentials — append `user=<username>&feed=<token>` to any feed URL, with the token copied once by hand from `/prefs/feeds/`. Both values are constant across all of that account's feeds, including search feeds. This is a **community finding, not documented behaviour**, and could change again silently.

### 12.2 What you lose

| Available from feeds | **Not available from feeds** |
|---|---|
| id (fullname derivable from the entry id) | `score`, `upvote_ratio`, `num_comments` |
| title, permalink, author, subreddit | `edited`, `removed_by_category`, flair fields |
| `updated` / `published` | `after`/`before` cursors → **no pagination past `limit=100`** |
| rendered HTML content | **comment trees, at all** |

So the degraded connector is: **feeds for post discovery at the live edge only; Arctic Shift for bodies, comments, metrics and everything historical**, accepting the ~36h metric lag on every metric. That is a genuinely usable tool. It is not the same tool, and the plan should not pretend otherwise.

### 12.3 Schema impact: none

`sources.transport` records which backend is live. Envelope kinds gain `reddit.feed` (`content_type: application/atom+xml`). Coverage from a feed is `PARTIAL` at best — a 100-entry Atom document with no cursor cannot honestly claim `EXACT` over an interval it cannot bound. Everything else — `items`, `authors`, `gaps`, `metric_observations` with `source_kind='archive'` — is unchanged. That is the payoff of freezing the schema before the transport is decided.

---

## 13. Testing

| Fixture | Asserts |
|---|---|
| `listing_new.json` | happy path; 100 items; `Coverage(EXACT)` |
| `listing_page10_capped.json` | `Coverage(PARTIAL, note='listing_cap')` → core writes a `gaps` row |
| `comments_tree.json` | nested tree; `more` nodes counted into `more_remaining` |
| `morechildren_orphans.json` | children whose ancestors are absent → `parent_id NULL`, `parent_ref` set, repair pass resolves them |
| `info_batch.json` | 100 fullnames in, fewer out → absence detected, `absence_streak` incremented |
| `deleted_author.json` | `author == '[deleted]'` → `visibility='deleted_upstream'`, no author row minted |
| `removed_by_mod.json` | `removed_by_category` set → `visibility='removed_by_mod'` |
| `empty_listing_200.json` | zero items + `EXACT` claim → **SUSPECT**, cursor rolled back |
| `429_with_retry_after.json` | `Verdict(WAIT, retry_after=…)`, exit 75 |
| `403_subreddit.json` vs `403_sitewide.json` | `DROP` (one target) vs `STOP` (disarm the source) — §9 |
| `arctic_tree.json` | one-call tree; metrics tagged `source_kind='archive'` |

Three tiers, all offline:

1. `test_parse_reddit_*` — table-driven over fixtures. A deliberately mangled fixture must yield `None`s, never a traceback.
2. `test_parse_needs_no_secrets` — construct with `secrets={}`, parse every fixture. This is the enforcement mechanism for parse purity; without it "parse is pure" is a comment that decays the first time someone needs one more request.
3. `crawler tick --fixture tests/fixtures/reddit/` — the **entire production pipeline** offline: budget accounting, commit ordering, coverage checks, gap recording, upserts, sweeps, SUSPECT detection.

**Golden-parse snapshots:** `sha256(canonical_json(ParseResult))` per fixture per `parser_version`. Bumping the version must change the snapshot deliberately — that is how you notice an "innocent" field-name tweak silently stopped populating `upvote_ratio`.

`test_no_praw_in_ingest`: after a full fixture tick, assert `'praw' not in sys.modules`.

---

## 14. Verify before building

**The consolidated list is [../../PLAN.md](../../PLAN.md) §12**; this is the Reddit subset with its per-item consequence. Ordered by what blocks what. Items 1–2 gate the connector's existence; 3–7 gate correctness; 8–12 are cheap confirmations.

| # | Claim | Confidence | How to settle it | Blocks |
|---|---|---|---|---|
| 1 | A personal, non-commercial, read-only app is approvable under the Responsible Builder Policy | `[unverified]` | **R0: submit the form.** Phase 0, day one. There is no other way to know. | the entire connector |
| 2 | Self-service registration is closed; review is manual with a ~7-day target | `[likely]` | read `support.reddithelp.com/hc/…/42728983564564-Responsible-Builder-Policy` **in a normal browser** | R0's framing |
| 3 | The Data API Terms' deletion obligation, incl. the "48 hours" figure and "even if de-identified" wording | `[unverified]` | read the Data API Wiki + Data API Terms **in a browser** before writing the sweep | `on_upstream_delete`, `retention_days` |
| 4 | 100 QPM over a rolling 10-minute window | `[likely]` | read live `X-Ratelimit-*` headers on the first authenticated request | bucket config (self-correcting either way) |
| 5 | Which OAuth scopes reach a private subreddit you belong to (`read` + `mysubreddits` + `history`) | `[unverified]` | mint one token, try one private sub, record the working set in `accounts.extra` | private-sub targets only |
| 6 | `/api/info` accepts ~100 fullnames per call | `[likely]` | one request with 100 ids; count what comes back | §8.3 rows 4–5 scale proportionally |
| 7 | Vote fuzzing is still active | `[unverified]` | fetch one post's score 5× in a minute; compare | whether `approximate=1` is honest (keep it either way) |
| 8 | `syntax=cloudsearch` + `timestamp:START..END` still functions | `[unverified]` | **one query.** If it works it is a bonus fast path. Nothing depends on it. | nothing |
| 9 | Arctic Shift coverage for *your* target subreddits | `[unverified]` | `GET /api/time_series?key=r/<sub>/posts/count&precision=day` vs your own counts | trust in the gap drain |
| 10 | The 5 Aug 2026 r/redditdev post and **two** dates from it: **30 September 2026** (register an existing/grandfathered app so its feedback counts) and **31 December 2026** (deadline to opt into Reddit's Migration Program) | `[likely]` — both second-hand | Check directly — **30 September is this month**, and **31 December is the one that decides whether an approved app survives into 2027**, which is exactly the kind of date discovered after it has passed. Both are dated checkboxes in [../../PLAN.md](../../PLAN.md) §6 M0 | optionality into 2027 |
| 11 | Current RSS rate limit and the `user=`/`feed=` workaround | `[unverified]` | only if R0 fails; the workaround is a community finding, not documented | §12 only |
| 12 | Commercial pricing ($0.24/1k calls; ~$12,000 minimum) | `[unverified]` | do not quote these. Reddit publishes no rate card. | nothing — recorded so it is not repeated as fact |

**Standing rule for this connector.** Silent, unannounced tightening is Reddit's established 2025–2026 pattern: robots.txt (Mar 2025), Wayback (Aug 2025), self-service registration (Nov 2025), unauthenticated `.json` (May 2026), old.reddit login (Jun 2026), RSS rate limits (Jun 2026). Anything this connector depends on must **fail loudly** and be re-verified on a schedule, not assumed stable. The `field_stats` canary and the SUSPECT rules are the mechanical half of that; re-reading this checklist once a quarter is the human half.

---

## 15. Known risks

| Risk | Why it matters here |
|---|---|
| **Credential risk is existential and outside the user's control.** | If R0 is declined, the full-fidelity connector does not exist. **Do not let any Facebook or Telegram milestone depend on Reddit's schema landing on time.** |
| **Platform-direction risk on a short clock.** | `[likely]` Reddit stated on 5 Aug 2026 that the public Data API is on a path toward gradual restriction in favour of Devvit, with **30 Sep 2026** to register an existing app and **31 Dec 2026** to opt into the Migration Program `[likely]` — *check both directly*. "Nothing changes this year" — but *"the API you build on is on a deprecation path"* is a fact worth writing down rather than discovering in 2027. |
| **Deletion obligation vs raw-first.** | Not resolvable by wishful wording. Build the sweep in the same milestone as ingest, or neither. |
| **Arctic Shift is a single point of failure.** | One volunteer, donation-funded, no uptime guarantee, carrying the load-bearing backfill role. Mitigation is the local `.zst` dumps, and its trigger is already written into the deferred list. |
| **Archive metric lag silently corrupts time series.** | Only `source_kind` prevents it. Never let a connector write an archive-sourced metric without it. |
| **The 1,000 wall turns any multi-day outage into permanent loss** unless gaps are recorded and drained. A daily job reporting success while skipping a week is invisible until analysis time. |
| **Score is mutable forever and possibly fuzzed.** | Any analysis treating one observation as ground truth, or reading meaning into ±3 deltas, is measuring noise. Same trap as Facebook reaction counts; same fix. |
| **Comment expansion is 96% of the budget.** | If the budget ever needs cutting, cut `MORE_BUDGET` or raise `COMMENT_DELTA`. Cutting targets barely helps. |

---

*See also: [../DATA-MODEL.md](../DATA-MODEL.md) for the full DDL and the canonical queries · [../../ARCHITECTURE.md](../../ARCHITECTURE.md) for the connector contract, `Envelope`, `Coverage`, `Budget` and the verdict taxonomy · [./x.md](./x.md) for the other REST connector, and the contrast between a quota governor and a spend governor.*
