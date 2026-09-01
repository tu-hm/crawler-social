# Step 02 — Facebook walking skeleton (M2)

## Outcome

Ten real posts from one public Facebook Page are queryable in social.db, and enough raw fixtures exist to continue offline.

## Depends on

- Step 01 is complete.
- The dedicated Facebook profile is at least 3 days old.
- Use a public Page only; Groups remain blocked until day 14.

## Work, in order

1. Put all SeleniumBase UC/CDP driver construction in one Facebook driver module.
2. Store the Chrome profile outside the repository with mode 0700; detect profile locks and fail clearly.
3. Keep locale, timezone, viewport, user agent, platform, and language mutually consistent.
4. Run the half-day extraction spike in the order specified in [Facebook §3](../docs/sources/facebook.md) and record a dated result.
5. Always capture viewport HTML. Add GraphQL bodies only if the live spike proves they are decodable and materially better.
6. Scroll and snapshot each viewport as it appears so virtualized feed nodes cannot disappear before capture.
7. Parse only platform_item_id, published_at, and text in this thin path.
8. Insert through Step 01 storage without introducing the final connector contract yet.
9. Capture at least six safe fixtures and matching meta sidecars. Never commit private content.

## Verification

- The canonical query prints ten real posts from the selected public Page.
- The extraction decision, selector evidence, and body encoding are dated in the Facebook source doc.
- At least six reproducible fixtures exist.
- The spike stops after half a day even if GraphQL interception fails; HTML remains the baseline.

## Safety constraints

- No CAPTCHA solving, proxy rotation, credential automation, or account farming.
- Never access a Group in this step.
- A failed GraphQL candidate changes parse quality, not project viability.

## Sources

- [PLAN M2](../PLAN.md)
- [Facebook §2–§3 and §11](../docs/sources/facebook.md)
