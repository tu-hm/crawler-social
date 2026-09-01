# Step 15 — X REST connector (M15)

## Outcome

An optional, disabled-by-default X REST connector can collect third-party timelines only within explicit local and platform spend ceilings.

## Depends on

- Step 14 is complete independently of paid access.
- The owner explicitly chose to pay for X.
- Current console pricing and billing behavior are verified before enabling anything.

## Work, in order

1. Ship with enabled=false.
2. Read the live console price and reconcile it with documented assumptions.
3. Set both a platform console cap and a local dollar-denominated cap.
4. Check month-to-date cost_micros before every run and update that same metric transactionally with ingested reads.
5. Default to excluding retweets, replies off, and a 200-post/account backfill ceiling.
6. Define --limit in billable posts, not requests or pages.
7. Capture metrics once; do not declare mutable-metrics capability.
8. Schedule paid collection at 01:00 UTC and keep retries inside the same UTC billing day.
9. Bound every pagination loop independently of server cursors.
10. Make dry-run output show targets, projected reads, live unit price, projected spend, remaining local cap, and console-cap reminder.

## Required tests

- A mocked infinite cursor terminates at the budget.
- The governor reads exactly cost_micros, the metric ingestion writes.
- Hitting the cap returns STOP and spends nothing further.
- Replies cannot be enabled globally and require a per-post maximum.
- Disabled connectors never enter scheduled work.

## Sources

- [PLAN M15](../PLAN.md)
- [X §3–§4 and §8](../docs/sources/x.md)
- [ARCHITECTURE §9](../ARCHITECTURE.md)
