# Step 00 — Preflight and scaffold (M0)

## Outcome

The project can start safely, the local runtime is proven compatible, and every external request with an uncontrolled waiting period has been started.

## Prerequisites

- Read [README](../README.md), [PLAN §1–§6](../PLAN.md), and [ARCHITECTURE §3](../ARCHITECTURE.md).
- Answer or explicitly defer the six owner decisions in [PLAN §13](../PLAN.md). Conversation retention and downstream LLM use must be settled before Steps 11–12.
- Do not fetch platform data as part of automated tests in this step.

## Work, in order

1. Start the four external tracks immediately:
   - Submit the Reddit app request (R0), retain its ticket number, and calendar the 2026-09-30 and 2026-12-31 decisions.
   - Obtain Telegram api_id and api_hash (T0) and store them in Keychain. If the form fails, record the date and failure.
   - Request and promptly download the X archive (X0); keep it outside the repository.
   - Create the dedicated Facebook Chrome profile (P0), log in interactively, and begin the 3-day Page / 14-day Group age clocks.
2. Initialize one Python 3.13 uv project and expose the crawler entry point.
3. Define the core, facebook, telegram, reddit, crypto, and dev dependency groups from [PLAN M0](../PLAN.md).
4. Add the initial module layout from [ARCHITECTURE §3](../ARCHITECTURE.md); keep later commands as honest stubs.
5. Implement one Config dataclass and YAML loader, with every default defined once.
6. Implement crawler doctor checks for:
   - Python and SQLite versions.
   - local_day STORED and FTS5 remove_diacritics 2 on the project interpreter.
   - SQLCipher compile options, especially ENABLE_FTS5; record the selected branch from [DATA-MODEL §14](../docs/DATA-MODEL.md).
   - FileVault status, Keychain access, unsafe/synced data paths, and Facebook profile age.
7. Implement crawler init-keys to create, but never silently replace, the identity pepper and private-database key.
8. Ignore data, exports, browser profiles, databases, sessions, and environment files in Git.
9. Record every verification result in the relevant source doc; never promote an unchecked claim to fact.

## Verification

- uv run crawler --help succeeds and identifies all planned commands, marking unavailable ones clearly.
- uv run crawler doctor reports every environment gate and a remediation.
- Both Keychain items resolve without printing their values.
- R0 has a ticket, T0 has credentials or a dated failure, X0 is requested, and P0's profile age is visible.

## Stop conditions

- If SQLCipher lacks FTS5, document one frozen branch before Step 11.
- If T0 fails, use the fallback connector order in [PLAN M0](../PLAN.md).
- Do not begin Step 02 before the Facebook Page age threshold.

## Sources

- [PLAN M0 and §12](../PLAN.md)
- [ARCHITECTURE §12 and §16](../ARCHITECTURE.md)
- [DATA-MODEL §14](../docs/DATA-MODEL.md)
