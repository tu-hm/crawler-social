# Simple v1 — ordered implementation checklist

Complete these plans in order:

1. [Step 00 — Verify the environment](./00-environment.md)
2. [Step 01 — Scaffold the application](./01-scaffold.md)
3. [Step 02 — Build the four-table database](./02-storage.md)
4. [Step 03 — Capture and preserve Facebook HTML](./03-facebook-capture.md)
5. [Step 04 — Parse and store five post fields](./04-parser.md)
6. [Step 05 — Make repeated runs safe](./05-safe-repeat-runs.md)
7. [Step 07 — Hold a Facebook session without tripping bot defence](./07-session-and-access.md)
8. [Step 06 — Prove reliability before expanding](./06-reliability-gate.md)

Rules:

- Finish the verification section of a step before starting the next one.
- Support macOS and Linux in every step; OS-specific code stays behind a small helper.
- Do not add private data, another source, a scheduler, or a generic connector framework
  during v1.
- Commit raw HTML before parsing it.
- Never type credentials from the driver; a human logs in, automation reuses the session.
- A wall stops the run. Never keep scrolling one, and never advance state from a
  truncated feed.
- Update crawl state only after parsed posts commit successfully.
