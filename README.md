# crawler-social

> **Start with [SIMPLE_PLAN.md](./SIMPLE_PLAN.md).** It defines the intentionally small
> first version: one public Facebook Page, one SQLite database, raw HTML, five parsed
> fields, and two CLI commands. The larger documents below are reference material for
> later expansion, not prerequisites for starting implementation. The active v1 supports
> both macOS and desktop Linux; follow the ordered files in
> [plans/v1/](./plans/v1/README.md).

**Current status: planning only; no application code has been written.** The detailed
documents below capture the larger, originally macOS-oriented multi-source design frozen on
**2026-09-01**. Keep them as reference, but implement the smaller cross-platform v1 first
and only bring decisions forward when the working product actually needs them.

---

## Active v1

Build one public Facebook Page crawler by following
[plans/v1/00-environment.md](./plans/v1/00-environment.md), then continue in numeric order.
Do not apply for other platform credentials, design private storage, or install a scheduler
for this version.

## Legacy multi-source design

A personal ingestion tool that pulls one person's social and messaging activity into SQLite
and **keeps the original transport bytes**, so that when Facebook rotates its DOM in month
seven you fix the parser and re-run it over six months of stored HTML — no re-crawl, no lost
history. One core, several connectors, one store, three run modes (one-time, `--limit N`,
a twice-daily routine under launchd).

Five sources: **Facebook** (headful Chrome on your own session), **Telegram** (MTProto user
API), **Reddit** (REST over OAuth, if credentials can be obtained at all), **X** (your own
archive ZIP, plus a paid REST connector that ships switched off), and **Zalo** (deferred,
with the evidence for why written down).

**Two ideas do most of the work, and everything else follows from them.**

**1. A connector is a capability contract, not a browser.** Browser automation is the right
transport for exactly one of the five sources. Reddit has an official API; Telegram has a
first-class user-account protocol that makes scraping strictly inferior; X is a pricing
question wearing a technical costume; Zalo has no legitimate personal-account path at all.
So one object crosses the connector boundary — an `Envelope` of verbatim bytes plus a cursor
proposal plus a coverage claim — and how a connector gets its bytes is its own private
business. Any design that assumes "crawler == browser" is wrong.

**2. Conversations contain other people.** Pages and subreddits are broadcast. DMs and
private group chats hold the personal data of people who never agreed to be in anyone's
database. That is treated as an engineering requirement with structural consequences —
privacy is a property of the *container*, the encryption boundary is a **file path** rather
than a `WHERE` clause, conversation *envelopes* go to the encrypted file too, enrolment is
forward-only, and there is deliberately no bulk-enrol command anywhere in the CLI.

---

## Legacy reference documents

| # | Document | What it settles | Lines |
|---|---|---|---|
| 1 | **README.md** (this file) | What the repo is, the reading order, status, next actions | 130 |
| 2 | [PLAN.md](./PLAN.md) | Scope, per-platform reality, the frozen decisions in summary, **the milestone plan**, first-run, failure modes, **the deferred list with triggers**, **the verify-before-building checklist**, **the open questions for you** | 1,394 |
| 3 | [ARCHITECTURE.md](./ARCHITECTURE.md) | The connector contract (`Envelope`, `Cursor`, `Coverage`, `Budget`, `Cap`), the core/connector seam, the import DAG, **the capability model**, **the CLI surface**, scheduling, rate limiting, the error taxonomy, secrets, testing | 1,595 |
| 4 | [docs/DATA-MODEL.md](./docs/DATA-MODEL.md) | **The frozen DDL — the only copy in the repo** — both database files, threading, metrics, raw storage, FTS5 tuned for Vietnamese, the canonical queries and their real query plans | 2,568 |
| 5 | [docs/GOVERNANCE.md](./docs/GOVERNANCE.md) | Privacy classes, the two-file split, encryption at rest, retention, redaction, upstream deletes, export gating, Vietnamese law | 921 |
| 6 | [docs/DECISIONS.md](./docs/DECISIONS.md) | 63 ADRs: the full argument behind every frozen decision, including what was rejected and what would have to change to reopen it | 1,753 |
| 7 | [docs/sources/](./docs/sources/) | One document per connector: [facebook](./docs/sources/facebook.md) · [telegram](./docs/sources/telegram.md) · [reddit](./docs/sources/reddit.md) · [x](./docs/sources/x.md) · [zalo](./docs/sources/zalo.md) | 3,824 |

Do not read these documents before building v1. When a verified v1 is ready to expand, read
PLAN, ARCHITECTURE, and DATA-MODEL in that order. Read GOVERNANCE before adding private
data. DECISIONS and the source documents remain references for the feature being added.

