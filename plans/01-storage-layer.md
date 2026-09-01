# Step 01 — Storage layer (M1)

## Outcome

data/social.db is created from the frozen schema, migrations are repeatable, and storage invariants are enforced before real data arrives.

## Depends on

- Step 00 runtime checks are green.
- The SQLCipher/FTS5 branch is recorded, even though private.db opens only in Step 11.

## Work, in order

1. Copy the DDL from [DATA-MODEL §3](../docs/DATA-MODEL.md) verbatim into the core schema file. Never maintain a paraphrased schema copy.
2. Set creation-time pragmas, including WAL and secure_delete.
3. Implement connect(store) with foreign keys, busy timeout, normal synchronization, and the SQLite version assertion.
4. Implement transactional numbered migrations with independent core and per-source counters.
5. Create only social.db; keep the private-store branch explicitly unavailable until Step 11.
6. Implement shared upserts with COALESCE for optional values and guards on every redacted content column.
7. Implement the fcntl.flock cross-process helper.
8. Add storage tests before any connector depends on this layer.

## Required tests

- Running migrations twice is a no-op.
- Repeating an item upsert preserves first_seen, advances last_seen, and creates no duplicate.
- Re-fetching identical bytes adds envelope_fetches without duplicating envelopes.
- An unchanged metric creates no metric observation.
- Both third-party acknowledgement triggers reject invalid insert and update paths.
- Insert/update/delete followed by PRAGMA integrity_check returns ok.
- Redacted content stays null when the source item is ingested again.

## Done when

- Tests pass with no browser or network.
- social.db contains the expected 77 sqlite_master objects.
- The identical-body test proves the envelope/fetch split.

## Sources

- [PLAN M1](../PLAN.md)
- [DATA-MODEL §3, §12, and §13](../docs/DATA-MODEL.md)
