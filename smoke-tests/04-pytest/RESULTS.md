# 04-pytest — last run

Run from this directory:

```
pip install pytest anthropic psycopg psycopg-pool httpx
pytest -q
```

Tests:

- `test_text_to_sql.py` — monkeypatches Anthropic client; covers the
  ask() loop's no-tool, single-tool, budget-exhausted, and tool-error
  branches. No network, no DB.
- `test_agent.py` — same approach for the multi-tool agent loop.
  Includes a regression test that captures the parameter list passed
  to `_vector_search`'s SQL (the fixed bug).
- `test_live_db.py` — requires postgres + ollama on localhost. Verifies
  read-only-cursor enforcement, statement_timeout, schema-digest
  contents, and embedding dimensionality. Includes a "documenting"
  test that captures the uppercase-collapse bug — flip it after
  embeddings are fixed.
