# Step 11 — Private store and governance (M11)

## Outcome

private.db is encrypted, privacy is resolved before writes, and retention, redaction, purge, export, and routing controls are structural.

## Depends on

- Step 00 settled the SQLCipher/FTS5 branch.
- Step 01 migrations are stable.
- The owner chose explicit conversation retention and reviewed downstream LLM use.

## Work, in order

1. Open SQLCipher with the key pragma first, followed by memory security, secure delete, WAL, and an immediate schema read that fails on a wrong key.
2. Apply the same migrations to both stores unless Step 00 declared the controlled FTS difference.
3. Verify the expected object count through the actual SQLCipher driver.
4. Resolve GovernanceProfile before any write. Route conversations and their envelopes to private.db.
5. Allow connectors to propose privacy, core to resolve it, and configuration only to raise it. Unknown targets fail closed to conversation.
6. Stamp forced privacy permanently so later configuration cannot silently lower it.
7. Run scrub and assert_clean on every non-broadcast body before persistence.
8. Consult block-reingest redactions on ingest and reparse.
9. Implement dry-run-first purge, forget, retention, secure vacuum, and honest APFS snapshot notices.
10. Run retention after tick and before export, but delete nothing until retention.confirmed is true.
11. Enforce default-deny export, identity pseudonymization, and TTY gating.

## Required tests

- Conversation enrolment fails when FileVault is off, acknowledgement is missing, or retention is unspecified.
- MATCH proves private content is unreachable through social.db FTS.
- An unconfirmed retention sweep prints a plan and deletes zero rows.
- Redact, ingest the same fixture, and verify zero rows are recreated.
- Both stores report declared migration versions and accept the same core queries.

## Sources

- [PLAN M11](../PLAN.md)
- [GOVERNANCE §1–§12 and §15–§16](../docs/GOVERNANCE.md)
- [DATA-MODEL §2 and §10](../docs/DATA-MODEL.md)
