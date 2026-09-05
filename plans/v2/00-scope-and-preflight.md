# Step 00 — Decide scope and verify the environment

## Outcome

The stack is chosen, the dependencies install, and you can prove the existing database
is readable from a second process while the crawler holds it.

## Depends on

- [v1 Step 06](../v1/06-reliability-gate.md) is complete: `data/social.db` exists and
  `uv run crawler posts --limit 10` prints rows.

## Decisions fixed here

- **Server**: FastAPI on uvicorn, started by a new `crawler serve` subcommand.
- **UI**: server-rendered Jinja2 templates plus one hand-written CSS file and small
  vanilla-JS enhancements. No Node, no bundler, no frontend framework.
- **Read path**: SQLite opened read-only through a URI (`file:...?mode=ro`), relying on
  WAL for concurrent reads while a crawl writes.
- **Write path**: the server never writes to the database. Starting a crawl (Step 08)
  spawns the existing CLI as a subprocess.
- **Audience**: one local user on their own machine. Loopback bind by default.

## Work, in order

1. Add runtime dependencies to `pyproject.toml`:
   - `fastapi>=0.115`
   - `uvicorn>=0.30`
   - `jinja2>=3.1`
2. Add dev dependencies:
   - `httpx>=0.27` (FastAPI's `TestClient` needs it)
3. Run `uv sync --dev` and confirm the lock file updates.
4. Add server settings to `config.py` and `.env.example`, all optional:
   - `CRAWLER_SERVE_HOST` (default `127.0.0.1`)
   - `CRAWLER_SERVE_PORT` (default `8765`)
   - `CRAWLER_SERVE_TOKEN` (default unset; used in Step 09)
   Extend the frozen `Config` dataclass with `serve_host`, `serve_port`, `serve_token`.
   Parse the port as an integer and fail with a clear message when it is not one.
5. Confirm read-only access works against the real database:
   ```python
   sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
   ```
   A `SELECT` succeeds; a `CREATE TABLE` raises `sqlite3.OperationalError`.
6. Write down in `docs/DECISIONS.md` that the server is read-only and loopback-bound,
   and why: the crawler is the single writer and the snapshot HTML is untrusted.

## Required tests

- `load_config()` returns the documented defaults when no server variables are set.
- `load_config()` reads `CRAWLER_SERVE_HOST`, `CRAWLER_SERVE_PORT`, and
  `CRAWLER_SERVE_TOKEN` from the environment.
- A non-numeric `CRAWLER_SERVE_PORT` raises a clear error naming the variable.
- A read-only connection to a temporary database can `SELECT` but cannot `CREATE TABLE`.

## Verification

```console
uv sync --dev
uv run pytest
uv run python -c "import fastapi, uvicorn, jinja2; print('deps ok')"
```

Tests pass, the imports succeed, and the existing CLI still works unchanged.
