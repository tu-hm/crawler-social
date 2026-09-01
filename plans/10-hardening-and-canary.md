# Step 10 — Hardening and canary (M10)

## Outcome

Parser drift, index drift, backup failures, and FTS damage become visible before they silently corrupt weeks of collection.

## Depends on

- Step 04 emits field statistics.
- Step 09 provides scheduled execution and notifications.

## Work, in order

1. Build the fill-rate canary from persisted field statistics.
2. Alert when a field drops below 50% of its trailing ten-run baseline on a run with at least 20 observed items.
3. Test specifically with tri-state is_pinned so unknown values remain visible.
4. Run ANALYZE after the first substantial crawl and during routine maintenance.
5. Put the canonical timeline query in one named constant so its partial-index predicate cannot drift.
6. Add a daily SQLite backup and report failure visibly.
7. Add a doctor repair action that rebuilds the external-content FTS index.
8. Do not add a Facebook absence sweep; opaque coverage cannot prove deletion.

## Verification

- Breaking a monitored selector over fixtures raises a canary alert.
- The canonical query uses idx_items_recent and no temporary sort B-tree.
- A backup restores and passes integrity checks.
- FTS corruption and repair tests use MATCH, not row counts.

## Sources

- [PLAN M10](../PLAN.md)
- [DATA-MODEL §6, §9, and §11](../docs/DATA-MODEL.md)
- [Facebook §10](../docs/sources/facebook.md)
