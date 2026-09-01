# Step 05 — Facebook harvest loop and watermark (M5)

## Outcome

Facebook collection is a bounded lazy generator with a safe incremental watermark, honest opaque coverage, and Group support after the profile-age gate.

## Depends on

- Steps 03 and 04.
- Dedicated profile age of 14 days before first Group access.

## Work, in order

1. Replace the walking skeleton with the full scroll-and-harvest generator.
2. Expand “See more” before capturing each post subtree; truncated raw content cannot be repaired later.
3. Deduplicate in-flight posts by platform item ID while snapshotting before virtualized nodes unmount.
4. Confirm the chronological Group URL manually before relying on it and record current behavior.
5. Use human pacing backed by injectable, seedable randomness.
6. Claim only opaque scroll_unmount coverage. Never invent an interval for a virtual feed.
7. Enforce the 25-minute deadline and graceful SIGTERM checkpoint behavior.
8. Stop after five consecutive already-seen posts, except on an empty database and excluding pinned/sponsored rows.
9. Stamp the last envelope with stop reason and scroll count for SUSPECT detection.
10. Enrol the first Group only after all age, privacy, and acknowledgement gates pass.

## Verification

- Two manual runs over one target produce sensible, similar unique counts.
- --limit 20 stops at 20 and writes no durable cursor.
- Empty-store, pinned, and sponsored cases cannot trip the watermark early.
- A broken pinned selector produces suspect, not a clean empty run.

## Sources

- [PLAN M5](../PLAN.md)
- [Facebook §4–§5, §7, and §9](../docs/sources/facebook.md)
