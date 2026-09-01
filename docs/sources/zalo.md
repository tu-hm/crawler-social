# Source: Zalo

**v1 status: DEFERRED. No Zalo transport ships in the first release.**

> ⚠️ **This is the worst-documented source in the project.** Zalo publishes no English
> developer reference for anything outside Official Accounts, its packages changed on
> 2026-06-01, and its terms were force-updated in December 2025 and are being publicly
> contested. **Every claim in this document carries an explicit confidence label. Do not
> act on any `unverified` line without checking it yourself first** — §12 is the list.

---

## 1. The verdict, first

**There is no safe path to your personal Zalo history. Not a difficult one — none.**

The honest chain of reasoning, in the order the evidence forces it:

1. **Zalo has no personal-account API of any kind.** *(verified)* Across the whole developer
   portal — Official Account API, Social API, ZNS API, Zalo Shop, Mini App, and the
   Android/iOS/Java/.NET/PHP SDKs — there is no product that grants a person programmatic read
   access to their own Zalo conversations. Social API covers login and basic profile only.
2. **The one surface that genuinely reads conversation history is business-gated.** *(verified
   endpoints; likely gating)* The OA OpenAPI has real, documented history endpoints — but
   getting an OA requires a Vietnamese business licence (*giấy phép kinh doanh*) or a
   household-business registration, plus the legal representative's national ID, and API
   access sits behind a paid package.
3. **The one surface an individual can use for free reads no history at all.** *(verified)*
   The Zalo Bot Platform is free to individuals and is a Telegram-Bot-API clone — and it is
   strictly forward-only. It creates a new inbox; it does not recover an old one.
4. **The only living unofficial client cannot read 1:1 DM history.** *(verified)* `zca-js`
   exposes 149 API methods including `getGroupChatHistory` for groups — and **no** 1:1
   conversation history method exists among them.
5. **Browser automation is worse than all of the above, not a fallback below them.** *(likely)*
   §7.

So the thing the user most wants — "archive my Zalo DMs, including what is already there" — is
not available on any transport at any price. Everything else in this document is about what
you can have *instead*, and what it costs.

**Recommendation for v1, plainly: build nothing.** Declare the three transports in the
registry with honest capability notes, build the `user_withdraw`-shaped purge path generically
for every source, add the `containers.content_unavailable` flag, and stop. Deferring is not
hand-waving; it is the conclusion the evidence forces, and the plan is stronger for saying so.

---

## 2. Three transports, one source, declared separately

Not one connector with a `mode=` flag. They differ in identity namespace, auth lifecycle,
whether backfill exists at all, whose data it is, and legal posture. A mode flag would smear
those differences into runtime branches and would invite daily-routine code to assume backfill
exists when for two of the three it does not.

Registry mechanics are in [../ARCHITECTURE.md](../../ARCHITECTURE.md).

```python
REGISTRY = {
    ...
    "zalo.oa":   "connectors.zalo.oa:build",
    "zalo.bot":  "connectors.zalo.bot:build",
    "zalo.user": "connectors.zalo.user:build",   # gated; never started by the scheduler
}
```

| | `zalo.oa` | `zalo.bot` | `zalo.user` |
|---|---|---|---|
| Tier | **A — cleanly possible** | **B — possible, limited** | **B — possible, risky** |
| What it reaches | conversations between users and **your OA**; OA-created groups | conversations with **your bot** | your own account: friends, groups, live message stream |
| Backfill | **yes** — 10 messages per request, offset-paginated | **none** | **groups only**; no 1:1 method exists |
| Gate | Vietnamese business licence + paid package | free, individual signup | none — and that is the problem |
| Transport | REST + webhook | long-poll or webhook | Node sidecar (`zca-js`) over a local socket |
| `sources.tos_class` | `official_api` | `official_api` | `prohibited` |
| Ban risk | none | none | **real and evidenced** |
| Ships in v1 | no | no | no |

`capabilities_note()` is where this becomes visible to the user, because no enum can say it:

```
zalo.oa   -> "backfill: yes, but 10 messages per request via /oa/conversation, offset-paged.
              Requires a business-verified Official Account and a paid package.
              Reaches OA<->user threads and OA-created groups ONLY -- never your
              personal chats or your personal groups."
zalo.bot  -> "backfill: NONE. getUpdates and webhooks deliver only what arrives after
              the bot exists. There is no getChatHistory. This creates a new inbox;
              it does not recover an old one."
zalo.user -> "backfill: groups only, via getGroupChatHistory; no 1:1 DM history method
              exists in the library at all. Ban risk is documented by the library's
              own README. Never run on your primary account."
```

---

## 3. Tier A — `zalo.oa`: real, and out of reach for a personal tool

### 3.1 The endpoints exist and the shape is good

*(verified — from a GitHub mirror of Zalo's official developer docs, snapshot pushed
2025-12-10; see §11 on sourcing)*

