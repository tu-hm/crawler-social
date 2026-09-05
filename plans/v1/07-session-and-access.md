# Step 07 — Hold a Facebook session without tripping bot defence

## Outcome

The crawler reaches a logged-in Facebook session reliably, recognises every wall
Facebook can serve, and stops instead of hammering one. Login stops happening inside
the automated browser.

## Depends on

- [Step 03](./03-facebook-capture.md) is complete.
- Unblocks the reliability gate in [Step 06](./06-reliability-gate.md), which cannot
  pass while every run returns zero posts.

## What actually went wrong

Two runs on 2026-09-05 stored five snapshots and zero posts, then died with
`TargetClosedError`. The captured HTML is on record in `tests/fixtures/`:

| Capture | Bytes | Visible text | Article nodes with text |
|---|---|---|---|
| `facebook_login_wall_page.html` (`/NASA`) | 1.39 MB | 327 chars | 0 |
| `facebook_login_wall_home.html` (`/groups/VNOIForum`) | 457 KB | 477 chars | 0 |

Both are login walls. Three separate defects stacked up:

1. **The session was never established.** The dedicated profile held no Facebook
   cookies, so every request was anonymous.
2. **The login check could not fire.** `wait_for_login()` matched the substring
   `"login"` against `page.url` only. Facebook serves its gate for a Page **at the
   Page's own URL, with no redirect** — the captured title is still
   `NASA … | Facebook`. A URL check is structurally incapable of seeing this.
3. **Nothing verified that a snapshot contained content.** The crawl scrolled a wall
   for the full time budget and would have reported `status: completed`. The runs only
   failed because the window was closed by hand.

The bot-defence problem sits underneath all three: signing in *inside a
Playwright-launched browser* is the single most likely way to get challenged.

## The rule this step establishes

> **Never authenticate from an automated browser.** A human types credentials into a
> window that automation did not start. Automation only ever *reuses* the resulting
> session.

Chrome launched by Playwright carries `--enable-automation`, sets
`navigator.webdriver = true`, and starts from a profile with no history, no
extensions, and no prior device trust. A login POST arriving with that combination is
the exact shape a login-integrity system exists to catch. A *page view* carrying an
already-trusted session cookie is a far weaker signal — which is why separating the two
is the whole fix.

## Work, in order

### 1. Split login out of crawl — done

`crawler login` opens a visible window and polls until the wall is gone. It never types
credentials. `crawler session` reports whether the profile still holds a live session,
so a dead session is a one-second check rather than a failed crawl.

```console
uv run crawler login     # sign in by hand, once
uv run crawler session   # "session looks live (ok)"
```

### 2. Classify what Facebook actually served — done

`wall.py` reads the bytes, not the URL, and returns one of `ok`, `login_wall`,
`checkpoint`, `rate_limited`, `unavailable`, `empty`.

Two decisions in it are load-bearing:

- **Script and style content is stripped before matching.** Every logged-in Facebook
  page ships the strings `LoginForm` and `captcha` inside its JS bundles — the old
  capture's own snapshots contained 24 occurrences of `captcha` on a page with no
  CAPTCHA. Matching raw HTML would false-positive on every successful crawl.
- **A login verdict needs a rendered password input, not just a phrase.** A feed you
  are entitled to see never renders one.

### 3. Prefer CDP attach when `launch` gets challenged — done

```bash
# in .env
CRAWLER_ATTACH_MODE=cdp
CRAWLER_CDP_URL=http://127.0.0.1:9222
```

```console
make chrome-cdp    # your own Chrome, with --remote-debugging-port
```

Then log in in that window and leave it open. Playwright attaches instead of launching,
so the browser was never flagged as automated and keeps a real profile, real history and
a real user-agent.

Two details that will bite otherwise:

- Chrome refuses `--remote-debugging-port` against its *default* profile directory. An
  explicit `--user-data-dir` is mandatory, which `make chrome-cdp` supplies.
- Teardown differs by mode on purpose: `launch` owns the browser and closes it, `cdp` is
  a guest and closes only its own tab. Closing the user's Chrome out from under them
  would also drop the session being protected.

### 4. Reduce the automation fingerprint in `launch` mode — done

Supporting measures, not a substitute for steps 1–3:

- `ignore_default_args=["--enable-automation"]` and
  `--disable-blink-features=AutomationControlled`;
