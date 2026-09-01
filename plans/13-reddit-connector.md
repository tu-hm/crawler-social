# Step 13 — Reddit connector (M13)

## Outcome

Reddit ingestion ships through exactly one backend selected from the R0 result, with listing ceilings represented as explicit gaps.

## Depends on

- The R0 decision is recorded.
- Steps 03 and 11 provide coverage, gaps, retention, and delete-policy enforcement.

## Branch once

### If R0 is approved

1. Use prawcore only for OAuth and httpx for raw bytes; keep PRAW dev-only.
2. Use code-grant refresh tokens and raw_json=1.
3. Track a durable created_utc watermark plus an ephemeral fullname hint with six hours of overlap.
4. Turn the approximately 1,000-item listing ceiling into coverage that core records as a listing_cap gap.
5. Bound comment expansion to 32 morechildren calls and persist more_remaining.
6. Route deep or old gap fill to Arctic Shift, marking supplied metrics as archive-sourced.
7. Lock upstream-delete behavior to follow and require explicit retention for every target.
8. Re-observe metrics on the frozen decaying schedule and batch lookups.

### If R0 is declined or exceeds its stated window

1. Decide whether the owner accepts the degraded path; if not, remove this execution phase.
2. If accepted, use authenticated Atom feeds for live discovery and Arctic Shift for bodies, comments, and metrics.
3. Declare transport=feeds and expose missing fields and lag in capability notes.
4. Do not build a shared three-way backend abstraction; choose one backend for v1.

## Verification

- Hitting page ten before closing the watermark writes a gap and does not advance complete_since.
- Gap drain fills available history with archive provenance.
- A production tick does not import PRAW.
- Live headers update pacing assumptions without redesign.
- Delete-policy override is refused.

## Sources

- [PLAN M13](../PLAN.md)
- [Reddit source plan](../docs/sources/reddit.md)
