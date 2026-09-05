# Step 02 — Stand up the HTTP server

## Outcome

`uv run crawler serve` starts a local web server that reports its own health and shuts
down cleanly on Ctrl-C. Nothing is rendered yet.

## Depends on

- [Step 01](./01-read-query-layer.md) is complete.

## Work, in order

1. Add `src/crawler_social/server/__init__.py` and `src/crawler_social/server/app.py`.
   Keep the server in its own package so the crawler never imports FastAPI.
2. In `app.py`, implement `create_app(config: Config) -> FastAPI`:
   - store the resolved `Config` on `app.state.config`;
   - set `title="crawler-social"` and disable the interactive docs on non-loopback binds
     (Step 09 revisits this);
   - open no database connection at import time.
3. Implement per-request connection handling as a FastAPI dependency:
   - one `connect_ro` connection per request, closed in a `finally`;
   - SQLite connections are not shared across threads, so never cache one on `app.state`;
   - when the database file is missing, return HTTP 503 with a JSON body
     `{"error": "database_missing", "path": ..., "hint": "run `crawler crawl` first"}`
     instead of a stack trace.
4. Add `GET /healthz` returning `{"status": "ok", "db": "<path>", "db_present": bool,
   "version": "<__version__>"}`. This endpoint must answer even when the database is
   missing, so it does not use the connection dependency.
5. Add a global exception handler that logs the traceback server-side and returns a plain
   `{"error": "internal"}` body. Never leak file paths or SQL into an error response.
6. Add the `serve` subcommand to `cli.py`:
   ```
   crawler serve [--host HOST] [--port PORT] [--reload]
   ```
   - defaults come from `Config` (`127.0.0.1`, `8765`);
   - CLI flags override the environment;
   - print the exact URL to open before uvicorn starts;
   - import FastAPI and uvicorn *inside* the command body, matching how `crawl` and
     `posts` already defer their imports, so `crawler --help` stays fast;
   - exit code 0 on Ctrl-C, not a traceback.
7. Refuse to start when `--host` is not a loopback address unless `--allow-remote` is also
   passed. Print why. Step 09 adds the token requirement on top.
8. Add `make serve` to the `Makefile`, matching the existing target style.

## Required tests

Use `fastapi.testclient.TestClient` against `create_app(config)` with a temporary config —
never start a real uvicorn process in tests.

- `GET /healthz` returns 200 and `db_present: true` for a temporary database.
- `GET /healthz` returns 200 and `db_present: false` when the file is missing.
- A data endpoint returns 503 with `error: "database_missing"` when the file is missing.
- The connection dependency closes its connection after each request (assert with a
  wrapper or by checking the connection is unusable afterwards).
- An endpoint that raises returns 500 with body `{"error": "internal"}` and no path or SQL
  text in the response.
- `crawler serve --host 0.0.0.0` without `--allow-remote` exits non-zero with a message
  naming the flag.

## Verification

```console
uv run pytest tests/test_server_app.py
uv run crawler serve
curl -s http://127.0.0.1:8765/healthz
```

The server prints its URL, `curl` returns the health JSON, and Ctrl-C exits cleanly with
status 0.