**Three boundaries, so you never read the same thing twice.**

- **The DDL appears in exactly one place**, DATA-MODEL §3. Every other document names tables
  and columns and never re-prints their definitions. A second copy of a schema is a copy that
  rots — and it did, in the draft this set replaced.
- **The CLI appears in exactly one place**, ARCHITECTURE §11. Everything else links to it.
- **PLAN §5 is the frozen decisions in summary**, one line of rationale each. DECISIONS
  carries the full argument. When the two disagree, DECISIONS is right and PLAN has drifted.

---

## Legacy design status

**Planning complete. No code written. Nothing has been run against any platform.**

- Every rate limit, price, library version, endpoint and library-maintenance status was
  researched against primary sources where one could be reached. **Nothing was fetched from
  facebook.com, x.com, reddit.com, telegram.org or zalo.me** — that was a hard rule for the
  research, and it is why some claims are labelled second-hand rather than verified.
- Confidence labels are uniform across all seven documents: `[verified]` = read from a
  primary source · `[likely]` = multiple consistent secondary sources, no primary ·
  `[unverified]` = must be confirmed before it is relied on. **Every unverified claim in the
  set is collected in one place** — [PLAN §12](./PLAN.md#12-verify-before-building) — with
  how to check it, roughly how long that takes, and what it blocks.
- **The frozen DDL was executed**, not just written. It runs clean on SQLite 3.51.0 and
  produces 30 tables, 41 indexes, 5 triggers and 1 view — 77 rows in `sqlite_master`. Both
  `ack_third_party` triggers abort, the `media.kind` and `usage_counters.metric` constraints
  reject what they are supposed to reject, the `local_day` generated column works, and FTS5
  with `remove_diacritics 2` matches `chao` against `chào`. Query plans in DATA-MODEL §11 are
  real `EXPLAIN QUERY PLAN` output.
- Three defects were found that way and are written up in DATA-MODEL §13, including one
  `CHECK` constraint SQLite rejects outright.

---

## Legacy next actions — deferred until after v1

**Four things on day one, before any code.** Each has a multi-day latency you do not control,
each is free, and each changes the plan if it comes back wrong. They are M0 in
[PLAN §6](./PLAN.md#6-milestone-plan).

1. **R0 — apply for a Reddit OAuth app.** Self-service registration ended in November 2025;
   every new client goes through manual review with a stated ~7-day target. This is the
   highest-variance assumption in the whole plan. Two dated checkboxes ride along with it:
   **2026-09-30** to register an existing app, **2026-12-31** for the Migration Program
   decision.
2. **T0 — get a Telegram `api_id`.** There is no review queue, but the self-service form is
   reported to fail opaquely for some accounts, and the entire "Telegram is connector #2"
   decision rests on that credential existing. If it fails, the slot changes hands — PLAN §6
   pre-makes that call.
3. **X0 — request your X data archive.** ~24h turnaround, and **the download link expires 7
   days after generation.** Then `unzip -l` it and paste the manifest into
   [docs/sources/x.md](./docs/sources/x.md) §2.3 — one command closes that connector's
   largest unknown.
4. **P0 — start the Facebook profile clock.** Log in once, then use that profile by hand like
   an ordinary browser. Session age is an asset and the thresholds are enforced, not advisory:
   **≥ 3 days** before the first Page crawl, **≥ 14 days** before the first group.

**Then answer the six questions in [PLAN §13](./PLAN.md#13-open-questions-for-you).** Four
earlier ones are already frozen as defaults with their costs stated. Of the six that remain,
two are genuinely urgent:

- **What retention do you want on other people's messages?** There is deliberately no
  default. Enrolling your first conversation target will make you type a number, and
  `forever` is a legal answer. An earlier draft defaulted to 180 days with an unattended hard
  delete, which would have quietly destroyed the archive the tool exists to build.
- **Does anything downstream of this corpus involve an LLM?** Telegram's API terms prohibit
  using or aggregating Telegram data to train or fine-tune models. That is an architectural
  boundary rather than a footnote, and it must be decided before the Telegram connector is
  built rather than discovered after the archive exists.

**Read [PLAN §12](./PLAN.md#12-verify-before-building) before you commit to any number.**
Nothing in this set is allowed to become a stated fact somewhere else without being checked
first, and a plan built on a hallucinated rate limit is worse than one that says *confirm
this*.

---

*No implementation files exist in this repository, deliberately. Code inside fenced blocks —
interface signatures, dataclass shapes, SQL DDL, config examples, CLI transcripts — is
illustrative and is there to make the design concrete, not to be executed.*
