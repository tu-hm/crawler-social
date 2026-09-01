# Step 08 — Complete CLI surface (M8)

## Outcome

Every normative command is discoverable, consistently validated, and connected to capabilities.

## Depends on

- Steps 01–07 supply working core and Facebook operations.

## Work, in order

1. Implement every command and flag in [ARCHITECTURE §11](../ARCHITECTURE.md); it is the only naming authority.
2. Group commands into setup/health, targets, collection, reading/repair, and governance.
3. Validate operations from connector capabilities. Capped backfill prints its ceiling; file-import-only sources reject crawl.
4. Enforce --limit as ephemeral cursor mode and --include-private as TTY-only.
5. Keep conversation enrolment forward-only and require acknowledgement plus explicit retention.
6. Make destructive commands print a plan first and require their documented apply mechanism.
7. Return frozen exit codes and actionable errors instead of raw tracebacks.
8. Add rotating file and console logs with source, target, and run ID context.
9. Render each connector's free-text capability note.

## Verification

- Every command in ARCHITECTURE §11 has working --help.
- Every command has an argument-validation test, including invalid capability combinations.
- Non-interactive private export writes nothing and fails clearly.
- No command spelling is independently defined elsewhere.

## Sources

- [PLAN M8](../PLAN.md)
- [ARCHITECTURE §6 and §11](../ARCHITECTURE.md)
- [GOVERNANCE §12 and §16](../docs/GOVERNANCE.md)
