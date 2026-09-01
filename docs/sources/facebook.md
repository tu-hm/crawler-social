# Connector #1 — Facebook

**Transport:** headful Chrome, driving your own logged-in session.
**ToS class:** `prohibited` — automated collection is against Meta's Terms even for
content your account can already see. The currency of failure is your account, not a
lawsuit. See [../../PLAN.md](../../PLAN.md) §3.
**Ships:** first. See [../../PLAN.md](../../PLAN.md) §6 M2–M10.

Facebook is the only source in this project where browser automation is the right answer,
and it is worth being precise about *why*, because the reason does not generalize:
**Facebook renders the content you want into the DOM of a page your session is entitled
to see.** Reddit has a better API. Telegram has a far better one. Zalo Web encrypts its
own request params with a per-session key, so a driver there gets nothing a scraper can
use. Facebook clears the bar that the others fail or exceed.

Everything in this document assumes the contract in [../../ARCHITECTURE.md](../../ARCHITECTURE.md)
and the schema in [../DATA-MODEL.md](../DATA-MODEL.md).

> **A sourcing note that applies to this entire document.** The research rule for this
> project forbade any request to `facebook.com`. Every DOM selector, GraphQL operation
> name and page-marker string below is therefore a **starting hypothesis derived from
> third-party reporting and from how these pages are known to be built** — not a verified
> observation. They exist so M2 has something concrete to confirm or discard in an hour
> rather than a day. The library, protocol and platform-lifecycle facts *are* verified,
> and are labelled as such. Do not let the two categories blur.

---

## 1. Position in the design

```yaml
# sources row
key:         facebook
transport:   browser
tos_class:   prohibited
state:       ok
```

### Capabilities

```python
caps = (Cap.NEEDS_GUI            # implies NEEDS_SESSION; must run in an Aqua session
        | Cap.NEEDS_SESSION
        | Cap.SINGLE_FLIGHT      # one process may hold the Chrome profile
        | Cap.NEEDS_HUMAN_LOGIN
        | Cap.BACKFILL           # by scrolling, slowly, at real risk
        | Cap.COMMENT_TREE
        | Cap.MUTABLE_METRICS)   # reaction and comment counts drift forever
```

Deliberately **not** set, and each absence is load-bearing:

| Flag | Why not |
|---|---|
| `EXACT_CURSOR` | There is no cursor. A scroll position is not addressable. Resume always re-reads an overlap |
| `DELETE_EVENTS` | Facebook reports no deletion events — but note that the absence sweep cannot help either, because an `opaque` coverage claim yields no absence evidence. **Facebook upstream deletes are undetectable, permanently** (§9), so `rescan_window_days = 0` |
| `PARALLEL_TARGETS` | One browser, strictly serial, deliberately slow |
| `BILLED` | Costs time and account risk, not money |
| `CONVERSATIONS` | Messenger is out of scope at v1. If it is ever added, this flag flips and everything in [../GOVERNANCE.md](../GOVERNANCE.md) engages |
| `FILE_IMPORT` | No archive importer at v1. The Facebook data-export ZIP is a plausible future `FILE_IMPORT` target and would reuse the shape M14 builds for X. *(`Cap.PUSH` is not in this list because it does not exist — see ARCHITECTURE.md §6.)* |

### `capabilities_note()`

Free text that core renders in `crawler capabilities` and never branches on. This is
where the connector says what no enum can:

```
facebook: headful Chrome on your own session.
  coverage: ALWAYS 'opaque'. A virtualised feed cannot honestly claim an interval.
  cursor:   approximate watermark (newest item id + newest published_at). Resume
            re-reads an overlap and stops on 5 consecutive already-seen non-pinned posts.
  backfill: by scrolling only. --since 90d is the default; deeper is a multi-hour
            session on an account you care about. There is no page 11 and no API.
  comments: second navigation per post, emitted as a separate fb.post_html envelope.
  media:    link only. Facebook CDN URLs expire; a stored URL is a dead pointer.
  metrics:  reaction counts are rounded above 1k in the UI ('1.2K'), so every metric
            from a DOM parse is recorded approximate=1.
```

### Envelope kinds

| Kind | Content type | When | Coverage |
|---|---|---|---|
| `fb.feed_html` | `text/html` | Every scroll step, unconditionally, from day one | `opaque`, `note="scroll_unmount"` |
| `fb.post_html` | `text/html` | A second navigation to a post permalink, for comments or a truncated body | `opaque`, `note="post_detail"` |
| `fb.graphql.feed` | `application/json` | Only if M2 says response-body capture works. Emitted **alongside** `fb.feed_html`, never instead of it | `opaque`, `note="graphql_observed"` |
| `fb.wall_html` | `text/html` | Whenever a wall is detected — the wall document is stored so the classifier can be improved later against real evidence | `opaque`, `note="wall"` |

**Coverage is `opaque`, forever, on every Facebook envelope.** This is a first-class
honest value, not a failure state. The alternative — inventing a scroll-position interval
— would be strictly worse than claiming nothing, because core would then confidently
record "no gap" over a feed that unmounted half its posts while you scrolled past it.

### Governance

`propose_privacy()` reads the target kind and, for groups, the privacy badge:

| Target | `containers.kind` | `shape` | proposed privacy | store |
|---|---|---|---|---|
| Public Page | `page` | `feed` | `broadcast` | `social.db` |
| Public group (not joined) | `group` | `feed` | `broadcast` | `social.db` |
| Public group (joined) | `group` | `feed` | `broadcast` | `social.db` |
| Private / closed group | `group` | `feed` | **`joined`** | `social.db` |
| Group whose badge did not render | `group` | `feed` | **`conversation`** — fail closed | `private.db` |
| Messenger | — | — | out of scope at v1 | — |

The fail-closed row is the one that matters. If the privacy badge is missing from the
DOM — a rotation, a partial render, a wall — the connector must return `CONVERSATION`, not
guess `broadcast`. Being wrong in that direction costs you a target sitting in the
encrypted store until you re-classify it. Being wrong in the other direction puts a
private group's contents in the plain file with no encryption and no retention timer.

Note that a `joined` group still lands in `social.db` — the encryption boundary is
`conversation` only. What `joined` buys is the delete policy (`follow` rather than
`tombstone`), a shorter default retention, and export gating.

### Budget

```yaml
facebook:
  mode: paranoia            # a comment for humans. NO code branches on this.
  buckets:
    navigation: { capacity: 3, refill_per_sec: 0.2 }    # ~1 navigation / 5s
  session:
    max_items:   200
    max_seconds: 1500       # -> Budget.deadline_ts. 25 minutes, then quit the driver.
    per_day:     2
  cooldown_between_targets: [60, 180]
  on_soft_block: [900, 3600, 14400]   # then STOP
  on_hard_block: stop_forever
```

