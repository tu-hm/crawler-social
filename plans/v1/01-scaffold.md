# Step 01 — Scaffold the application

## Outcome

The project has a portable Python package, two CLI command stubs, configuration, and a
working test command on macOS and Linux.

## Depends on

- [Step 00](./00-environment.md) is complete.

## Work, in order

1. Initialize one `uv` project targeting Python 3.12 or newer.
2. Create this layout:

   ```text
   src/crawler_social/
     __init__.py
     cli.py
     config.py
     paths.py
   tests/
   ```

3. Add only the initial dependencies:
   - a CLI library;
   - browser automation;
   - HTML parsing;
   - `platformdirs` for OS-appropriate application paths;
   - pytest as a development dependency.
4. Expose the `crawler` command.
5. Add two honest command stubs:
   - `crawler crawl PAGE_URL --limit N`
   - `crawler posts --limit N [--contains TEXT]`
6. Implement `paths.py` with `platformdirs`; never scatter OS path checks through the
   application.
7. Read optional local settings from environment variables or a gitignored local config.
   Support at least:
   - `CRAWLER_BROWSER_BINARY`
   - `CRAWLER_PROFILE_DIR`
   - `CRAWLER_DB_PATH`
8. Use these default profile locations:
   - macOS: `~/Library/Application Support/crawler-social/chrome/default`
   - Linux: `${XDG_STATE_HOME:-$HOME/.local/state}/crawler-social/chrome/default`
9. Keep the database at `data/social.db` by default so development commands are easy to
   inspect on either OS.
10. Ignore `.env*`, `data/`, browser profiles, database files, and test caches.

## Verification

Run on the current operating system:

```console
uv run crawler --help
uv run crawler crawl --help
uv run crawler posts --help
uv run pytest
```

All commands must succeed. Unit tests for `paths.py` must cover mocked macOS and Linux
platform values.

## Done when

The same source tree starts without macOS-only imports or commands. OS-specific behavior is
limited to `paths.py` and configuration.
