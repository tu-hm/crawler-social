# Step 14 — X archive importer (M14)

## Outcome

The user's complete X archive, including DMs, imports locally for free with raw-first provenance and correct private-store routing.

## Depends on

- The X0 archive is downloaded and stays outside the repository.
- Step 11 private-store routing is complete.

## Work, in order

1. If the download link expired, request the archive again and download it immediately.
2. Inspect the real ZIP manifest before writing a parser and paste the observed layout into [X §2.3](../docs/sources/x.md).
3. Review the referenced community archive parser as prior art, then implement against the observed manifest.
4. Implement file import over a local path; treat the ZIP as raw input and never call X APIs.
5. Create one DM envelope per conversation, not per ZIP part, so target/person deletion also removes raw conversation bytes.
6. Route DM containers and envelopes to private.db; route public personal history according to its broadcast profile.
7. Preserve import provenance and make repeated imports idempotent.
8. Define --limit for import without advancing a crawl cursor.

## Verification

- Importing the same archive twice produces one logical item set.
- Imported history reaches earlier than the REST timeline ceiling when present.
- DMs cannot be found from social.db or its FTS index.
- Forgetting a DM conversation removes parsed rows and its complete raw envelope.

## Sources

- [PLAN M14](../PLAN.md)
- [X §2](../docs/sources/x.md)
