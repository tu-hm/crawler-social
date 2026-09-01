# Step 06 — Reparse and repair (M6)

## Outcome

Stored envelopes can be reprocessed without platform access, proving the raw-first guarantee before production depends on it.

## Depends on

- Step 05 produces complete envelopes.
- Step 04 parser versions and golden snapshots exist.

## Work, in order

1. Implement reparse over envelopes behind the current parser version, ordered by monotonic envelope sequence.
2. Ensure reparse never initializes drivers, credentials, or connector fetch modules.
3. Resolve parent_ref to parent_id in a repair pass so ingest order is irrelevant.
4. Iteratively backfill thread_path until a pass changes zero rows; leave irreparable roots null and visible.
5. Apply ordinary redaction and block-reingest guards during reparse.
6. Add dry-run reporting for affected envelope and item counts.

## Required tests

- Crawling identical fixtures twice preserves item count and adds fetch observations.
- A no-op parser-version bump reparses without network and preserves golden output.
- Redact, reparse, then verify content remains null and FTS has no match.
- A minimal dependency test confirms Selenium is not imported during reparse.

## Done when

A full Facebook reparse completes offline, repairs resolvable threads, preserves redactions, and makes no source request.

## Sources

- [PLAN M6](../PLAN.md)
- [DATA-MODEL §5, §7, and §12](../docs/DATA-MODEL.md)
