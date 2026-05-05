# usaspending-ai-search

Local AI analysis stack for the USAspending Postgres dump
(`/Volumes/JAC/usaspending-db_20260406`, 161 GB, 74 `.dat.gz` segments + `toc.dat`).

Phase 1 runs everything in Rancher Desktop containers against a small
selectable subset of tables. Phase 2 (planned, not built yet) pushes the full
dataset to S3 and queries via Athena. See
`/Users/jasso/.claude/plans/toasty-swinging-dongarra.md` for the full design.

## Stack

| Service     | Port  | Purpose                                |
| ----------- | ----- | -------------------------------------- |
| `postgres`  | 5432  | pgvector-enabled Postgres 16           |
| `ollama`    | 11434 | local embeddings (`nomic-embed-text`)  |
| `metabase`  | 3000  | dashboards / SQL verification          |
| `ai`        | 8000  | FastAPI: `/ask`, `/search`, `/agent`   |

## Setup

1. **Copy the dump off the external drive** (don't restore from `/Volumes/JAC`):
   ```
   mkdir -p ./dump
   rsync -a --exclude '._*' /Volumes/JAC/usaspending-db_20260406/ ./dump/
   ```
   Or, to save space, copy only `toc.dat` plus the segments your chosen tables
   reference (find them in `pg_restore -l ./dump`).
2. **Configure env**:
   ```
   cp .env.example .env
   # set ANTHROPIC_API_KEY
   ```
3. **Start the infra**:
   ```
   docker compose up -d postgres ollama metabase
   docker compose exec ollama ollama pull nomic-embed-text
   ```
4. **Pick segments and restore**:
   ```
   # default slice: public.agency + rpt.recipient_lookup (fast, ~3 GB)
   ./restore/pick-segments.sh ./dump
   # or pass schema-qualified table names explicitly:
   ./restore/pick-segments.sh ./dump public.agency rpt.recipient_lookup rpt.award_search
   ./restore/restore.sh
   ```
   Heavy tables in this dump (sizes after restore):
   `rpt.award_search` ≈ 270 GB, `rpt.transaction_search_fpds` ≈ 100+ GB,
   `rpt.transaction_search_fabs` similar. Opt in deliberately.
5. **Start the AI service**:
   ```
   docker compose up -d ai
   ```

## Usage

```
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"q":"top 10 recipients by total obligated amount in FY2024"}' | jq

curl -s localhost:8000/search -H 'content-type: application/json' \
  -d '{"q":"semiconductor research grants","k":10}' | jq

curl -s localhost:8000/agent -H 'content-type: application/json' \
  -d '{"q":"which agencies most often contract with recipients in DE?"}' | jq
```

Embed text columns before using `/search` or `/agent` with vector tools:

```
docker compose exec ai python embed.py \
  --table rpt.recipient_lookup --column legal_business_name --pk id --limit 10000
```

Metabase: open <http://localhost:3000>, add a Postgres connection to host
`postgres`, db `usaspending`, user `postgres`.

## Phase 2 (AWS, planned)

Cost-optimized path:
1. `aws s3 cp --exclude "._*" --recursive ./dump s3://<bucket>/raw/usaspending-db_20260406/`
2. Spin up a transient EC2 instance, run DuckDB's `postgres_scanner` against a
   restored Postgres on local EBS, write Parquet partitioned by `fiscal_year`
   directly to `s3://<bucket>/parquet/`.
3. Register tables in Glue, query with Athena.
4. Optional: `s3://<bucket>/archive/` in Glacier Deep Archive as cold backup.

Full plan: `/Users/jasso/.claude/plans/toasty-swinging-dongarra.md`.
