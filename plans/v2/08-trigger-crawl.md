# Step 08 — Trigger a crawl from the UI

## Outcome

A "Crawl now" button starts a real crawl, streams its output to the page, and refuses to
start a second one while the first is running.

## Depends on

- [Step 07](./07-runs-dashboard.md) is complete and everything before it is green. This is
  the first step where the server causes a write, so it comes last on purpose.

## Two constraints that decide the design

1. **`run_crawl` must run in the main thread of its own process.** `pipeline.py` installs
   `SIGINT`/`SIGTERM` handlers via `signal.signal`, which raises `ValueError` in any
   non-main thread. A thread pool or a FastAPI background task will not work. The server
   spawns `crawler crawl` as a **subprocess**.
2. **The crawl needs a visible browser.** `facebook.check_gui_session()` fails without
   `DISPLAY` or `WAYLAND_DISPLAY`, and login is completed by hand in the window. So this
   button only works when the server runs on the user's own desktop session, and the UI
   must say so rather than failing mysteriously.

## Work, in order

1. Add `src/crawler_social/server/jobs.py` holding a single module-level `CrawlJob`:
   process handle, start time, page URL, captured output lines, exit code, and a
   `threading.Lock` guarding all of it.
2. Implement `start(page_url, limit)`:
   - refuse when a job is already running, returning a clear reason;
   - refuse when `facebook.check_gui_session()` raises, passing its message through;
   - validate `page_url` against an allowlist of hosts (`facebook.com`, `www.facebook.com`,
     `m.facebook.com`) and require an `https` scheme. This is a URL that becomes a
     subprocess argument and a browser navigation — do not accept arbitrary input;
   - spawn `[sys.executable, "-m", "crawler_social.cli", "crawl", page_url, "--limit", str(limit)]`
     with a **list** argv and `shell=False`, never a shell string;
   - capture stdout and stderr into the ring buffer with a reader thread, capped at 500
     lines so a runaway crawl cannot exhaust memory.
3. Implement `status()` returning running/idle, the page URL, elapsed seconds, the last N
   output lines, and the exit code once finished. Implement `stop()` sending `SIGTERM`,
   which `pipeline.py` already handles as a graceful "interrupted" stop; a second call
   escalates to `SIGKILL` after a 10-second grace period.
4. Add the routes:
   - `POST /crawl` — starts a job, redirects to `/crawl` (303) on success, re-renders the
     form with the reason on refusal;
   - `GET /crawl` — the form plus the live status panel;
   - `POST /crawl/stop` — requests a graceful stop;
   - `GET /api/crawl/status` — the JSON the status panel polls every 2 seconds.
   State-changing routes are `POST` only. A `GET` must never start a crawl.
5. Add CSRF protection: a random token minted per server start, embedded as a hidden form
   field, and required on every `POST`. Combine it with an `Origin`/`Referer` check
   against the bound host. Without this, any page in the user's browser could start a
   crawl at `127.0.0.1:8765`.
6. Rely on the existing `FileLock` as the real mutual exclusion. The in-process job flag
   prevents the common case; the profile lock in `facebook.acquire_profile_lock` prevents
   the case where the user also started a crawl from the terminal. Surface `LockBusyError`
   from the subprocess output as a readable message, not a stack trace.
7. Pre-fill the page URL from `config.page_url`, and offer the pages already in the
   database as a datalist.
8. When the job finishes, show its exit code, link to the new run in `/runs`, and note that
   the page will now show the new posts.
9. Reload the read-only connections naturally — each request already opens its own, and
   WAL means a reader sees committed data immediately. No cache to invalidate.

## Required tests

- `start()` with a job already running returns a refusal and spawns no second process.
- `start()` with `http://evil.example/x` is refused by the host allowlist.
- `start()` with `https://facebook.com/somepage` is accepted.
- The subprocess is spawned with a list argv and `shell=False` — assert with a patched
  `subprocess.Popen`.
- A page URL containing `; rm -rf ~` is passed through as one argument, not interpreted.
- `POST /crawl` without a CSRF token returns 403 and starts nothing.
- `POST /crawl` with a foreign `Origin` header returns 403.
- `GET /crawl` never starts a job — assert the patched `Popen` was not called.
- `/api/crawl/status` reports `running: true` with elapsed time for a fake long process,
  then `running: false` with the exit code after it finishes.
- `stop()` sends `SIGTERM` first, not `SIGKILL`.
- The output ring buffer holds at most 500 lines when fed 5,000.
- A `check_gui_session` failure produces a readable refusal naming `DISPLAY`.

All tests patch `subprocess.Popen`. No test launches a real browser.

## Verification

```console
uv run pytest tests/test_crawl_trigger.py
uv run crawler serve
```

On a desktop session, click "Crawl now". A Chrome window opens, the status panel streams
the crawl's output, the new run appears in `/runs`, and clicking the button again while it
runs is refused with a clear message.
