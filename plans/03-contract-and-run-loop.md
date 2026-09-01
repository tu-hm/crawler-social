# Step 03 — Connector contract and run loop (M3)

## Outcome

Core runs a complete durable tick through the frozen connector contract, and the Facebook skeleton uses it without changing results.

## Depends on

- Step 02 produced fixtures and a known real-item count.

## Work, in order

1. Implement the frozen protocol and dataclasses from [ARCHITECTURE §5](../ARCHITECTURE.md): envelopes, cursors, coverage, verdicts, budget, capabilities, governance, targets, and typed drafts.
2. Add the hardcoded lazy connector registry. Unknown names must list valid choices.
3. Implement the token bucket and unified Budget behavior.
4. Implement six verdict dispositions and their exit-code projection.
5. Implement commit_envelope as one transaction: deduplicate body, write envelope, always write fetch observation, upsert drafts and provenance, compare coverage, record gaps and field stats, then write the cursor last.
6. Implement all three core-owned SUSPECT rules from [PLAN M3](../PLAN.md). A match prevents cursor advancement; two consecutive suspect runs escalate to HUMAN.
7. Implement FixtureConnector as a replay adapter that delegates parsing to the real connector.
8. Refactor Step 02 to emit envelopes and run through the contract.

## Required tests

- A fixture tick writes run, envelope, rows, and cursor without the cursor outrunning committed bytes.
- Exact non-empty coverage parsing to zero becomes suspect and leaves the cursor unchanged.
- A process killed mid-run loses at most the active envelope and creates no duplicate on retry.
- Fixture replay produces the Step 02 item count.
- Core tests pass with the Facebook package removed.

## Done when

crawler tick --fixture exercises the production path with no network or browser and leaves auditable runs, envelopes, items, coverage, and cursors.

## Sources

- [PLAN M3](../PLAN.md)
- [ARCHITECTURE §4–§7, §10, and §13–§14](../ARCHITECTURE.md)