`mode: paranoia` exists purely to tell whoever reads the config where the numbers came
from. Every other connector's bucket is configured from a published quota and corrected by
real response headers. Facebook publishes nothing, so this budget has **no feedback
signal**: if the numbers are too aggressive you find out by getting checkpointed, not by a
429. That asymmetry is unmitigable. The only defence is starting conservative and loosening
after a clean week — which is exactly why the numbers below are unchanged from the original
plan rather than tuned upward for throughput.

---

## 2. Surface choice: `www` vs `m` vs `mbasic`

**Decision: `www.facebook.com`. Not negotiable at v1, and the two alternatives are worse
than they were when the original plan was written.**

| Surface | Status, September 2026 | Verdict |
|---|---|---|
| `www.facebook.com` | The live product. React, GraphQL-driven, virtualised feed, obfuscated class names | **Use this** |
| `mbasic.facebook.com` | Retirement **announced November 2024, effective 3–4 December 2024** *(verified — the announcement and the effective date are widely reported and the Hacker News thread naming December 3rd is contemporaneous)*. Any residual resolution today is a leftover, not a supported surface | **Rejected.** The original plan's line that it "still resolves in 2026" is stale and should not be carried forward |
| `m.facebook.com` | No retirement announcement found; still serves a restricted mobile interface *(likely — absence of evidence, and no primary source was fetched)*. But it is **not** the old static-HTML mbasic; it is its own app surface | **Rejected**, for two independent reasons below |

**Why not `m.facebook.com`, concretely.** It is tempting because "mobile page = simpler
HTML" is the folk wisdom that made mbasic worth using. That wisdom expired with mbasic.
Two objections, and the second is the disqualifier:

1. **It is unverified that `m.` server-renders group feeds at all.** It has been an app
   surface for years. If it renders client-side too, you have swapped one virtualised feed
   for another and gained nothing. This is a five-minute check, not a plan.
2. **It would break fingerprint consistency, which is the one stealth property that
   actually matters here.** Reaching `m.facebook.com` in a way that gets you the mobile
   experience means presenting a mobile user-agent — from a persistent profile whose entire
   session history, device registration and cookie set belong to a desktop Mac. That is not
   a smaller signal than automation; it is a *louder* one. An inconsistent spoof is worse
   than no spoof, and §4 explains why that principle governs every stealth decision in this
   connector.

**A third reason that only applies if M2 succeeds:** `www` is the surface that fires the
GraphQL queries you want to capture. Downgrading to a server-rendered surface to make DOM
parsing easier would throw away the richer envelope kind. The correct trade is the other
way round.

**Related and out of scope:** Meta announced in February 2026 that the standalone
`messenger.com` site closes in April 2026, with web users redirected into
`facebook.com/messages` *(likely — press reporting)*. Nothing here depends on it, but it is
worth knowing that if Messenger is ever added, its surface is `facebook.com`, not a
separate host.

---

## 3. M2 — the extraction-strategy spike

**This is a parse-quality gate, not a project go/no-go gate.** The original plan made M2 a
project-wide gate that every later milestone hung off, and choosing wrong there would have
cost every post collected before you noticed. The frozen rule replaces it:

> **Capture `fb.feed_html` from day one, unconditionally, whatever the spike says.** If
> response-body capture works, capture `fb.graphql.feed` **alongside** it and have the
> parser prefer the richer kind. The HTML history stays replayable either way.

That reframing removes the risk entirely. The worst outcome of the spike is now "the
parser has one input instead of two", not "six months of collection was the wrong shape".

**Timebox: half a day.** Do not extend it. If all three candidates fail, the answer is DOM,
and you have lost half a day rather than a week.

### 3.1 What changed since the original plan

The original plan framed this as "Selenium BiDi GraphQL vs DOM". Two verified facts move
the goalposts, in opposite directions:

**Selenium's BiDi network module cannot read a real response body.** The documented method
set is `addIntercept`, `removeIntercept`, `continueWithAuth`, `continueWithAuthNoCredentials`,
`cancelAuth`, `failRequest`. Nothing exposes the payload of a response that actually came
back from the server; the `continueResponse` family is for *providing* or *modifying* a
response, which is the opposite operation. *(Verified against
`selenium.dev/documentation/webdriver/bidi/w3c/network/`, 2026-09-01.)* There is a
long-standing open feature request for exactly this capability. So candidate (A) is a **no**
before you write any code — which is worth knowing, because it is the candidate the
original plan led with.

**SeleniumBase's CDP Mode can.** It ships a documented, exercised path:

```python
# ILLUSTRATIVE — adapted from SeleniumBase's own examples/cdp_mode/raw_xhr_sb.py
import mycdp
from seleniumbase import SB

captured = []          # (url, request_id)

def listen(page):
    async def handler(evt):
        if evt.type_ is mycdp.network.ResourceType.XHR:
            captured.append((evt.response.url, evt.request_id))
    page.add_handler(mycdp.network.ResponseReceived, handler)

async def drain(page, requests):
    out = []
    await page
    for url, request_id in requests:
        res = await page.send(mycdp.network.get_response_body(request_id))
        if res is None:
            continue
        out.append({"url": url, "body": res[0], "is_base64": res[1]})
    return out
```

*(Verified: `add_handler(mycdp.network.ResponseReceived, …)`,
`page.send(mycdp.network.get_response_body(request_id))` returning `(body, is_base64)`, and
the `ResourceType.XHR` filter are all read directly from
`seleniumbase/examples/cdp_mode/raw_xhr_sb.py` and `raw_res_sb.py` on `master`,
2026-09-01.)*

**And one clarification that removes a worry from the original plan.** "CDP is deprecated
and removed in Selenium 5.0" is true of *Selenium's* DevTools bindings —
`driver.execute_cdp_cmd`, the `selenium.webdriver.common.devtools` modules. SeleniumBase's
CDP Mode does not go through them; it drives Chrome over the protocol directly via `mycdp`,
a nodriver-derived library, with chromedriver disconnected. Selenium 5.0's removal does not
kill it. The churn risk moves from Selenium's release policy to SeleniumBase's, which is
different but not obviously worse — SeleniumBase shipped six releases in August 2026 alone
*(verified from PyPI: 4.52.0 through 4.53.0, 2026-08-19 to 2026-08-30)*.

### 3.2 The three candidates, in test order

