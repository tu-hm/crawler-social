# Step 12 — Telegram connector (M12)

## Outcome

One pull-only connector ingests public channels and explicitly enrolled conversations through the same pipeline while routing each to the correct store.

## Depends on

- Step 11 privacy and private-store invariants are green.
- T0 credentials exist; otherwise follow the fallback recorded in Step 00.
- Telegram legal and ML restrictions have been reviewed for the intended downstream use.

## Work, in order

1. Smoke-test and pin the documented Telethon release on Python 3.13; vendor the wheel and confine Telethon imports to the Telegram connector directory.
2. Keep the session outside synced folders, mode-restricted, and protected by a single-flight lock.
3. Use polling and history reconciliation, not an update listener.
4. Iterate history oldest-first after the durable ID cursor with the specified wait and fixed request chunk.
5. Add the bounded ID refresh window for edits, metrics, and reactions.
6. Make coverage exact and add the core absence/tombstone sweep, gated on good access and successful run status.
7. Do not claim delete-event capability; the sweep stays mandatory.
8. Serialize scrubbed TL-derived slices to msgpack, preserve entities raw, and keep parsing pure.
9. Add item-version history and zstd dictionary compression.
10. Enforce read-only behavior by importing no send, forward, or join method. Chat discovery is interactive and print-only.
11. Map flood wait to literal WAIT, long waits to checkpoint/75, peer flood to STOP, and duplicated/revoked sessions to HUMAN.
12. Enrol conversations individually and forward-only unless supervised backfill is explicitly selected.

## Verification

- One tick writes a channel item and envelope to social.db and a DM item and envelope to private.db.
- An edited message preserves prior text in item versions.
- --limit leaves the durable cursor unchanged.
- AST tests prove send/forward/join APIs are absent.
- The absence sweep no-ops on failed access or a failed run.

## Sources

- [PLAN M12](../PLAN.md)
- [Telegram source plan](../docs/sources/telegram.md)
- [GOVERNANCE Telegram defaults](../docs/GOVERNANCE.md)
