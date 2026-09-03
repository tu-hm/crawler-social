# Step 04 — Parse and store five post fields

## Outcome

Saved Facebook HTML becomes queryable posts through a pure, offline parser.

## Depends on

- [Step 03](./03-facebook-capture.md) produced at least one safe fixture.

## Work, in order

1. Add `parser.py` with a pure entry point:

   ```python
   parse(html: bytes, page_url: str, captured_at: datetime) -> list[Post]
   ```

2. Extract only:
   - Facebook post ID;
   - Page URL;
   - text;
   - author;
   - published time.
3. Treat text as the only required content field after the post ID. Store `None` when an
   optional value is unavailable; never infer an author or time from unrelated text.
4. Use `captured_at` only to resolve an explicitly relative timestamp.
5. Keep parsing independent of the browser, OS, database, secrets, environment, and wall
   clock.
6. Add table-driven fixture tests, including missing author, missing time, and malformed
   post cases.
7. In the crawl pipeline, load the already committed snapshot, parse it, and upsert its
   posts.
8. Implement `crawler posts --limit N [--contains TEXT]` with parameterized SQL.

## Required tests

- Fixtures produce stable Facebook post IDs.
- A missing optional field becomes `None` without failing the whole snapshot.
- Malformed candidate nodes are skipped with a diagnostic.
- Parser tests pass with browser-related packages unavailable.
- Text filtering cannot alter SQL syntax.

## Verification

```console
uv run pytest tests/test_parser.py
uv run crawler crawl "https://www.facebook.com/<page>" --limit 20
uv run crawler posts --limit 10
uv run crawler posts --contains "example" --limit 10
```

At least 10 real posts are queryable, or the run reports a clear fixture-backed parser
problem without losing its snapshots.