| # | Candidate | Prior | What it buys | What it costs |
|---|---|---|---|---|
| **A** | Selenium BiDi network | **Fails on documentation alone** | — | Nothing. Skip it; ten minutes reading the docs above is the whole test |
| **B** | **SeleniumBase CDP Mode** | **Most likely to work** | Structured GraphQL JSON: exact timestamps, unrounded counts, author ids, comment trees, pagination cursors | chromedriver is disconnected in CDP Mode, so the Selenium API surface is partly unavailable and you drive through `sb.cdp.*`. Async handlers. Response bodies must be drained before the buffer is dropped |
| **C** | DOM only | **The floor. Always available** | Simplicity; no protocol dependency at all | Rounded counts, relative timestamps, obfuscated class names that rotate, and a per-field fallback chain to maintain |

### 3.3 Exactly how to run it

Half a day, in this order. Stop as soon as B produces a real body.

1. **Ten minutes: confirm A is out.** Read the BiDi network doc page linked above. If a
   method for reading a real response body has appeared since 2026-09-01, re-open this
   decision; otherwise mark A rejected with today's date and move on.

2. **Set up the CDP Mode harness against a page you are already entitled to see.** Use the
   persistent profile from §5 — logged in, warm, with history. Do not do this from a fresh
   profile; a brand-new profile hitting a group feed with an automation stack attached is
   the highest-risk single action in the whole project.

   **"Warm" is a number now, not a judgement call.** The clock starts at M0/P0 —
   `crawler login`, then use that profile by hand like an ordinary browser. `crawler doctor`
   reports profile age from the user-data-dir mtime and `crawl` **refuses** below the
   threshold with an actionable message: **≥ 3 days** before a public Page, **≥ 14 days**
   before a group. Run this spike against a **Page** first, and take the group feed (step 5)
   only once the 14-day threshold is met. An earlier draft asserted the principle and gave
   no number, which left the whole §4 pacing argument undercut at the exact moment it
   matters most.

3. **Register the handler *before* navigation**, then navigate to one Page feed. Log every
   XHR response URL and `request_id` you see. You are looking for POSTs to a GraphQL
   endpoint. Write down what you actually observe — the `fb_api_req_friendly_name` form
   field is the usual identifier, and names of the shape
   `GroupsCometFeedRegularStoriesPaginationQuery` are reported in the wild *(likely,
   third-party)*. **Record what you see; do not trust this document's names.**

4. **Drain the bodies promptly.** `get_response_body` reads from the browser's network
   buffer, which is dropped on navigation and bounded in size. If a body comes back
   `is_base64=True`, decode it and check what you actually have — the SeleniumBase
   maintainer states plainly that byte-level decompression is out of that project's scope,
   so if Facebook serves these bodies in a form the protocol hands back compressed, that is
   your problem to solve or your reason to fall back. **This is the single most likely way
   B fails in practice, and it is the specific thing to test.**

5. **Repeat against a private group feed**, because the feed query differs and this is the
   target you actually care about.

6. **Whatever happens, capture the six fixtures**, because every later milestone runs
   offline against them:

   | Fixture | Why it must exist |
   |---|---|
   | Public Page feed | The easy case; the parser baseline |
   | Private group feed | The case you care about; different query, different DOM |
   | Post with media | Album grouping, link cards, video posts |
   | Post with >100 comments | Comment pagination and the "View more comments" path |
   | A checkpoint / block wall | M7's classifier has nothing to test against without it |
   | **An empty feed returning HTTP 200** | The failure that looks like success. If you cannot capture one naturally, synthesize it — M7 and the SUSPECT rule both need it |

   Each fixture gets a `meta.json` sidecar reproducing the `Envelope` non-body fields
   (`kind`, `captured_at`, `content_type`, `cursor_after`, `coverage`, `meta`). Capture via
   `crawler capture`, never by hand-copying out of DevTools.

### 3.4 How to decide

| Observation | Decision |
|---|---|
| B returns a decodable JSON body containing recognisable post fields, on **both** the Page feed and the private group feed | **Emit `fb.graphql.feed` alongside `fb.feed_html`.** Parser prefers the GraphQL kind and falls back to HTML per field |
| B returns bodies for the Page feed but not the group feed | Emit both kinds where available. The parser already handles per-field fallback, so a target with no GraphQL simply parses from HTML. **Do not build a second connector** |
| B returns bodies but they are base64 blobs you cannot decode into JSON in under an hour | **Stop. DOM only.** Record the reason and the date. Re-open only if someone hits the same wall and solves it |
| B's handler never fires, or CDP Mode will not attach to the persistent profile | **Stop. DOM only.** The profile is worth more than the richer envelope |
| Anything triggers a checkpoint during the spike | **Stop everything.** Clear the checkpoint by hand, wait, and re-run the spike a week later at half the pace. The spike is not worth the account |

Write the outcome into this section as a dated line, e.g.
`2026-09-XX — B confirmed on Page and group feeds; fb.graphql.feed emitted alongside HTML.`
That dated line is what a future reader needs; the reasoning above is what they need if it
stops being true.

### 3.5 One escalation that is explicitly rejected

There is a tempting fourth option: extract the `doc_id` and the `fb_dtsg` token from the
page, then issue your **own** GraphQL POSTs directly, paginating far faster than a human
could scroll. **Rejected, permanently.**

- It changes what you are. Observing the requests the real page makes is recording your own
  browsing. Forging requests the page did not make is impersonating the app to its own
  backend — a different act, a much louder automation signal, and squarely the behaviour
  enforcement is tuned for.
- It is brittle in a way that never stabilises: the `doc_id` is a persisted-query hash
  embedded in obfuscated JS bundles that change on every frontend deploy *(likely,
  third-party reporting)*. You would be re-deriving it, from minified JavaScript, forever.
- It buys throughput, and throughput is the thing this connector is deliberately not
  optimising for. Re-read §4.

The pacing budget in §4 assumes you are scrolling like a person. Every argument in this
document collapses if you are not.

---

## 4. Stealth posture: pacing beats fingerprinting

**The single most important sentence in this document: you are logged in.**

Facebook is not trying to work out whether you are a browser. It already knows exactly who
you are — you handed it a session cookie tied to an account with years of history, a
registered device, and a behavioural baseline. The question it is actually asking is
*"does this account behave like a person?"*, and no amount of `navigator.webdriver`
patching answers that question in your favour.

So the effort budget is lopsided on purpose:

| Effort | Where it goes | Why |
|---|---|---|
| **~80%** | Pacing, session length, sessions per day, dwell distributions, cooldowns between targets | This is what the account-level heuristics measure |
| **~15%** | One *consistent* fingerprint story | Consistency, not exoticism. See below |
| **~5%** | Driver-level stealth (UC Mode) | Real but marginal once you are authenticated |