```
GET https://openapi.zalo.me/v2.0/oa/listrecentchat?data={"offset":0,"count":5}
GET https://openapi.zalo.me/v2.0/oa/conversation?data={"user_id":2512523625412515,"offset":0,"count":5}
Header: access_token: <oa_access_token>
```

Both documented as *"mỗi request được lấy tối đa 10 tin nhắn"* — a hard cap of **10 messages
per request**, offset-paginated, `offset=0` being the most recent. Element schema, identical
for both:

```json
{"src":1,"time":1619401853770,"type":"text","message":"Chào shop",
 "message_id":"92e5d851aa8178dd2192","from_id":"2512523625412515",
 "to_id":"3120036654733951760","from_display_name":"Khoa Pham",
 "from_avatar":"https://s240-ava-talk.zadn.vn/...",
 "to_display_name":"OA","to_avatar":"https://..."}
```

`src`: 0 = OA→user, 1 = user→OA. `time` is epoch **milliseconds**. `type` ∈ `text`, `voice`,
`photo`, `GIF`, `link`, `links`, `sticker`, `location`, with conditional `links[]`, `thumb`,
`url`, `description`, `location`.

**Version skew, unresolved:** the send endpoints are documented at v3.0, these two reads at
v2.0, and third-party SDKs (`ChickenAI/zalo-node-oa`, `ttpro1995/zalo-python-sdk`) call the
same paths at v3.0. Both may resolve. *(unverified — test both before writing a client.)*

**10 messages per request is a real constraint.** At a reported 100 req/min tier limit that is
a **1,000 messages/minute ceiling**, which makes any substantial historical backfill a
multi-hour job. Whether `offset` has a maximum — and therefore whether deep backfill is
possible at all — is *(unverified)*.

### 3.2 Auth: 25-hour token, single-use rotating refresh token, PKCE

*(verified — and it contradicts the widely-cited third-party claim of a 1-hour token)*

Official docs: *"Thời hạn hiệu lực của Access Token: 25 giờ"* and *"Thời hạn hiệu lực của
Refresh Token: 3 tháng"*. The refresh token is **single-use** and returns a new refresh token
on every exchange. The authorization code is single-use, valid 10 minutes. OAuth v4 with PKCE:
`code_challenge = Base64(SHA-256(ASCII(code_verifier)))`, verifier 43 chars, unique per request.

```
POST https://oauth.zaloapp.com/v4/oa/access_token
Content-Type: application/x-www-form-urlencoded
Header: secret_key: <app_secret>
Body:   code=<auth_code>&app_id=<app_id>&grant_type=authorization_code
```

**The single-use refresh token demands crash-safe rotation.** If the process dies after
spending a refresh token but before persisting its replacement, the connector is locked out
until a human re-authorises in a browser — a real, entirely avoidable outage. The frozen
design:

1. `flock` a `zalo.token.lock`.
2. Write the **new** refresh token to Keychain **before** using the new access token.
3. Keep the previous value under Keychain account `refresh_token.prev` for one cycle, so a
   half-failed rotation is recoverable.

### 3.3 Least-privilege scopes

*(verified)* Nine permission groups exist; an ingestion-only connector needs exactly **four**,
and none of them is a send scope:

- Quyền quản lý tin nhắn người dùng — gates `listrecentchat` + `conversation`
- Quyền quản lý thông tin OA — follower list, profiles, tags
- Quyền nhận sự kiện quản lý tin nhắn — message webhooks
- Quyền nhận sự kiện quản lý người dùng — follower/consent webhooks, including `user_withdraw`

Worth stating in the config as a deliberate choice: **no send scope is ever requested.**

### 3.4 Webhooks are the right primary transport — and a laptop cannot host one

*(verified)* Zalo POSTs JSON with `X-ZEvent-Signature`, where
`mac = sha256(appId + data + timeStamp + OAsecretKey)` and `data` is the raw JSON body string.
Message events: `user_send_text`, `user_send_image`, `user_send_link`, `user_send_audio`,
`user_send_video`, `user_send_sticker`, `user_send_location`, `user_send_business_card`,
`user_send_file`, plus OA-side send/seen/reaction events. Multimedia adds
`attachments[].payload.{url,thumbnail,description}`; a reply carries `quote_msg_id`.

**But a webhook needs a publicly reachable HTTPS endpoint, and a personal Mac does not have
one.** The options are a tunnel (Cloudflare Tunnel, Tailscale Funnel) or a small always-on
relay — either way a second uptime dependency with its own secret, and the only part of the
whole system that cannot live on the laptop. The degraded alternative is polling
`listrecentchat` at 10 messages a call. Both are real architectural costs and neither is a
detail to solve later.

*(The exact concatenation order for the signature is **disputed in Zalo's own developer
community forum**, with different orderings documented in different places. Write the verifier
as a pure function over `(headers, raw_body)` with fixtures for a valid and an invalid
signature, and budget real time to settle it against a live webhook.)*

### 3.5 OA group chats (GMF) are not your groups

*(verified)* "Quản lý nhóm (GMF)" lets an OA create and administer group chats with customers.
Gating, verbatim: *"Tính năng Quản lý nhóm chỉ dành riêng cho OA xác thực và đang sử dụng Gói
Nâng cao hoặc Premium."*

