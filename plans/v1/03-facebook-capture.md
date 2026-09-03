# Step 03 — Capture and preserve Facebook HTML

## Outcome

The crawler opens one public Facebook Page in a real Chrome or Chromium window and commits
raw HTML snapshots before parsing.

## Depends on

- [Step 02](./02-storage.md) is complete.
- The dedicated browser profile exists and has user-only permissions.

## Work, in order

1. Add `facebook.py`; keep browser startup, navigation, scrolling, and capture in this
   module.
2. Resolve the browser binary in this order:
   - `CRAWLER_BROWSER_BINARY` when set;
   - common Google Chrome locations/names for the current OS;
   - common Chromium locations/names for the current OS;
   - otherwise fail with installation/configuration guidance.
3. Resolve the profile path through `paths.py` or `CRAWLER_PROFILE_DIR`.
4. Acquire a single-process lock before opening the profile. `fcntl.flock` is available on
   both supported platforms; keep the locking helper separate for testability.
5. Start a visible browser. On Linux accept X11 or Wayland; do not force an X11-only
   configuration.
6. If Facebook requests login, let the user complete it manually in the visible window.
7. Navigate to the selected public Page.
8. After every scroll:
   - capture the current HTML;
   - commit it through `save_snapshot()`;
   - stop at 20 visible candidate posts or a fixed time limit.
9. Never wait until the final scroll to capture: virtualized feeds may remove earlier DOM
   nodes.
10. Save a small non-private HTML fixture and metadata sidecar for offline development.
11. Close the browser and release the lock in `finally` blocks.

## Platform checks

- macOS: fail clearly when there is no logged-in graphical session or browser application.
- Linux: fail clearly when both `DISPLAY` and `WAYLAND_DISPLAY` are empty.
- Both: print the resolved browser binary and profile path without printing cookies or
  profile contents.

## Verification

```console
uv run crawler crawl "https://www.facebook.com/<page>" --limit 20
```

Then verify through Python or the SQLite CLI that:

- the run exists;
- at least one raw snapshot exists;
- every snapshot has a SHA-256 value;
- the fixture can be loaded without starting the browser.

## Stop conditions

- Stop on checkpoint, CAPTCHA, or access restriction; do not bypass it.
- Stop after one day if reliable HTML capture is still unavailable and record the observed
  blocker before changing browser libraries.
