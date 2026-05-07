# Metabase dashboard for the prototype

A scripted Metabase setup that loads smoke-test results into Postgres
and assembles a 10-card dashboard from them. No clicks required.

```
metabase/
├── ops_schema.sql      # creates ops.* tables and embedding_health view
├── load_results.py     # smoke-tests/0X-*/*.json  →  ops.*
├── setup_metabase.py   # admin user + DB conn + cards + dashboard via API
└── data/               # h2 storage for Metabase (gitignored)
```

## First-time setup

Prereqs: stack is up (`docker compose up -d`), the smoke tracks have
been run at least once (so there's data to load).

```bash
# 1) Create ops.* tables and grants (idempotent).
docker compose exec -T postgres psql -U postgres -d usaspending \
    < metabase/ops_schema.sql

# 2) Load whatever smoke results exist on disk.
smoke-tests/04-pytest/.venv/bin/python metabase/load_results.py

# 3) Build the dashboard. Reads MB_ADMIN_EMAIL / MB_ADMIN_PASSWORD from
#    the environment (defaults match .env.example).
smoke-tests/04-pytest/.venv/bin/python metabase/setup_metabase.py
# → prints "Open: http://localhost:3000/dashboard/<id>"
```

After step 3, log in to Metabase with the admin credentials and open
the printed dashboard URL.

## Re-running

- **Add a new run** to the trend cards: re-run any smoke track, then
  `python metabase/load_results.py`. Each invocation creates a new
  `ops.runs` row; nothing is overwritten.
- **Iterate on layout**: edit the `CARDS` list in `setup_metabase.py`,
  then `python metabase/setup_metabase.py --reset`. Reset deletes the
  dashboard + named cards before recreating, so updates apply cleanly.
- **Wipe history**: `python metabase/load_results.py --truncate`
  (truncates `ops.runs` with CASCADE, then reloads from disk).
- **Wipe Metabase itself**: `docker compose down metabase &&
  rm -rf metabase/data && docker compose up -d metabase`. The
  setup script will reattach to a fresh instance.

## What's on the dashboard

Ten cards across four bands.

| Band | Card | Source |
|---|---|---|
| Live service state | Embedding health by column | `ops.embedding_health` view (live count of `ai.text_embeddings`) |
| Live service state | Restored table sizes | `pg_total_relation_size` |
| Live service state | Schema digest column count | `information_schema.columns` |
| Latest smoke | Pytest outcomes | `ops.pytest_runs` for the latest pytest run |
| Latest smoke | Endpoint smoke status | `ops.endpoint_smoke` for the latest endpoints run |
| /ask history | Pass rate over time | `ops.ask_eval` joined to `ops.runs` |
| /ask history | Per-fixture trend | same |
| /ask history | Latency per fixture (latest) | same |
| /search history | Overall recall@k trend | `ops.search_eval` (perturbation = `__overall__`) |
| /search history | Recall by perturbation | `ops.search_eval` (latest run, all perturbations) |

## Connection details

The dashboard connects as the read-only `ai_reader` Postgres role
(created by `db/init/01-extensions.sql`). `ops_schema.sql` extends
that role's grants to cover `ops.*` and the post-restore `rpt.*`
tables; without those grants, Metabase can see schemas but not query
their tables.

## Troubleshooting

- **Setup says "admin login failed"** — Metabase was set up with
  different credentials. Either set `MB_ADMIN_EMAIL` /
  `MB_ADMIN_PASSWORD` to the existing ones, or wipe `metabase/data/`.
- **Cards show "this card was unable to load"** — check the underlying
  query in the Metabase SQL editor; likely cause is a missing grant.
  Re-apply `ops_schema.sql`.
- **Trend cards show only one point** — only one run is loaded. Run
  the smoke track again and re-run `load_results.py`.
