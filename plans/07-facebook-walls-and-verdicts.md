# Step 07 — Facebook walls and verdicts (M7)

## Outcome

Every known Facebook wall maps to a deterministic action, and hard blocks cannot enter retry loops.

## Depends on

- Step 03 verdict model.
- Captured safe wall fixtures.

## Work, in order

1. Implement a pure detector over HTML, title, status, and final URL with conservative markers and a text-length guard.
2. Implement pure Facebook exception classification to core verdicts.
3. Map login redirect to HUMAN, checkpoint to STOP, temporary block to policy WAIT, and not-joined to DROP.
4. Keep empty-feed-with-200 detection in core SUSPECT logic, not the wall classifier.
5. Store an fb.wall_html envelope before exit so classification can improve from evidence.
6. On STOP, persist the source as stopped. Scheduled ticks no-op until explicit resume.
7. Ensure scheduled runs never prompt for login or other input.

## Verification

- Tests classify all saved walls and reject long benign pages that merely mention wall terms.
- A simulated hard block records a blocked run and exits 86.
- The next tick exits successfully without opening a browser.
- Resume is explicit and auditable.

## Sources

- [PLAN M7](../PLAN.md)
- [Facebook §6](../docs/sources/facebook.md)
- [ARCHITECTURE §10](../ARCHITECTURE.md)