- an init script clearing `navigator.webdriver`;
- `locale` and `timezone_id` from config, so the browser agrees with the machine and IP
  it runs on;
- `headless=False`, kept — headless Chrome is trivially detectable and buys nothing here.

### 5. Stop scrolling like a machine — done

The old loop scrolled exactly 2400px every 1500ms. `human_scroll()` now uses 2–4 wheel
steps of 500–1100px with 120–380ms gaps, a 20% chance of backtracking, and a jittered
pause. Uneven and occasionally backwards is what reading looks like.

### 6. Stop on a wall instead of scrolling it — done

A blocking verdict commits the offending snapshot as evidence, then raises
`BlockedError`. The pipeline:

- keeps posts parsed from earlier, legitimate snapshots;
- **holds the watermark** — a truncated feed's newest post is not a trustworthy
  high-water mark, and advancing past it would silently skip everything the wall hid;
- records `runs.status = 'failed'` with `error` prefixed `blocked:<kind>`;
- exits `3` with the remedy for that specific verdict.

`runs.status` keeps its four-value CHECK constraint; the blocked reason lives in `error`
so this step needs no schema migration.

### 7. Pace the crawler — to do

Not yet implemented; the following are the intended budgets.

| Control | Value | Why |
|---|---|---|
| Minimum gap between runs on one Page | 30 min | A Page posts a handful of times a day; polling faster gains nothing |
| Pages per session | 1 | Already true in v1 |
| Runs per account per day | ≤ 20 | Keeps the account inside ordinary human volume |
| Session duration | ≤ 180s | Already enforced by `CaptureOptions.max_seconds` |

### 8. Back off on a block — to do

Escalate on consecutive blocked runs rather than retrying at the same rate. A checkpoint
means **stop entirely** and clear it by hand; retrying into a checkpoint is what turns a
challenge into a disabled account.

| Consecutive blocks | Action |
|---|---|
| 1 | Wait 1 hour |
| 2 | Wait 6 hours, re-run `crawler session` first |
| 3 | Stop automated runs; require a manual `crawler login` |
| any `checkpoint` | Stop immediately; do not retry until cleared by hand |

## What not to do

- **Do not automate credential entry**, and do not store the password in `.env`. Typing
  credentials from a driver is the highest-risk action available and it is what got
  challenged in the first place.
- **Do not solve or bypass CAPTCHAs and checkpoints.** Step 03's stop condition stands.
- **Do not rotate proxies or IPs.** For an anonymous scraper rotation hides volume; for a
  *logged-in account* it does the opposite — a session that appears in three countries in
  an hour is a stronger signal than the volume ever was. One account, one machine, one
  ordinary residential IP.
- **Do not go headless**, and do not run this on a server with no display. `check_gui_session()`
  already refuses.
- **Do not raise `--limit` or shorten the interval to compensate for a blocked run.** The
  block is the signal to slow down.

## Escalation: when scraping is the wrong tool

For a Page **you own or administer**, the Graph API returns the same posts with a Page
access token, no browser, and no account risk. It is strictly better and should be
preferred wherever it applies.

It does not generalise: reading Pages you do not own needs Page Public Content Access,
which requires App Review and business verification and is effectively closed to
individuals. That gap is the only reason browser capture exists here — as
[../../docs/sources/facebook.md](../../docs/sources/facebook.md) records, this connector
is `tos_class: prohibited` and **the currency of failure is the account**. Use an account
whose loss you can absorb.

## Verification

1. `uv run pytest` is green, including the two real wall captures in `tests/fixtures/`.
2. `uv run crawler session` on a cold profile exits `3` and says "not logged in".
3. `uv run crawler login`, sign in by hand, then `uv run crawler session` reports a live
   session.
4. `uv run crawler crawl "$CRAWLER_PAGE_URL" --limit 5` stores at least one post, and
   `uv run crawler posts` shows real text.
5. Log out in the browser, crawl again: the run exits `3` with `blocked: login_wall`, the
   wall snapshot is stored, and `state` is unchanged from step 4.

## Stop conditions

- Stop on `checkpoint` or `rate_limited`; clear it by hand and wait.
- If a hand-completed login in `cdp` mode is still challenged within a day, stop and
  record the observed challenge before changing browser libraries. The next lever is the
  account and the network, not the driver.
