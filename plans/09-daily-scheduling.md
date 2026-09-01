# Step 09 — Daily scheduling (M9)

## Outcome

One macOS LaunchAgent runs enabled targets twice daily, handles sleep/wake correctly, and leaves a visible operational record.

## Depends on

- Step 08 exposes tick, status, and verdict exit codes.

## Work, in order

1. Implement target iteration ordered by enabled state, priority, and ID.
2. Apply per-target budget, connector pacing, single-flight locking, backoff, and run reporting.
3. Write the strict shell entry point and wrap execution in caffeinate -i.
4. Create one Aqua-session LaunchAgent at 08:05 and 20:35 ICT with no KeepAlive.
5. Bootstrap and inspect the agent with launchctl; verify current macOS behavior.
6. Send a local notification for HUMAN verdicts. STOP stays disarmed until explicit resume.
7. Ensure logs and status explain missed or failed targets after the fact.

## Verification

- One real unattended run appears in the run ledger and status.
- Sleeping through a schedule produces exactly one deferred run after wake.
- A human-required fixture emits a notification and never blocks on stdin.
- Concurrent manual and scheduled runs honor single-flight locks.

## Sources

- [PLAN M9 and §7](../PLAN.md)
- [ARCHITECTURE §8](../ARCHITECTURE.md)