```
GET  https://openapi.zalo.me/v3.0/oa/group/getgroupsofoa?offset=0&count=5
GET  https://openapi.zalo.me/v3.0/oa/group/conversation?group_id=<id>&offset=0&count=2
POST https://openapi.zalo.me/v3.0/oa/quota/group
```

Group size is a **purchased** asset capped at 10 / 50 / 100 members. Critically: **there is no
API to join or read an existing personal Zalo group.** GMF covers only groups the OA itself
created. Note also that the gating text names the *retired* "Nâng cao / Premium" packages
(§3.6), so which current tier carries GMF is *(unverified)*.

### 3.6 The gate: a business licence, and a paid tier

*(likely — Vietnamese trade sources, consistent with each other, not confirmed against Zalo's
own current policy page)*

Verification requires a **Giấy phép kinh doanh** — original or notarised copy, still valid —
or a household-business registration certificate, plus the **CCCD** of the legal
representative or household-business owner, matching the GPKD. Review takes up to 7 days. An
individual with no GPKD gets an unverified *"OA phổ thông"* / advertising profile, explicitly
described as unable to use messaging, broadcast, chatbot or voice-calling features — and the
messaging surface is exactly what the read APIs sit behind. **An unverified OA is useless for
ingestion.**

**Packages changed on 2026-06-01** *(likely)*: Cơ bản (free, auto-granted to a *verified* OA),
Tiêu chuẩn 1,000,000₫/yr, Tăng trưởng 2,500,000₫/yr, Toàn diện 6,000,000₫/yr. Reported
per-tier API limits: Tăng trưởng **100 req/min** with up to 3 connected apps; Toàn diện
**2,000 req/min** with unlimited apps. Chatbot/API access is reported to require a paid tier.

**This directly contradicts the official docs**, whose appendix states a flat **4,000
requests/minute** per app for the OA API and exposes `X-RateLimit-Limit` /
`X-RateLimit-Remain` headers with `error code = -32` on breach. That appendix page is footered
©2023 and is very likely stale. **Treat the per-tier numbers as operative and read
`X-RateLimit-Limit` from a live response before sizing any backfill.**

**Blunt conclusion:** `zalo.oa` is a *business capability the user could unlock by registering
a hộ kinh doanh*. It is not something to assume, and it does not reach personal chats even
once unlocked.

### 3.7 ZNS and ZCA are not ingestion, at all

*(verified)* ZNS (Zalo Notification Service) is outbound notification only. Every retrieval
endpoint concerns your own assets — template list/detail, quota, send status, quality rating —
and its webhooks are delivery/quota/quality/template events. Zalo Cloud Account is a prepaid
balance wallet linked to the Zalo App. **Neither reads conversations.** Drop both from the
ingestion design; mention ZCA only as the billing prerequisite if the OA path is ever taken.

---

## 4. Tier B — `zalo.bot`: free, individual, and forward-only

*(verified where noted; limits are secondary-sourced)*

- Console: `https://bot.zaloplatforms.com` · Docs: `https://bot.zapps.me/docs` · API base:
  `https://bot-api.zapps.me` · Token format `numeric_id:secret`.
- The platform describes itself as supporting chatbot integration *"cho cá nhân & doanh
  nghiệp"* — **individuals included**, no business registration named.
- It is a **Telegram Bot API clone**: `getMe`, `getUpdates`, `setWebhook`, `testWebhook`,
  `deleteWebhook`, `getWebhookInfo`, `sendMessage`, `sendPhoto`, `sendSticker`, `sendVoice`,
  `sendChatAction`; SDKs add `editMessageText`, `deleteMessage`, pin/unpin, `banChatMember`,
  promote/demote, `setChatKeyboard`, `uploadFile`, `getFileInfo`, `getFileDownloadUrl`.
- `getUpdates` polling and webhook mode are **mutually exclusive**.
- Outbound text caps at **2,000 characters**. *(verified via an independent integration guide)*
- Groups are supported but **require an `@mention` to trigger the bot**, and this is not
  configurable. Some marketplace-bot setups reportedly cannot be added to groups at all.
- **History reading is explicitly listed as not supported.** *(verified via an independent
  integration guide, docs.openclaw.ai/channels/zalo)*
- Free "Basic" tier: 3 bots, **50 users per bot**, **3,000 messages/month**, group chat in
  Beta. Pro at 129,000₫/month listed as coming soon. *(likely — pricing page, secondary)*

**The architectural upside is real and worth naming.** The Python client is
`python-zalo-bot` 0.1.9 (2026-01-27, `requires_python >=3.8`), an explicit **fork of
python-telegram-bot**. So the connector would be close to a copy of a path the project has
already built — a genuine validation that the capability contract crosses platforms at near
zero marginal cost.

