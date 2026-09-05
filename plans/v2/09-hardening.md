# Step 09 — Harden the server

## Outcome

The server is safe to leave running: it binds to loopback, it will not expose the database
to the network by accident, and it fails clearly instead of leaking internals.

## Depends on

- [Step 08](./08-trigger-crawl.md) is complete.

## Work, in order

1. **Binding.** Default to `127.0.0.1`. A non-loopback `--host` requires both
   `--allow-remote` and a `CRAWLER_SERVE_TOKEN` of at least 32 characters. Refuse to start
   otherwise, and say which of the two is missing.
2. **Token auth**, active only when a token is configured:
   - accept it as an `Authorization: Bearer` header or a `token` query parameter on the
     first request, then set a `HttpOnly`, `SameSite=Strict` session cookie so links work;
   - compare with `hmac.compare_digest`, never `==`;
   - exempt `/healthz` so a supervisor can probe it;
   - return 401 with a JSON or HTML body depending on the path prefix.
3. **Response headers** on every HTML response:
   - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self';
     img-src 'self' data:; frame-src 'self'; connect-src 'self'; base-uri 'none';
     form-action 'self'` — note this requires the JS and CSS to be real files, which
     Step 04 already established, and no inline `<script>`;
   - `X-Content-Type-Options: nosniff`;
   - `Referrer-Policy: no-referrer`;
   - `X-Frame-Options: DENY` on app pages. The snapshot raw route keeps its own headers
     from Step 06 and must be exempted from the app CSP, not fight with it.
4. **Errors.** Confirm the Step 02 handler is still in force after all the routes exist: a
   500 body carries no path, no SQL, and no traceback. Log the full traceback to stderr
   with the request path and a short request id, and put that id in the error page so a
   user can quote it.
5. **Request logging** to stderr: method, path, status, duration in milliseconds. Never
   log the `token` query parameter or the cookie value — redact them.
6. **Limits.**
   - reject query strings over 4 KB and `q` values over 500 characters with 400;
   - cap `limit` at 200 as `queries.py` already enforces;
   - set a uvicorn `--timeout-keep-alive` of 5 seconds.
7. **Read-only enforcement**, as a test rather than a promise: run the full HTML and API
   route table against a database file whose permissions are `0o444` and assert every
   route returns a non-5xx status. Any route that needs a write will fail loudly here.
8. **Dependency review.** `uv run pip list` should show only fastapi, uvicorn, jinja2,
   pydantic, and their direct requirements added by v2. Anything else that appeared is
   worth explaining before it stays.
9. **Document it** in `README.md`: how to start the server, that it is loopback and
   read-only by default, what `--allow-remote` costs, and that snapshot HTML is rendered
   sandboxed and never trusted.

## Required tests

- Non-loopback host without `--allow-remote` refuses to start, naming the flag.
- Non-loopback host with `--allow-remote` but no token refuses, naming the variable.
- A short token (under 32 characters) is refused.
- With a token set, an unauthenticated request returns 401 and an authenticated one
  returns 200.
- Token comparison rejects a value that shares a prefix with the real one.
- `/healthz` answers without a token.
- Every HTML response carries the CSP, `nosniff`, and `Referrer-Policy` headers.
- `/api/snapshots/{id}/raw` keeps its own restrictive CSP and is not overwritten by the
  app CSP.
- No rendered page contains an inline `<script>` or `<style>` block that the CSP would
  block — assert by grepping the rendered HTML.
- A 500 response contains a request id and no filesystem path or SQL fragment.
- The access log line for a request carrying `?token=secret` does not contain `secret`.
- A `q` of 600 characters returns 400.
- Every route returns a non-5xx status against a `0o444` database file.

## Verification

```console
uv run pytest tests/test_server_security.py
uv run crawler serve --host 0.0.0.0
CRAWLER_SERVE_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))") uv run crawler serve --host 0.0.0.0 --allow-remote
```

The first non-loopback attempt is refused with a clear reason; the second starts and
rejects an untokened request with 401.
