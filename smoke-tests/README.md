# Smoke tests for the running prototype

Four tracks of testing the local stack (Postgres + pgvector + Ollama +
FastAPI). Each track is self-contained under its own directory. Re-run
instructions and last-known results are below.

Run order recommended:

1. `01-endpoints/` — fast confidence check that all four endpoints respond.
2. `04-pytest/`     — DB-free unit tests for the `ai/` module (no LLM cost).
3. `03-search-eval/` — labeled query set, recall@k for vector / BM25 / RRF.
4. `02-ask-eval/`   — Claude `/ask` quality eval. **Costs Anthropic credits.**

## Findings (2026-05-07)

### Bug fixed: `/search` parameter ordering when `table_name` is set
`agent.py:_vector_search` interpolated `*params` three times into the
positional args list while the SQL only has the table-name placeholder
twice (vec CTE and kw CTE WHERE clauses), and they were in the wrong
order relative to the `query` placeholders. `/search` 500'd with
`the query has 7 placeholders but 8 parameters were passed` whenever
`table_name` was supplied. Patched (`args = [vec, *params, vec, query,
*params, query, k]`) and rebuilt the `ai` image.

### Showstopper: nomic-embed-text collapses ALL-CAPS short strings
The dump's `recipient_lookup.legal_business_name` and
`award_search.description` are stored in upper case. Embedding multiple
distinct upper-case names returns **bit-for-bit identical** 768-d
vectors, e.g. `JOHN SMITH`, `JANE DOE`, `ACME LLC`, `ZEBRA INC` all map
to the same vector. The lower-case versions embed distinctly. Symptom
in `ai.text_embeddings`: 9,997 distinct contents but only 711 distinct
embedding vectors in the recipient_lookup slice.

Consequence: semantic ranking on the existing embeddings is meaningless
— all comparisons either tie at the same cosine distance or rank by
arbitrary physical row order. Track 3 (search-quality eval) cannot run
until embeddings are rebuilt.

Suggested fix in `ai/embed.py`: lowercase texts before sending to
Ollama (and likewise lowercase the query at search time so the two
sides stay consistent). Could also try the `mxbai-embed-large` or
`bge-m3` Ollama models, which use richer tokenizers.

### `readonly_cursor` does not block writes (autocommit pool)
`ai/db.py` opens its `ConnectionPool` with `autocommit=True`, then
issues `SET TRANSACTION READ ONLY` at the start of each cursor scope.
With autocommit, that SET applies only to the implicit transaction
wrapping the SET itself; subsequent statements run in fresh
transactions where the read-only flag is gone. A `CREATE TABLE`
issued inside `readonly_cursor()` succeeds — verified via
`test_live_db.test_readonly_cursor_does_not_block_writes_BUG`.

Practical risk: if Claude ever produces a non-SELECT statement, the
guardrail won't catch it. Two cheap fixes:
1. Open the pool with `autocommit=False` (and commit/rollback explicitly).
2. Or `cur.execute("SET SESSION default_transaction_read_only = on")`
   at the start of each scope — that one persists across statements.
A `statement_timeout` set the same way works because it's a session
GUC, not a per-transaction flag.

### `/ask` and `/agent` were briefly blocked on credits, then recovered
At the start of this session both endpoints 500'd with
`anthropic.BadRequestError: 400 — Your credit balance is too low`.
Mid-run the account came back online; track 2 ran end-to-end and
scored 5/5 (see `02-ask-eval/RESULTS.md`). The eval harness caches
each response to disk so it can be re-scored without spending more
credits — handy when iterating on assertions.

### HNSW returns ties at 6 decimal places
With the buggy embeddings, the HNSW index returned exact-tie distances
(0.6260457…) across 50 candidates. Forcing seqscan (`SET
enable_indexscan = off`) revealed real distance variation. May be a
pgvector quirk worth re-checking *after* rebuilding embeddings — if the
problem persists with healthy embeddings, raise
`hnsw.ef_search` (default 40) or rebuild with higher `m`.

## Status snapshot of the running stack at the time of these tests

- `usaspending-pg`: up; `public.agency` (136 KB), `rpt.recipient_lookup`
  (3.1 GB), `rpt.award_search` (276 GB) restored.
- `usaspending-ollama`: up; `nomic-embed-text:latest` pulled.
- `usaspending-ai`: up after rebuild (parameter-ordering fix).
- `ai.text_embeddings`: 33,005 rows
  (`rpt.recipient_lookup.legal_business_name` × 10,000;
   `rpt.award_search.description` × 23,005). All affected by the
  uppercase bug.