> **Maturity caveat, worth one line:** `python-zalo-bot` 0.1.9's own PyPI metadata still
> points its Homepage and Issues URLs at `github.com/yourusername/python-zalo-bot` — an
> unedited template placeholder. *(verified against the PyPI JSON API, 2026-09-01.)* That is
> not disqualifying, but it is a 0.1.x library from a single publisher and should be pinned
> and vendored like Telethon.

**Frame it correctly to the user, or the feature will disappoint:** `zalo.bot` builds a **new
inbox going forward**. It recovers nothing. If the user's goal is "archive the Zalo
conversations I already have," this transport does not serve it at all.

---

## 5. Tier B — `zalo.user`: the unofficial client, and its hard ceiling

### 5.1 `zca-js` is the only living option

*(verified — GitHub API, 2026-09-01)*

| | |
|---|---|
| Repo | `RFS-ADRENO/zca-js`, MIT, TypeScript |
| Stars / forks / open issues | 619 / 289 / **67** |
| Created | 2024-07-15 |
| Latest release | **v2.1.2, 2026-03-17** |
| Last push | 2026-06-25 — **dependabot dependency bumps only since 2026-04-12** |
| Archived | no |

Feature work has visibly slowed. Treat this as a project that will be **broken**, not merely
degraded, at unpredictable intervals.

### 5.2 The ceiling: no 1:1 DM history method exists

*(verified — read directly from the source tree)*

`getGroupChatHistory(groupId, count = 50)` exists and hits `{group service}/api/group/history`,
returning `{groupMsgs, more, lastActionId, lastActionIdOther}`. There is **no corresponding
method for a 1:1 conversation** anywhere in the 149 wrapped APIs. The list includes
`getAllFriends`, `getAllGroups`, `getGroupInfo`, `getGroupMembersInfo`, `getArchivedChatList`,
`getPinConversations`, `getHiddenConversations`, `getUserInfo`, `getMultiUsersByPhones` and
`listen` (websocket) — and nothing shaped like `getChatHistory`.

So the realistic ceiling of the unofficial path is:

| | |
|---|---|
| ✅ | full friend and group directory |
| ✅ | paginated **group** history |
| ✅ | live stream of new DMs and group messages **from the moment you connect** |
| ❌ | **your existing 1:1 history — out of reach** |

There is a `custom` escape hatch for calling arbitrary endpoints, so an undocumented DM-history
endpoint *may* exist. *(unverified speculation. Given no such method appears in 149 wrapped
APIs, assume no until someone demonstrates otherwise. Do not plan around it.)*

### 5.3 Auth, and why plain HTTP scraping of Zalo Web is off the table

*(verified — from `src/zalo.ts`)*

Credentials are `{imei, cookie[], userAgent, language}`. `loginQR()` renders a QR the user
scans with the Zalo mobile app, derives `imei` via `generateZaloUUID(userAgent)`, and captures
the `chat.zalo.me` cookie jar. Those credentials persist and replay through
`login(credentials)`, so QR re-scanning is not a per-run cost. The login response yields:

- **`zpw_enk`** — an AES secret key, stored as `ctx.secretKey`
- `zpw_service_map_v3` — per-service base URLs
- `zpw_ws` — the websocket endpoint

**Every request then encrypts its params:**
`const encryptedParams = utils.encodeAES(JSON.stringify(params))`.

Two consequences, and the second is what kills browser automation in §7:

1. Zalo Web is **not a scrapable JSON API** — you must reimplement the session-keyed crypto,
   which is exactly the maintenance burden `zca-js` carries and exactly why it breaks when
   Zalo changes it.
2. **Only one web listener per account.** Opening Zalo in a browser stops the listener — so
   the connector actively contends with the user's own Zalo Web usage.

### 5.4 Ban risk is evidenced, not hypothetical

