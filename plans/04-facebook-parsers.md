# Step 04 — Facebook parsers (M4)

## Outcome

Stored Facebook bytes become typed drafts through pure, versioned, fixture-tested parsers.

## Depends on

- Step 03 contract and fixture runner.
- Step 02 fixture set and dated extraction findings.

## Work, in order

1. Implement parse(envelope) returning ParseResult with no database, network, secret, wall clock, or mutable connector state.
2. Implement the observed per-field fallback chains from [Facebook §8](../docs/sources/facebook.md). Update the doc where live evidence differs.
3. Parse absolute timestamps where available; use captured_at only to interpret relative text and record honest precision.
4. Keep is_pinned, is_sponsored, and more_remaining tri-state: unknown is not false.
5. Emit seen/filled statistics for every monitored field.
6. Mark counts derived from rounded UI strings as approximate.
7. Add table-driven tests for every fixture plus deliberately mangled variants.
8. Commit one canonical-JSON SHA-256 golden snapshot per fixture and parser version.

## Verification

- Normal fixtures populate all available core fields.
- Mangled fixtures return nulls and diagnostics instead of raising.
- Every fixture parses with an empty secrets mapping.
- Golden snapshots change only with a deliberate parser-version change.

## Sources

- [PLAN M4](../PLAN.md)
- [Facebook §8 and §10–§11](../docs/sources/facebook.md)
- [ARCHITECTURE §7 and §14](../ARCHITECTURE.md)