**Consistency is the whole fingerprint requirement.** UA, `navigator.platform`,
`Accept-Language`, `Intl.DateTimeFormat().resolvedOptions().timeZone`, screen metrics and
the profile's own history must tell **one** story: a Mac in Vietnam, in English, at a fixed
realistic viewport. An inconsistent spoof — a mobile UA on a desktop profile, `en_US` with
an `Asia/Ho_Chi_Minh` clock and a `vi-VN` `Accept-Language`, a 1920×1080 window reporting a
Retina device pixel ratio — is a *louder* signal than no spoof at all, because ordinary
users are never internally contradictory. This is the reason §2 rejects `m.facebook.com`.

**Headless: no. Always headful.** Headless is the single loudest signal available, and a
warm logged-in session is far too valuable to spend on the convenience. This constrains
scheduling to a GUI session, which is why the tool uses a LaunchAgent rather than `cron`
(see [../../PLAN.md](../../PLAN.md) §7).

**Driver: SeleniumBase UC Mode**, with all construction behind one module so swapping is a
one-file change. *(Verified from PyPI, 2026-09-01: `selenium` 4.48.0, released 2026-08-27,
`requires_python >=3.10`; `seleniumbase` 4.53.0, released 2026-08-30, `requires_python
>=3.10`; `nodriver` 0.50.3, released 2026-05-13.)* The anti-detect frontier has moved off
WebDriver entirely — undetected-chromedriver's own author ships `nodriver` because patching
WebDriver is a losing game — but the frontier is not where this connector lives. You are
not trying to beat a bot-detection vendor from a cold start; you are trying not to look
anomalous from inside a warm session. Accept the ceiling and isolate it.

### The pacing table

Unchanged from the original plan. These are cheap; a flagged account is not.

| Knob | Default | Note |
|---|---|---|
| Scroll dwell | **2.5–6.0 s**, jittered, eased | Eased, not uniform-random. Human scroll intervals are not a flat distribution |
| Post-open dwell | **4–12 s** | Applies to the second navigation for comments |
| Between targets | **60–180 s** | A full cooldown, not a pause |
| Posts per session | **200** | `Budget.max_items` |
| Session length | **25 min max, then quit the driver** | `Budget.deadline_ts`. Quitting matters — a browser idling on a feed for an hour is its own signal |
| Sessions per day | **2** | The 08:05 and 20:35 ticks |
| Backoff on soft block | **15 min → 1 h → 4 h, then stop** | Policy backoff, because Facebook supplies no `Retry-After` |
| Hard block | **stop everything, alert, no retry** | `STOP` disarms the schedule until `crawler resume` |

All jitter comes from a **seedable** `random.Random` so tests are deterministic. Seed it
from the run id in production and from a constant in tests.

**Loosening protocol.** Do not tune these upward because a run felt slow. Run a full week
at these values with zero walls of any kind, then change **one** knob by at most 25%, then
run another clean week. If any wall appears, revert immediately and do not re-try that
change for a month. There is no feedback signal here (§1), so the only instrument you have
is a clean week.

---

## 5. Persistent profile handling

The Chrome profile **is** the Facebook credential. Treat it exactly as you would treat a
password file — because that is what it is.

```
~/Library/Application Support/crawler-social/chrome/default/     mode 0700
```

**Outside the git worktree, deliberately**, so that no `.gitignore` mistake can ever stage
it. `profiles/` and `*.session*` are gitignored anyway, as belt and braces, but the
directory placement is the actual control. Keychain cannot hold this — Chrome wants a real
directory path — so the filesystem is the boundary.

| Rule | Why |
|---|---|
| **`Cap.SINGLE_FLIGHT`, flocked.** Take an exclusive lock on the profile path before constructing the driver; if held, exit 0 | Two Chrome processes on one user-data-dir corrupt it. This is also what stops a manual `crawler crawl` from colliding with the 20:35 tick |
| **Detect the lock and say so.** If Chrome already has the profile open (you opened it by hand), fail with a message naming the profile path, not a `SessionNotCreatedException` traceback | This will happen, and the raw exception is unactionable |
| **Never in a synced directory.** Not iCloud Drive, not Dropbox, not `~/Documents` if that is synced | A synced session cookie is a session cookie on someone else's server |
| **Never backed up casually.** `~/Library/Application Support/` in a Time Machine or cloud backup silently copies your live Facebook session | Exclude the directory from backup tooling and say so in the setup docs |
| **Never copied to a second machine.** Log in again instead | Same account, two IPs, two device fingerprints, simultaneously — the exact pattern that gets a session challenged |
| **One profile, warm.** Do not rotate profiles, do not use a fresh one per run | Session age and history are assets. A brand-new profile is the most suspicious thing you can present |
| **`crawler login` is the only interactive path**, never called by a job. A scheduled run with no valid session fails **loudly** (exit 78, `HUMAN`) and never blocks on stdin | A job that blocks on a password prompt at 08:05 hangs until you notice in November |

**Locale and viewport are pinned at construction**, not left to the machine: forced
`en_US`, `Asia/Ho_Chi_Minh`, and a fixed realistic viewport. Pinned so that a macOS update
or a locale change cannot silently alter your fingerprint between runs — an *unexplained
change* in a stable profile is more interesting to a heuristic than any particular value.

**Chrome auto-update vs driver skew** is the mundane failure that will actually bite you.
`crawler doctor` reports both versions and warns on a mismatch. Chrome updating overnight
and the driver not following is the most likely cause of a Monday morning of red runs that
has nothing to do with Facebook at all.

---

## 6. Wall and challenge taxonomy

Port `~/dev/crawler-pages/webcrawler/challenge.py` into `connectors/facebook/walls.py` as the pattern. Take the whole design,
not just the idea:

- The frozen `Challenge` dataclass with `kind` / `vendor` / `detail`, the `human_clearable`
  property, and module-level string constants for kinds.
- **`detect()` as a pure function** of `(html, title, status, final_url)`. No driver, no
  network, no clock. That is what makes it table-driven testable against saved wall HTML,
  which is the only way this stays correct as Facebook changes its wording.
- The **`_WALL_TEXT_LIMIT` length guard.** A challenge page is always small; past ~2500
  characters of visible text you are looking at real content that merely *mentions* a
  checkpoint, and flagging it would be a false positive. This guard is the difference
  between a classifier you trust and one you learn to ignore.
- The stated stance, which is already right for this project: *"Nothing here defeats a
  challenge. When one shows up the crawler stops... and waits for you to complete the check
  the way any other visitor would."* Facebook changes nothing about that.
- The conservatism: **it would rather miss a wall than mislabel a real page**, because a
  false positive stops the crawl and bothers you for nothing.

What does **not** port: the `ChallengeGate` human-handoff machinery. `crawler-pages` runs
many worker threads and needs to serialise "a person clears the wall" across them. This
connector is strictly serial with one browser, so the handoff is just `crawler login`.

### The walls

