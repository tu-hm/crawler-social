# Step 16 — Zalo deferred boundary

## Outcome

v1 accurately represents Zalo as unavailable without building a Zalo transport, while retaining generic schema and purge hooks for a future legitimate path.

## Depends on

- Core capability notes, governance purge, and container schema from earlier steps.

## Work, in order

1. Do not implement a Zalo crawler in v1.
2. Declare zalo.oa, zalo.bot, and zalo.user as separate deferred registry entries with the exact limitations from [Zalo §2](../docs/sources/zalo.md).
3. Keep all three disabled and out of the scheduler.
4. Ensure generic purge can represent a user_withdraw request for any source.
5. Keep containers.content_unavailable so encrypted or inaccessible threads are explicit rather than silently empty.
6. Preserve source-scoped actor identities; never assume a global Zalo user ID.
7. Perform only the three high-value deferred checks when useful: inspect the Zalo PC export ZIP, test individual bot signup, and verify household-business OA eligibility.
8. Reopen a transport only when its documented trigger fires; write a new ADR before implementation.

## Explicitly prohibited

- Selenium against chat.zalo.me.
- Unofficial personal-account access on a primary account.
- Pretending Bot or OA history covers personal DMs.
- Building webhook or sidecar machinery before an enabled transport needs it.

## Verification

- Capability output explains why each transport is unavailable and what unlocks it.
- Scheduled ticks never initialize Zalo code.
- Generic withdrawal purge and content_unavailable behavior have fixture tests.
- No Zalo automation dependency is installed in v1.

## Sources

- [PLAN “Not a phase — Zalo” and §11](../PLAN.md)
- [Zalo source plan](../docs/sources/zalo.md)
- [DECISIONS ADR-0060](../docs/DECISIONS.md)
