# Step 00 — Verify the environment

## Outcome

One public Facebook Page and one supported desktop environment are ready for development.
No application code is written in this step.

## Supported environments

| Requirement | macOS | Linux |
|---|---|---|
| CPU | Apple Silicon or Intel | x86_64 or arm64 where browser packages exist |
| Browser | Google Chrome or Chromium | Google Chrome or Chromium |
| GUI | Logged-in desktop session | Logged-in X11 or Wayland session |
| Python | 3.12 or newer | 3.12 or newer |
| Package runner | `uv` | `uv` |
| Database | Python's bundled `sqlite3` | Python's bundled `sqlite3` |

Headless servers are outside v1. Facebook capture needs a real, logged-in desktop session.
Offline parsing and database tests must still work without a browser or GUI.

## Work, in order

1. Choose one public Facebook Page URL. Do not start with a Group or private content.
2. Confirm `uv`, Python, and Git are available:

   ```console
   uv --version
   uv run python -c "import sqlite3, sys; print(sys.version); print(sqlite3.sqlite_version)"
   git --version
   ```

3. Confirm Chrome or Chromium is installed:
   - macOS: check `/Applications/Google Chrome.app` or
     `/Applications/Chromium.app`.
   - Linux: run `command -v google-chrome`, `command -v chromium`, or
     `command -v chromium-browser`.
4. On Linux, confirm a GUI session is visible:

   ```console
   test -n "$DISPLAY" || test -n "$WAYLAND_DISPLAY"
   ```

5. Choose a browser-profile directory outside the repository:
   - macOS:
     `~/Library/Application Support/crawler-social/chrome/default`
   - Linux:
     `${XDG_STATE_HOME:-$HOME/.local/state}/crawler-social/chrome/default`
6. Create the directory with user-only permissions (`0700`). Do not place it in a cloud
   synchronized directory.
7. Record the selected Page URL, browser name, and operating system in a local,
   gitignored configuration file later in Step 01.

## Verification

- All three version commands succeed.
- Python reports SQLite support.
- Chrome or Chromium can open normally.
- A GUI session exists.
- The chosen profile directory is outside this repository and readable only by its user.

## Stop conditions

- Stop if the machine has no desktop GUI.
- Stop if the Page is not public.
- Do not automate Facebook passwords, two-factor authentication, checkpoints, or CAPTCHA.