| Wall | Detection signal | Verdict | Exit | What core does |
|---|---|---|---|---|
| **Login redirect** | `final_url` path in `/login`, `/login.php`, `/checkpoint/?next=`; a password input in a small document | `HUMAN` | 78 | Notify loudly, disarm, stay stopped until `crawler login` |
| **Two-factor prompt** | Small document, code-entry field, "two-factor" / "login code" wording | `HUMAN` | 78 | Same |
| **`/checkpoint/`** | `final_url` contains `/checkpoint/` and it is **not** a login redirect | **`STOP`** | 86 | `sources.state='stopped'`. **Never retried.** launchd keeps firing; every run is an immediate no-op exit 0 until `crawler resume --source facebook` |
| **Identity verification** (photo ID upload, "confirm your identity") | Checkpoint URL plus upload-control or ID wording | **`STOP`** | 86 | Same, and this one is why STOP exists — retrying it is how a recoverable state becomes an account loss |
| **Account disabled** | "your account has been disabled" wording, small document | **`STOP`** | 86 | Same |
| **Temporarily blocked** ("You're Temporarily Blocked", "please try again later") | Wall wording in a small document, or an interstitial that replaces the feed | `WAIT` | 75 | **Policy** backoff 15m → 1h → 4h → STOP. Facebook supplies no `Retry-After`, so unlike Telegram there is no server-supplied duration to obey |
| **Not a member / no access** ("You must be a member", a Join button where the feed should be) | Group URL resolves, feed container absent, join affordance present | `DROP` | 0 | `containers.access_state='no_access'`. **Absence sweep skipped for that container** — this is the guard that stops one lost group from tombstoning 4,000 posts |
| **Content unavailable** (single post) | Post permalink returns "This content isn't available right now" | `DROP` for that item only | 0 | Does not fail the run. **This is the one Facebook signal that is genuine absence evidence**, because a permalink either resolves or does not — unlike a feed re-scan, which claims `opaque` and therefore contributes nothing (§9). Nothing at v1 navigates permalinks for this purpose; it is what a future bounded delete-check would be built on |
| **Group went private / you were removed** | Indistinguishable from a block at the HTTP level | `DROP` + `access_state` change | 0 | This is why `access_state` exists as a separate axis from `runs.status`. Getting these two confused is how you chase ghosts |
| **Empty feed, HTTP 200** | **Not detected by the connector at all** | `SUSPECT` | — | **Core-detected.** See below |

### The empty-feed-with-200 case

Preserved verbatim in spirit from the original plan, because it is still the failure that
bites:

> It looks like a clean successful crawl of a quiet day, so the watermark advances and
> every subsequent daily run skips real posts forever. Treat "0 items from a target that
> had items yesterday" as a **failure**, not a result: don't advance the cursor, and flag
> the run.

What changed is *where the logic lives*. The connector contributes nothing to this beyond
an honest coverage claim. Core detects it, for every connector, with **two rules** — each
blind exactly where the other fires:

- **(a)** An envelope claims `exact` over a non-empty interval and parses to `found == 0`.
  Fires on run one. Facebook never claims `exact`, so **rule (a) does not protect
  Facebook** — it protects Reddit's auth-error page and Telegram's silently-empty history.
- **(b)** Zero items from a target that produced items in **each of its last three runs**.
  This is the rule that catches Facebook's empty feed, and it needs the run ledger, which
  only core has.

Either rule rolls back the cursor write and sets `status='suspect'`. Two consecutive
escalate to `HUMAN`.

**Be honest about rule (b)'s blind spots**, because they are real: it cannot fire on a
brand-new target, and it is blind for three runs after a reparse resets state. The
mitigation is not a cleverer rule — it is that the run ledger is never truncated, so the
history rule (b) depends on is always there.

**Store the wall document.** Every detection emits an `fb.wall_html` envelope before
exiting. The classifier can only be improved against real evidence, and the one time you
need that evidence is the one time you will not have thought to save it by hand.

---

## 7. Scroll-and-harvest mechanics

### 7.1 The virtualized-feed trap

**This is the single most common way these crawlers quietly under-collect, and it produces
no error of any kind.**

Facebook's feed is virtualised: as you scroll, React unmounts post nodes that have moved
far enough off-screen. A crawler that scrolls to the bottom and *then* serialises the DOM
captures only what happens to be mounted at that moment — often a small tail of the feed —
while reporting a completely successful run. You get maybe 15 posts out of 200 and no
indication anything went wrong.

**The fix is structural: `fetch()` is a lazy generator that harvests at every scroll step.**

```python
# ILLUSTRATIVE
def fetch(self, target, cursor, budget, gov) -> Iterator[Envelope]:
    seen: set[str] = set()
    run_of_known = 0
    for step in self._scroll_steps(budget):          # yields after each scroll + dwell
        fresh = self._mounted_posts_not_in(seen)     # subtrees currently in the DOM
        if not fresh:
            continue
        seen.update(p.item_id for p in fresh)
        yield Envelope(
            source="facebook", target_id=target.id, kind="fb.feed_html",
            body=b"".join(p.outer_html for p in fresh),
            content_type="text/html", captured_at=now(),
            coverage=Coverage(COVER_OPAQUE, note="scroll_unmount"),
            cursor_after=self._watermark_if_advanced(fresh),
            privacy=gov.privacy,
            meta={"scroll": step, "n_posts": len(fresh), "url": self._url},
        )
        budget.take(items=len(fresh), requests=0)
        run_of_known = self._update_stop_counter(run_of_known, fresh, cursor)
        if run_of_known >= STOP_AFTER_SEEN or budget.exhausted():
            return
```

Four properties this shape buys, all of which matter:

1. **Nothing is lost to unmounting**, because the bytes are captured while the node is
   mounted.
2. **Core commits each envelope before the generator is resumed**, so a crash mid-scroll
   costs at most one scroll step.
3. **`--limit N` and the 25-minute deadline both work**, because `budget.exhausted()` is
   checked every step rather than after the fact.
4. **Facebook envelope bodies are per-step deltas, so `sha256` dedupe rarely fires here** —
   and this is the one place to be honest about it rather than repeat the generic argument.
   `fresh` is the set of posts *not yet seen in this run*, so within a run the bodies are
   disjoint by construction, and across runs a day apart the fresh sets, their ordering and
   their embedded tracking params all differ. A byte-identical `fb.feed_html` is essentially
   impossible.

   The `envelopes` / `envelope_fetches` split is still correct and still valuable — it is
   just Reddit's `reddit.info` that exercises it, not this connector (see
   [../DATA-MODEL.md](../DATA-MODEL.md) §7). **For Facebook, "when did I last confirm this
   post existed" comes from `items.last_seen`, bumped by the upsert, plus
   `items.absence_streak` — not from `envelope_fetches`.**