*(verified — the project's README and its issue tracker)*

The README, verbatim: *"Using this API could get your account locked or banned. We are not
responsible for any issues that may happen. Use it at your own risk."*

Concrete enforcement signals from the tracker:

| Date | Issue |
|---|---|
| 2026-03-09 | *"[HELP] bị zalo xóa hết danh sách bạn bè"* — Zalo wiped the user's entire friend list |
| 2026-06-09 | *"[Question] Tìm số điện thoại quá nhiều lần trong 1 giờ có thể bị xem là hoạt động bất thường"* — phone lookups above a threshold flagged as abnormal |
| 2025-08-20 | *"[BUG] Lỗi api getUserInfo(userId) - Vượt quá số request cho phép"* — server-side per-request quota |

Zalo is clearly applying server-side behavioural limits to this traffic. And **account loss on
Zalo in Vietnam is materially worse than on most platforms**: the account is phone-number-bound
and carries banking notifications, government services and work identity. This is not a
throwaway Reddit account getting rate-limited.

### 5.5 A Node sidecar, because the Python ecosystem is dead

*(verified)* `Its-VrxxDev/zlapi`, the Python equivalent, is **archived and read-only** — last
push 2024-11-24. `zalo-python-sdk` and `zalo-sdk` on PyPI cover only Official Account APIs and
are years stale. There is no maintained Python route to the unofficial surface.

So `zalo.user` means a **Node sidecar** speaking to the Python core over a local socket — the
pattern demonstrated by `darkamenosa/openzca` and `diendh/zca-bridge`, both Node/TS bridges
wrapping `zca-js`. That is a whole extra runtime, supervisor and failure class **for a source
that cannot deliver DM history**.

The architecture already accommodates it at zero redesign cost: the frozen error taxonomy
projects onto an integer exit table (`0 / 69 / 75 / 77 / 78 / 86`) precisely so the same
dispositions survive a process boundary. But "the design can host it" is not "it should be
built."

### 5.6 Gating, if it is ever built

- Hand-edited config flag **plus** `--i-accept-ban-risk` on the command line. Not a config
  default.
- **Never started by the scheduler.** No LaunchAgent, no tick lane.
- **Never on the primary account.** A dedicated secondary the user can afford to lose.
- No bulk friend or phone enumeration — `listen` plus targeted `getGroupChatHistory` only.
- Human-plausible pacing.
- The user accepts that the listener conflicts with their own Zalo Web session (§5.3).

---

## 6. Tier C — not possible

| Want | Status | Why |
|---|---|---|
| Your existing 1:1 Zalo DM history | **impossible on every transport** | no personal API; OA reaches only OA threads; bot reaches only bot threads; `zca-js` has no 1:1 history method |
| Your existing personal *group* history | **only via `zca-js`**, at ban risk | no official API can join or read a personal group; GMF covers OA-created groups only |
| A machine-readable personal export | **no** | see below |
| E2EE-upgraded conversations, on any transport | **no** | §8 |
| Reading Zalo Web with a driver | **rejected permanently** | §7 |

**The usual "just export it manually" fallback does not work on Zalo.** *(verified for the
backup scope; likely for the export claim)* Zalo's official backup covers **text messages
only**, stored to Zalo's own servers; photos go to the user's linked Google Drive and only
those sent in the last **120 days**; voice messages, files and videos are explicitly **not**
backed up; images from communities and groups over 100 members are excluded. Vietnamese guides
describe a Zalo PC *"Xuất dữ liệu"* flow producing a `.zip`, but also report the archive is
encrypted and can only be re-imported into Zalo PC, not read externally.

So unlike X (a clean data-archive ZIP → `Cap.FILE_IMPORT`) or Telegram (Desktop's JSON export),
**"ask the user to export and ingest the file" is not a viable Zalo path.** If it were, it
would be the recommendation, because it is free, complete and carries zero ToS risk — which is
exactly the reasoning applied to X DMs. *(Whether the `.zip` is genuinely re-import-only is
**unverified** and deserves five minutes of the user's own hands-on checking; it is the
difference between "no manual path" and "a viable manual path.")*

---

## 7. Selenium against `chat.zalo.me` is rejected permanently

**No trigger. This is not deferred; it is closed.**

Three independent reasons, in order of weight:

1. **Fidelity.** *(verified)* The underlying XHR params are AES-encrypted with the
   per-session `zpw_enk` key (§5.3), so a driver gets **no usable network JSON** and is
   reduced to scraping a virtualised, obfuscated-class-name DOM. Every message type — sticker,
   location, business card, quoted reply, recalled message — becomes a separate brittle DOM
   rule, and **message ids are not exposed in the DOM at all**, so cross-run deduplication has
   no stable key. Without a stable id there is no `platform_item_id`, and without that there
   is no upsert conflict target, no cursor, and no absence sweep.
2. **The data may not be there.** *(likely)* Zalo Web/PC history sync is partial and unreliable
   by design — call history is not synced to desktop at all, and missing-old-messages on
   desktop is a routine, widely documented complaint in Vietnamese support forums. You would
   be scraping a DOM that may simply not contain the history you want.
3. **Same risk, worse everything else.** It presents as the same unauthorised automated
   session as `zca-js`, on the same account, with the same ban exposure — while being far more
   fragile and roughly an order of magnitude slower.

**There is no configuration in which Selenium beats `zca-js` here.**

### Why this rejection belongs in the plan rather than in a footnote

This is the concrete proof that **transport is a per-connector decision, not a house default.**

> Browser automation is right for Facebook precisely because Facebook *renders the content you
> want* into the DOM. Zalo does not clear that bar.

Any future reviewer — or future agent — who reaches for "we already have Selenium, point it at
Zalo" should find this section and the reason it fails. That is the whole argument for the
capability-contract architecture, stated in one source.

---

## 8. End-to-end encryption is a hard ceiling, and the schema must be able to say so

*(likely — Vietnamese press and Zalo's own help pages)*

Zalo's E2EE is built on the Signal Protocol, applies **per individual conversation** (and small
groups), and must be **enabled manually by the user** — unlike WhatsApp, it is not the default.
Any conversation upgraded to E2EE is unreadable by Zalo's servers and therefore unreachable by
the OA API, the Bot API, and any server-side reconstruction.

**Design consequence, and it generalises beyond Zalo:** the connector must be able to record
*"this thread exists and its content is not retrievable"* rather than silently producing an
empty or partial thread. That is what `containers.content_unavailable` is for in the frozen
schema:

```sql
content_unavailable INTEGER NOT NULL DEFAULT 0 CHECK (content_unavailable IN (0,1)),
                -- e.g. a Zalo thread the user upgraded to E2EE: it exists and
                -- its content is not retrievable. The archive must be able to
                -- SAY that rather than silently look complete.
```

Without it, Zalo coverage is silently incomplete and gets **more** incomplete over time as
users enable E2EE — a failure mode that only surfaces when someone trusts the data.

*(Whether `getGroupChatHistory` returns plaintext for an E2EE-upgraded group is
**unverified**.)*

---

## 9. Two schema consequences that ship even though the connector does not

### 9.1 `user_withdraw` — Zalo pushes data-subject-rights requests at you

*(verified — official docs, verbatim)*

> *"Khi người dùng Zalo yêu cầu thực hiện quyền chủ thể dữ liệu với Bên Kiểm soát dữ liệu cá
> nhân (bao gồm rút lại sự đồng ý; xoá dữ liệu; hạn chế xử lý dữ liệu; phản đối xử lý dữ liệu),
> Nền tảng Zalo thực hiện thông báo các yêu cầu này đến các bên Đối tác để tiến hành xóa dữ
> liệu tương ứng của chủ thể dữ liệu."*

```json
{"app_id":"1519815886763164419","oa_id":"607812198688816074",
 "user_id_by_app":"2836211951173838919","user_id":"4558424660619243532",
 "event_name":"user_withdraw","timestamp":"1727770763679"}
```

**This is the single most important schema consequence in the whole Zalo brief, and it is why
the purge path is built for every source rather than for Zalo alone.** A raw-first store whose
invariant is "never delete the original payload" cannot satisfy it. Telegram and Facebook DMs
contain the same third parties — they just do not send you a webhook about it.

The resolution is in [./docs/GOVERNANCE.md](../GOVERNANCE.md) §7: `purge --person`,
indexed by `authors.actor_hmac`, cascading to items, `item_versions`, media blobs, the FTS
index and the envelopes; leaving a content-free `redactions` row with `block_reingest = 1` so
tomorrow's run cannot silently re-create what you just deleted.

**Build it generically. Zalo is the source that makes the requirement legible, not the source
it exists for.**

### 9.2 Zalo user ids are scope-local — design the identity key around this case

*(verified)* The same human carries at least three different identifiers:

| Identifier | Scope |
|---|---|
| `user_id` | relative to the **OA** (empty string when the app is not linked to an OA) |
| `user_id_by_app` | relative to your **Zalo App** |
| the `zca-js` uid | an entirely separate personal-surface namespace |

So the identity key is `(source, scope_id, external_id)` where `scope_id` is the
`oa_id` / `app_id` / `bot_id` / account uid — and cross-scope resolution is a **separate,
explicitly manual, user-confirmed** operation, never an implicit join.

The frozen schema already encodes this: `authors` is `UNIQUE (source, actor_hmac)` with
`actor_hmac = HMAC(source ‖ platform_uid, pepper)`, and cross-platform linking lives in
`persons` / `author_person_links` with `CHECK (linked_by IN ('manual','self'))` — deliberately
**no `'inferred'` value**, so automated matching cannot be added without a schema migration.
For Zalo, `platform_uid` must be namespaced: `zalo.oa:<oa_id>:<user_id>`, not a bare
`user_id`. Reddit and X have global user ids; Telegram's are per-bot-scoped; **Zalo is the
strictest case, which makes it the right one to design the key around.**

### 9.3 Media: snapshot bytes or store nothing

*(verified)* Every media reference Zalo returns — `url`, `thumb`, avatar links — points at
`*.zdn.vn` CDN hosts with **expiring, token-bearing paths**. A store that keeps only the URL
keeps a link that will be dead by the time anyone re-parses, which defeats the point of
keeping the payload.

This collides with the project-wide `media_mode = 'link'` default. **The honest resolution:**
`link` for Zalo means metadata only (`kind`, `mime`, `byte_len`, dimensions, `caption`) with a
stored URL flagged as expired-by-construction; anything the user actually wants must be
`media_mode='download'`, per-target, at ingest time, behind a mime allowlist and `max_bytes` —
and the downloaded bytes must be reachable from the redaction index so a `user_withdraw` purge
removes them too. Lazy fetch on first access is not an option: by then the URL is dead and, for
Zalo, re-crawling may be impossible.

This is the same "dead pointer" property Telegram media has (`file_reference` expires) — see
[./docs/sources/telegram.md](./telegram.md) §11. The replay guarantee covers
**structured content only**, on both sources.

---

## 10. Triggers — when to revisit, and with what

| Transport | Trigger | Estimated shape |
|---|---|---|
| `zalo.bot` | The user wants a **new** Zalo inbox archived going forward and accepts that **no history comes with it** | ~1 week — roughly a copy of the Telegram bot path; `python-zalo-bot` is a python-telegram-bot fork |
| `zalo.oa` | The user registers a **hộ kinh doanh** or a company and verifies an OA on a paid tier | REST + webhook + a tunnel; the webhook host is the real cost |
| `zalo.user` | The user **explicitly asks** AND has a secondary account they can afford to lose. Gated behind a hand-edited config flag and `--i-accept-ban-risk`; never started by the scheduler; never on the primary account | Node sidecar + local socket + the exit-code bridge |
| Selenium on `chat.zalo.me` | **none. Rejected permanently** | — |
| Manual `.zip` import | Someone demonstrates that Zalo PC's *"Xuất dữ liệu"* archive is machine-readable | `Cap.FILE_IMPORT`, the same shape as the X archive importer and the spool drain |

The last row is the one worth actively checking: if that `.zip` turns out to be readable, Zalo
goes from "experimental scraper" to "file importer" and the whole risk picture changes.

---

## 11. Vietnamese-language sources

Zalo's substantive documentation is Vietnamese-only, and most of it is trade press rather than
first-party. Listed here so the user can re-check without repeating the search.

**Official docs (via a GitHub mirror — see the sourcing note below)**

- `raw.githubusercontent.com/doanhidm1/zalo-docs/master/` — full mirror of developers.zalo.me,
  266 pages, snapshot pushed 2025-12-10. The conversation-history, OAuth, permission-group,
  webhook, GMF, ZNS and rate-limit pages cited above all come from here.

**OA verification and registration**

- `cnv.vn/xac-thuc-zalo-oa/` — GPKD + CCCD requirements, review timeline
- `misaeshop.vn/38111/cach-dang-ky-zalo-oa/` — registration walkthrough
- `subiz.com.vn/blog/zalo-oa-la-gi.html` — what an unverified "OA phổ thông" can and cannot do

**Package restructure (effective 2026-06-01)**

- `miniai.vn/so-sanh-goi-zalo-oa-cu-va-moi/` — old vs new package comparison
- `v9.com.vn/bang-gia-zalo-oa-2026/` — 2026 price table and per-tier API limits

**Zalo Web / PC history sync (the §7 evidence)**

- `thegioididong.com/hoi-dap/10-cach-sua-loi-khong-xem-duoc-tin-nhan-zalo-tren-may-tinh-1369367`
- `fastcare.vn/blog/zalo-web-bi-mat-tin-nhan-cu.html`
- `gearvn.com/blogs/thu-thuat-giai-dap/cach-sao-luu-tin-nhan-zalo-tren-laptop` — the
  *"Xuất dữ liệu"* flow

**E2EE**

- `vietnamnet.vn/zalo-chinh-thuc-ho-tro-ma-hoa-dau-cuoi-tang-cuong-bao-ve-thong-tin-nguoi-dung-i411123.html`
- `help.zalo.me/huong-dan/chuyen-muc/quan-ly-tai-khoan-zalo/sao-luu-va-khoi-phuc/` — backup scope

**Terms and legal context**

- `vci-legal.com/news/zalos-new-terms-under-fire-for-multiple-legal-violations` — the December
  2025 forced terms update, and the PDPL consent argument against it
- `e.vnexpress.net/news/tech/enterprises/users-confused-by-sudden-terms-of-use-update-at-vietnam-s-most-popular-messaging-app-zalo-4999823.html`
- `vietanlaw.com/decree-356-2025-vs-decree-13-2023-updates-on-personal-data-protection-in-vietnam/`

**Libraries**

- `github.com/RFS-ADRENO/zca-js` · `github.com/Its-VrxxDev/zlapi` (archived) ·
  `github.com/darkamenosa/openzca` · `github.com/diendh/zca-bridge` ·
  `pypi.org/project/python-zalo-bot/` · `docs.openclaw.ai/channels/zalo`

> **Sourcing note, disclosed honestly.** The project's hard rule is zero requests to
> `zalo.me`. During research, three read-only documentation pages under `developers.zalo.me`
> and `help.zalo.me` were fetched before the work switched to the GitHub mirror above. No
> login was attempted and no product surface (`chat.zalo.me`) was touched. Everything cited in
> this document is either from the mirror, from third-party sources, or from the GitHub API.

---

## 12. Verify before building — the full list

**This section is the most important one in the document.** Zalo's documentation quality is
poor enough that acting on an unchecked claim is a real risk.

| # | Claim | Status | How to settle it |
|---|---|---|---|
| 1 | `listrecentchat` / `conversation` also resolve at `/v3.0/` as third-party SDKs assume | **unverified** | Test both paths against a live token before writing the client |
| 2 | Whether OA conversation history has a **server-side retention window**, and whether `offset` has a maximum | **unverified** | Determines whether deep backfill is possible at all. Page until it stops |
| 3 | Which of the four post-2026-06-01 packages unlocks **API access**, and which carries **GMF** | **unverified** — the GMF docs still name the retired "Nâng cao / Premium" tiers | Ask Zalo sales, or read the current package page |
| 4 | Whether the free "Cơ bản" tier permits **any** OA API calls | **unverified** | Same |
| 5 | The real OA API rate limit — official docs say a flat 4,000 req/min (©2023 footer); trade sources say 100 (Tăng trưởng) / 2,000 (Toàn diện) | **contradictory** | Read `X-RateLimit-Limit` from one live response |
| 6 | The exact `X-ZEvent-Signature` concatenation order | **disputed in Zalo's own dev forum** | Determine empirically against a live webhook. Write the verifier as a pure function with valid/invalid fixtures |
| 7 | Zalo Bot API rate limits, `getUpdates` retention semantics, whether `offset` can replay, media size limits | **unverified** — `bot.zapps.me/docs` returned only an index; its API reference sub-pages 500'd | Retry the docs site; otherwise measure |
| 8 | Whether the 50-users-per-bot Basic cap counts **distinct lifetime** or **concurrent** users, and what happens **at** the 3,000-msg/month cap | **unverified** | Matters: if it drops messages rather than erroring, data is lost silently |
| 9 | Whether an individual with **no business registration** can complete Zalo Bot Platform signup end to end | **unverified** — the pricing page says "cá nhân & doanh nghiệp"; the identity requirements were not checked | Attempt the signup. It is free |
| 10 | Whether OA verification accepts a **hộ kinh doanh** registration rather than a full company | **likely** — trade sources say yes; not confirmed against Zalo's own current policy | This is the difference between "unreachable" and "a cheap afternoon of paperwork" |
| 11 | Whether `zca-js`'s `custom` escape hatch can reach an undocumented 1:1 history endpoint | **unverified speculation** | Assume no. Do not plan around it |
| 12 | Whether `getGroupChatHistory` returns plaintext for an **E2EE** group | **unverified** | Decides whether `content_unavailable` also applies to groups |
| 13 | Whether Zalo PC's *"Xuất dữ liệu"* `.zip` is genuinely encrypted / re-import-only | **unverified** — one Vietnamese guide says so | **Five minutes of the user's own hands-on checking.** This is the highest-value unknown in the document (§10) |
| 14 | Zalo's specific Terms-of-Use clause on **automated access / scraping** | **not located** | The claim that unofficial clients violate Zalo policy comes from the libraries' own disclaimers and community consensus, **not** from a cited ToS provision. Do not assert what Zalo's ToS says |
| 15 | Zalo Web session/cookie lifetime (community reports suggest ~1 week) | **unverified** | Only relevant if the rejected Selenium path is ever revisited — i.e. never |
| 16 | Whether `zca-js` group-history pagination via `lastActionId` reaches arbitrarily far back or hits a server-side floor | **unverified** | Bounds what group backfill can actually deliver |

---

## 13. Risks to carry forward

- **Account loss is severe and probably irreversible.** Phone-number-bound, carries banking,
  government and work identity. A wiped friend list is documented. Not comparable to a
  rate-limited Reddit account.
- **`zca-js` is a single-maintainer reverse-engineering project with slowing feature work and
  67 open issues.** Expect it to be broken, not degraded.
- **The webhook requirement is the only part of the system that cannot live on the Mac.** It is
  a legitimate reason to defer `zalo.oa`, not a detail to solve later.
- **The OA package restructure is three months old**, so most Vietnamese sources describing OA
  capabilities predate it. Anything citing OA pricing, quotas or feature gating needs a
  verify-before-committing marker.
- **Opt-in E2EE means coverage silently degrades over time.** `content_unavailable` is the only
  thing standing between that and an archive that misrepresents its own completeness.
- **Zalo CDN URLs expire.** An archive storing URLs looks complete on write and is hollow on
  read.
- **The Zalo ToS was force-updated in December 2025 under a 45-day account-deletion threat and
  is being publicly contested on data-protection grounds in Vietnam.** *(likely)* The rules
  governing automated access could change again on short notice, and the December precedent
  shows Zalo is willing to make acceptance mandatory.
- **Zalo is the source most likely to tempt a "just make it work" shortcut, precisely because
  it matters most to this user.** The design makes the limits structurally visible — `backfill`
  is `False` for `zalo.bot` and groups-only for `zalo.user`, and `capabilities_note()` says so
  in words — so that a later self, or a later agent, cannot quietly assume history is
  available.

---

## 14. Recommendation for v1, restated

**Ship nothing for Zalo.** Ship these three things, which are cheap now and expensive to
retrofit:

1. Three registry entries (`zalo.oa`, `zalo.bot`, `zalo.user`) with honest
   `capabilities_note()` strings, so the CLI can tell the truth about what is not available.
2. The `user_withdraw`-shaped **per-subject purge path**, built generically for every source —
   [./docs/GOVERNANCE.md](../GOVERNANCE.md) §7.
3. `containers.content_unavailable`, so the archive can distinguish *"nothing here"* from
   *"something here I cannot read."*

Then check item 13 in §12 — whether the Zalo PC export `.zip` is readable — because it is the
one cheap experiment that could change the answer.
