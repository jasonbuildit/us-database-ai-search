# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local AI analysis stack (Postgres + pgvector + Ollama + FastAPI) over a
USAspending `pg_dump`. Phase 1 (built) runs a small subset locally; Phase 2
(planned only) is Parquet on S3 + Athena. Full design and end-to-end setup
are in `README.md` and the plan file
`/Users/jasso/.claude/plans/toasty-swinging-dongarra.md`.

## Commands worth knowing

The README lists the full setup sequence. The non-obvious ones:

```
./restore/pick-segments.sh ./dump [schema.table ...]   # rebuild restore/segments.txt
docker compose exec ai python embed.py \
  --table rpt.recipient_lookup --column legal_business_name --pk id --limit 10000
```

Tables in this dump are schema-qualified — pass them as `schema.name`
(e.g. `rpt.award_search`, `public.agency`). Bare names default to `public`.

Verify changes by hitting the FastAPI endpoints with `curl` (examples in
README) and by running `docker compose exec ai python schema_introspect.py`.

A pytest suite + smoke harness lives under `smoke-tests/` (four tracks:
endpoints, /ask eval, search-quality eval, pytest). Each track writes JSON
under `smoke-tests/0X-*/`; `metabase/load_results.py` ingests those into
`ops.*` tables and `metabase/setup_metabase.py` builds the dashboard. See
`smoke-tests/README.md` and `metabase/README.md`.

## Architecture

- `docker-compose.yml` orchestrates four services: `postgres`, `ollama`,
  `metabase` (h2 backend persisted to `./metabase/data/`), and `ai`.
  `postgres` is built from
  `db/` (pgvector-enabled image + init SQL that creates the `vector`/`pg_trgm`
  extensions, an `ai_reader` read-only role, and the `ai.text_embeddings`
  table with HNSW + GIN indexes). The dump directory is bind-mounted at
  `/dump` via the `DUMP_DIR` env var.
- `restore/pick-segments.sh` parses `pg_restore -l` output to build a
  selective TOC list (`segments.txt`) for a chosen set of tables. Defaults to
  the small slice `public.agency` + `rpt.recipient_lookup`. The dump's
  partitioned/heavy tables (`rpt.award_search`, `rpt.transaction_search`,
  `rpt.transaction_search_fpds`, `rpt.transaction_search_fabs`) are 80–270 GB
  each restored — opt in only when you have headroom. The script handles two
  TOC quirks: schema and name are space-separated (not `schema.name`), and a
  table's owning sequence may live in a different schema (e.g.
  `rpt.recipient_lookup` defaults from `public.recipient_lookup_id_seq`).
  `restore/restore.sh` runs `pg_restore --use-list` inside the container with
  `--jobs=4`.
- `ai/` is a FastAPI app. Three endpoints share one schema digest produced by
  `schema_introspect.py` and cached via Anthropic prompt caching:
  - `/ask` (`text_to_sql.py`): single-tool Claude loop that emits one SELECT
    against a read-only cursor with `statement_timeout` and a row cap.
  - `/search` (`agent.py:_vector_search`): reciprocal-rank fusion of pgvector
    cosine and Postgres `tsvector` BM25.
  - `/agent` (`agent.py:run`): multi-step Claude tool-use over `sql_query`,
    `vector_search`, `fetch_url` with an 8-step budget.
- `db.py` owns the `psycopg_pool.ConnectionPool` and a `readonly_cursor`
  context manager (sets `TRANSACTION READ ONLY` + `statement_timeout`). All
  SQL paths in the AI service go through it.
- Embeddings use Ollama (`nomic-embed-text`, 768-dim) via HTTP from
  `embed.py`; the same function is reused at agent query time for the search
  tool.
- Cross-module coupling inside `ai/`: `agent.py` imports `_run_sql`,
  `schema_digest`, `MODEL`, and `ROW_LIMIT` from `text_to_sql.py`, and
  `server.py` re-exports `agent._vector_search` as the `/search` handler.
  Renaming any of these silently breaks `/agent` or `/search` — update all
  three modules together.

## Things to know before editing

- The `ai` service won't start without `ANTHROPIC_API_KEY` in `.env` — copy
  `.env.example` first.
- `restore/restore.sh` uses `docker compose cp` + `docker compose exec`, so
  `postgres` must already be `up` and healthy before you run it.
- The embed/search paths fail until `docker compose exec ollama ollama pull
  nomic-embed-text` has been run once; the model persists in the `./ollama`
  volume after that.
- The schema digest is cached in-process (`@lru_cache`) and sent as a
  cache-controlled system block. After restoring new tables, restart the `ai`
  container so the digest refreshes.
- `ai.text_embeddings.embedding` is `vector(768)`. If you swap `EMBED_MODEL`
  to one with a different dimension, update the column type and HNSW index.
- The default model env is `claude-sonnet-4-6`. Override with
  `ANTHROPIC_MODEL=claude-opus-4-7` for harder SQL.
- Don't `pg_restore` directly from `/Volumes/JAC` — external-drive throughput
  and macOS `._*` AppleDouble files cause failures. Always copy/rsync to
  `./dump` first (`--exclude '._*'`).
- `pgdata/`, `dump/`, `ollama/`, `metabase/data/`, `.env` are gitignored and
  large — never commit them.
- Both index-time and query-time embedding paths normalize text via
  `embed.normalize_for_embedding` (NFKC + casefold + whitespace collapse) —
  short ALL-CAPS inputs would otherwise collapse to identical
  `nomic-embed-text` vectors. If you swap the embedder or add a new caller
  of `embed_batch`, run that text through the same normalizer.