### 7.2 An honest caveat about "raw" HTML

A DOM serialization is **not** literally the transport bytes. It is a snapshot of rendered
state after the client framework has had its way with the response. So the raw-first
guarantee is genuinely weaker here than for a JSON API:

- You **can** re-parse the rendered DOM you captured, forever, with no network. That is the
  guarantee that matters and it holds.
- You **cannot** recover a field the DOM never contained. If Facebook renders a rounded
  `1.2K` and the exact number only ever existed in a GraphQL response you did not capture,
  no future parser recovers it.

This is precisely why `fb.graphql.feed` is worth capturing alongside if M2 permits, and why
`media_mode` defaults to `link` with an explicit note that the replay guarantee covers
**structured content only** — a Facebook CDN URL is a dead pointer within days.

### 7.3 The rest of the loop

| Mechanic | Rule |
|---|---|
| **Sort order** | Group feeds get `?sorting_setting=CHRONOLOGICAL`. *(Community-established, never documented by Meta; the "Recent posts" affordance is hidden on most groups, which is why people type it by hand. Re-confirm at M2 — if it stops working, the watermark's stop rule degrades but does not break, because it counts already-seen posts rather than trusting order.)* |
| **In-flight dedupe** | By `platform_item_id` within the run, before the envelope is emitted. Cross-run dedupe is the DB's job via `UNIQUE (container_id, platform_item_id)` |
| **"See more"** | Expand every truncated body **before** capturing that post's subtree. A truncated body captured raw is a permanently truncated body, and no reparse recovers it — this is the one place where a fetch-time mistake defeats raw-first |
| **Comments** | A **second navigation** to the post permalink, emitted as a separate `fb.post_html` envelope. `parse()` never fetches. This is the purity tax, paid deliberately: six months of re-parseable comment HTML is worth the browser minutes |
| **Which posts get comments** | Only new posts, or posts whose comment count moved by more than `COMMENT_DELTA` (default 5) since the last comment fetch. Every navigation costs 4–12 s of dwell and a slice of the 25-minute session |
| **Sponsored posts** | Parsed and stored with `is_sponsored=1`, never skipped silently. They are also excluded from the stop counter (§8) |
| **Pacing** | Every scroll step and every navigation goes through `budget.take()`, which blocks on the token bucket. Pacing is not a `sleep()` sprinkled in the loop; it is the one place the budget is enforced |
| **SIGTERM** | Trapped: checkpoint by yielding the current envelope, then return. launchd sending SIGTERM at logout must not lose the step in flight |

---

## 8. Per-field extraction

**Every selector and path in this table is a hypothesis to confirm at M2 against your own
fixtures.** They are here so the confirmation takes an hour rather than a day. The
*structure* of the table — a documented fallback chain per field and an explicit failure
mode — is the part that is frozen.

The governing rule: **a missing field returns `None` and never raises.** A parser that
throws on one malformed post loses the whole envelope; a parser that returns `None` loses
one field and the canary tells you about it (§10).

| Field | Preferred (GraphQL, if M2 succeeds) | DOM fallback chain | Failure mode if all fall through |
|---|---|---|---|
| `platform_item_id` | post node id / `post_id` | 1. `story_fbid=` in a permalink anchor · 2. `/posts/<id>` path segment · 3. `/permalink/<id>/` · 4. **last resort** `sha256(author_uid + text[:200] + local_day)` | The last-resort key is stable across re-crawls of the *same* content but changes if the text is edited — an edit then looks like a new post. Record which strategy produced the key in `extra.id_strategy` so this is diagnosable rather than mysterious |
| `permalink` | permalink field | 1. timestamp anchor `href` · 2. any `a[href*="story_fbid"]` · 3. any `a[href*="/posts/"]` | `None`. Strip `?__cft__` and other tracking params before storing, or every re-crawl looks like a changed permalink |
| `author_uid` | actor id | 1. actor anchor `href` → `/profile.php?id=<n>` · 2. vanity path segment · 3. `None` | `None` → the item gets no `author_id`. Note a **vanity name is not a stable id**: it can be changed by its owner. Prefer the numeric id and store the vanity in `authors.handle` |
| `author name` | actor name | 1. actor anchor text · 2. `aria-label` on the actor block | `None`. Never fabricate from the profile URL |
| `published_at` | exact unix `creation_time` | 1. timestamp anchor `aria-label` (carries an absolute date) · 2. `title` attribute on the timestamp element · 3. parse the relative text ("3 h", "Yesterday", "12 August") | **The most commonly silently-wrong field in the project.** Never write an exact-looking integer for a relative string |
| `published_prec` | `exact` | `exact` from an absolute date; `minute`/`hour`/`day` from a relative string; `relative` when you have only a bucket; `unknown` when nothing parsed | This column exists so a downstream query can tell a real timestamp from a guess. Setting it dishonestly is worse than leaving `published_at` NULL |
| `text` | message text | 1. the post message container after "See more" expansion · 2. concatenated text nodes of the message subtree | `None`. **Do not** fall back to the whole article's text — that sweeps in the author name, timestamp and reaction labels and silently poisons FTS |
| `is_pinned` | pinned flag | Pinned badge / "Pinned post" label in the post header | **`None`, not `False`.** Tri-state: `False` **only** when the parser positively observed the header without the badge; `None` when the selector matched nothing at all. A false negative here breaks the watermark stop rule (§9) *and* blinds the canary (§10), which is why the distinction is structural rather than stylistic |
| `is_sponsored` | sponsored flag | "Sponsored" label in the header | **`None`** when the header did not parse; `False` when it did and there was no label. Same reason |
| `reactions.total` | exact integer | Reaction summary text, `aria-label` on the reaction bar | **Rounded above 1k in the UI** (`1.2K` → 1200). Always `MetricDraft(approximate=True)` when parsed from the DOM; `approximate=False` only from a GraphQL integer |
| `reactions.<type>` | per-type counts | Reaction bar `aria-label`, if it enumerates | Usually absent from the DOM. Emit only what you actually observed; never distribute a total across types |
| `comments` count | integer | "N comments" text | Same rounding rule. Also **not** the same as the number of comments you can actually reach |
| `shares` count | integer | "N shares" text | Same rounding rule |
| media `url` | attachment URL | `img[src]` / `video[src]` / link-card anchor within the attachment subtree | `None`. Store the URL and know it is a **dead pointer** — Facebook CDN URLs are time-limited and token-bearing. This is why `media_mode` defaults to `link` and why the replay guarantee covers structured content only |
| media `kind` | attachment typename | Element type + presence of a play affordance | `file` as the honest fallback, not a guess between `image` and `video` |
| comment `platform_item_id` | comment node id | `comment_id=` in the comment permalink | Fall back to `sha256(post_id + author_uid + text[:200])`. Same edit caveat as posts |
| comment `parent_ref` | parent comment id | `reply_comment_id=` in the permalink, or nesting depth in the DOM | **Always write `parent_ref` even when you cannot resolve it to an id.** Facebook delivers replies whose parents are collapsed behind "View more replies", and an unresolvable ref is exactly what the parent-repair pass exists for |
| `more_remaining` | remaining count | The "View N more comments" affordance's number | **`None` when comments were not fetched at all**; `0` only when they were fetched and the tree is complete; the remainder when they were fetched and truncated. The tri-state is what stops `0` meaning both "complete" and "I did not look" — a partial tree that says it is partial is a correct result; a silently truncated one is a bug, and an unexamined one claiming completeness is worse than either |

**Two general rules that apply to every row:**

1. **Prefer attribute and structural selectors to class names.** `[role="article"]`,
   `[aria-posinset]`, `a[href*="story_fbid"]`, `[aria-labelledby]` survive a rotation that
   turns `x1y1aw1k` into `x9c3n2p`. Obfuscated class names are generated per build and
   should never appear in the parser.
2. **`parse()` is pure.** No network, no DB, no `time.time()` — use `env.captured_at`. This
   is enforced by `test_parse_needs_no_secrets`, which constructs the connector with
   `secrets={}` and parses every fixture. Without that test, "parse is pure" is a comment
   that decays the first time a field needs one more request.

---

## 9. The incremental watermark

There is no cursor. A scroll position is not addressable, so the durable state is an
**approximate content watermark** plus a **behavioural stop rule**.

```python
Cursor(
    kind=CURSOR_WATERMARK,
    axis=AXIS_TIME,
    axis_value=newest_published_at,          # the ordinal core compares for gap detection
    value=json.dumps({                        # OPAQUE to core. Core never looks inside.
        "newest_item_id": "...",
        "newest_published_at": 1756...,
        "newest_prec": "exact",
    }),
)
```

The cursor rides on the envelope as `cursor_after` and core writes it **only after** that
envelope commits. There is no `advance_cursor()` method to call and therefore no way to
advance past bytes you never stored.

### The stop rule

Stop scrolling after **`STOP_AFTER_SEEN` = 5 consecutive already-seen posts**.

Consecutive, not cumulative — a single old post surfacing mid-feed (a re-shared item, an
edit bumping a post) must not end the run.

### Guard 1 — empty database

On the first run for a target there is no watermark, so **the stop rule must not fire at
all**. Zero already-seen posts is not five consecutive already-seen posts, but the guard
must be explicit rather than emergent, because an off-by-one that treats "no watermark" as
"everything is known" produces a first run that collects nothing and reports success.

With no watermark, the run is bounded by `--limit`, `--max-scrolls` and
`Budget.deadline_ts` instead. The default first crawl is `--since 90d`, not "everything" —
an unbounded scroll of a large group is the single riskiest operation in the project.

### Guard 2 — pinned posts

Pinned posts sit at the top of a feed forever. Without a guard they are the first thing
every run sees, they are always already-seen, and they trip the counter on scroll one — so
every daily run after the first collects nothing and reports success. **`is_pinned` rows do
not count toward `STOP_AFTER_SEEN`.**

The same applies to `is_sponsored` rows, which are injected by the feed regardless of
recency, and which are also *not* what the watermark is tracking.

This makes `is_pinned` detection load-bearing rather than cosmetic. If a DOM rotation
breaks the pinned badge selector, the failure is not a missing column — it is a crawler
that silently stops collecting. **The canary must therefore track `is_pinned` fill rate
specifically** — and that is only possible because `is_pinned` is **tri-state** in
`ItemDraft` (`bool | None`, ARCHITECTURE §5). On a `bool = False` default its fill rate is
structurally 100% and the alarm can never sound, which would have made the canary's own
headline justification the one field it could not watch. Core's SUSPECT rule (c) — a
watermark stop at scroll ≤ 2 with zero new items — is the second, fill-rate-independent
guard on exactly this failure.

### Guard 3 — the overlap is not optional

Because the watermark is approximate and the feed order is not reliably chronological, the
run always re-reads an overlap rather than trusting the boundary. That overlap is what
`STOP_AFTER_SEEN` *is*: five known posts of evidence before you believe you have reached
familiar ground. Reducing it to 1 to save scrolls is a false economy that trades a few
seconds for silent gaps.

### What the watermark cannot do

Stated plainly, because these are permanent properties and not bugs to fix later:

- **It cannot detect edits.** A post whose text changed after you first saw it is
  already-seen and will not be re-read. Edits are recovered only when the post happens to
  fall inside a re-scan window; when they are, `item_versions` records them.
- **It cannot detect deletions — and neither can anything else in this connector.** This is
  the honest version, and an earlier draft got it wrong by handing the job to the absence
  sweep. The sweep only counts absence **inside a positively-covered range**
  ([../GOVERNANCE.md](../GOVERNANCE.md) §8.1 requirement 3), and Facebook claims `opaque`
  coverage on every envelope it will ever emit. So a feed re-scan produces **zero absence
  evidence by construction** and no Facebook item can ever accumulate an `absence_streak`
  from one.

  Therefore **`rescan_window_days = 0` for Facebook** and upstream deletes are simply not
  detected. A deleted post stays `visible` in your archive forever. That is a permanent
  property of an opaque transport, not a gap to close, and saying so costs nothing while
  pretending otherwise would spend the scarcest and most dangerous budget in the project —
  200 posts, 25 minutes, no feedback signal, the account as the failure currency —
  re-scrolling old posts for a signal the design discards on arrival.
  [./x.md](./x.md) §3.8 reaches the same conclusion for the same class of reason.

  *(If per-post deletion detection is ever wanted for Facebook, it is a different feature: a
  bounded re-navigation to individual permalinks, where "This content isn't available right
  now" genuinely is absence evidence — §6 — with its own item budget. Spec it separately.
  Do not conflate it with a feed re-scan; only one of the two works.)*
- **It cannot express a gap.** Coverage is `opaque`, so `gaps` will never carry a Facebook
  row. If you go away for two weeks and the feed moved more than a session can scroll, the
  middle is simply missing and the tool cannot tell you so. This is the honest cost of the
  transport, and it is the one place where Facebook is strictly worse than every other
  connector in the project.
- **`crawler cursors --rebuild` still works**, because the watermark was persisted on the
  envelope as `cursor_json`. After corruption or manual surgery, re-derive it from
  `SELECT target_id, cursor_json, max(seq) FROM envelopes WHERE cursor_json IS NOT NULL
  GROUP BY target_id` rather than guessing.

---

## 10. The canary

Facebook will rotate the DOM. The question is not whether the parser breaks but **how long
you go before noticing** — and the default answer, with no canary, is weeks, discovered
from empty columns during analysis.

The mechanism is `field_stats(run_id, field, seen, filled)`, written by core from
**`ParseResult.field_stats`** — `dict[str, tuple[int, int]]`, field → `(seen, filled)` — on
every run. Not from `diagnostics`, which is free text for a human reading a run report and
cannot carry a triple:

```sql
-- fill rate for this run, per field
SELECT field, filled * 1.0 / NULLIF(seen, 0) AS fill_rate
  FROM field_stats WHERE run_id = :run;

-- the trailing baseline: same field, same source, last 10 completed OK runs
SELECT fs.field,
       sum(fs.filled) * 1.0 / NULLIF(sum(fs.seen), 0) AS baseline
  FROM field_stats fs
  JOIN runs r ON r.id = fs.run_id
 WHERE r.source = 'facebook' AND r.status = 'ok'
   AND r.id < :run
 GROUP BY fs.field
 ORDER BY r.started_at DESC
 LIMIT 10;
```

**Alert when a field's fill rate falls below 50% of its trailing baseline over a run that
saw at least 20 items.** Both bounds matter: the ratio rather than an absolute threshold,
because fields legitimately differ (`title` is near-zero for Facebook posts and that is
correct); the minimum sample, because a three-post run tells you nothing and a canary that
cries wolf is a canary you disable.

### The fields to watch, and what a collapse means

| Field | A collapse means |
|---|---|
| `published_at` | The timestamp selector rotated. **Highest severity** — items keep landing, with NULL or wrong times, and the timeline query silently degrades |
| `is_pinned` | The pinned badge rotated → **the watermark stop rule will start firing on scroll one** and the crawler will quietly stop collecting while reporting success (§9, guard 2) |
| `platform_item_id` | The permalink shape changed → the connector has fallen through to the `sha256` last resort, and re-crawls will start creating duplicate items instead of updating |
| `text` | The message container rotated → items with no body. Also visible as a collapse in FTS row growth |
| `author_uid` | The actor anchor changed → orphaned items, and the author timeline query goes empty |
| `reactions.total` | The reaction bar changed → metric time series flatlines. Lower severity: the change-log design means a stalled metric writes nothing, so this shows up as *silence*, which is exactly what makes it worth watching explicitly |

### Two things that make the canary actually work

1. **Golden-parse snapshots.** `sha256(canonical_json(ParseResult))` per fixture per
   `parser_version`, committed. Bumping `parser_version` must change the snapshot
   **deliberately**. This is how you notice that an "innocent" selector tweak silently
   stopped populating a field — the canary catches breakage in production, the snapshot
   catches it in your own edit, which is a week earlier and far cheaper.
2. **Recovery is a reparse, not a re-crawl.** When the canary fires: fix the selector, bump
   `parser_version`, run `crawler reparse --source facebook`, and the stored envelopes
   re-populate every affected row back to the beginning. **This is the entire reason the
   project is raw-first**, and it is worth rehearsing once at M6 before you need it under
   pressure. `projection A/B diffing` — rebuild into a shadow table, diff per-field fill
   rates, then promote or discard — is deferred in [../../PLAN.md](../../PLAN.md) §11 with the second parser
   rewrite as its trigger, at which point it converts the canary from an alarm that fires
   three weeks late into a pre-flight check on your own fix.

---

## 11. Fixtures and tests

| Tier | What | Needs |
|---|---|---|
| `test_parse_*` | Table-driven over the M2 fixtures, in the style of `crawler-pages/tests/test_challenge.py`. Golden fixtures assert a full field set; a deliberately mangled fixture must yield `None`s, never a traceback | Nothing |
| `test_walls` | `detect()` over every saved wall document, asserting kind and verdict | Nothing |
| `test_parse_needs_no_secrets` | Construct the connector with `secrets={}`, parse every fixture | Nothing. **This is the enforcement mechanism for parse purity**, not a nicety |
| `test_no_selenium_after_reparse` | `uv sync --group core --group facebook`, full reparse, assert `'selenium' not in sys.modules` | Nothing |
| `test_pipeline_fixture` | `crawler tick --fixture tests/fixtures/facebook/` — the **entire** production pipeline: budget accounting, commit ordering, coverage checks, SUSPECT detection, upserts, sweeps. Run twice: identical item count, `last_seen` bumped, a second `envelope_fetches` row per envelope | Nothing |

**Fixtures are captured by `crawler capture`, never hand-copied out of DevTools**, because a
hand-copied fixture is not the bytes the pipeline would have stored and will diverge from
production in exactly the way that makes a test worthless.

Facebook fixtures are broadcast or joined content, so they may live in the repo. **No real
DM or private-group message from any source enters the repo, redacted or otherwise** — that
rule belongs to Telegram and Zalo, but it is worth knowing here so nobody relaxes it later
by analogy with Facebook.

---

## 12. Facebook-specific items to verify

| Claim | Status | Verify at |
|---|---|---|
| SeleniumBase CDP Mode yields decodable Facebook GraphQL response bodies | The API is **verified** from the SeleniumBase repo; whether it works against Facebook's specific flow, timing and body encoding is exactly what the spike tests | M2 |
| Whether `get_response_body` returns those bodies base64-encoded and/or compressed | Unverified. The maintainer states byte-level decompression is out of SeleniumBase's scope, so this is the most likely way candidate B fails in practice | M2 |
| GraphQL operation names (`GroupsCometFeedRegularStoriesPaginationQuery` and friends) | Third-party reporting only. **Record what you actually observe; do not trust this document** | M2 |
| `?sorting_setting=CHRONOLOGICAL` still sorting group feeds | Community-established, never documented by Meta | M2/M5 |
| Every DOM selector in §8 | **All unverified** — no request to facebook.com was made during planning | M2/M4 |
| Whether `m.facebook.com` server-renders group feeds | Unverified, and rejected on fingerprint grounds regardless (§2) | Only if §2 is ever re-opened |
| Meta's exact ToS clause on automated collection | Quoted second-hand; the prohibition itself is not in doubt | Before you decide how much account risk you accept |
| Whether `mbasic` retains any residual function | Retired December 2024. Treat as gone | Not worth checking |
| **Profile is warm enough to drive** | Not a fact — a policy, enforced. `crawler doctor` reports profile age from the user-data-dir mtime; `crawl` refuses below the threshold | **≥ 3 days** before M2 (a Page); **≥ 14 days** before M5 (a group). The clock starts at M0/P0 |

Every row above is also in the consolidated checklist,
[../../PLAN.md](../../PLAN.md) §12 — that is the list to work from; this one carries the
Facebook-specific consequence of each answer.
