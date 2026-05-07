-- ops.* schema: smoke-test history surfaced through Metabase.
-- Idempotent. Run as a superuser (postgres) — grants are issued for ai_reader.

CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.runs (
    run_id      BIGSERIAL PRIMARY KEY,
    track       TEXT NOT NULL,            -- 'endpoints' | 'ask_eval' | 'search_eval' | 'pytest'
    ran_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_path TEXT,
    notes       TEXT
);
CREATE INDEX IF NOT EXISTS runs_track_ran_at_idx ON ops.runs (track, ran_at DESC);

CREATE TABLE IF NOT EXISTS ops.endpoint_smoke (
    run_id  BIGINT REFERENCES ops.runs(run_id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    method  TEXT NOT NULL,
    path    TEXT NOT NULL,
    status  INT  NOT NULL,
    dur_s   NUMERIC,
    bytes   INT,
    PRIMARY KEY (run_id, name)
);

CREATE TABLE IF NOT EXISTS ops.ask_eval (
    run_id     BIGINT REFERENCES ops.runs(run_id) ON DELETE CASCADE,
    fixture_id TEXT NOT NULL,
    passed     BOOLEAN NOT NULL,
    notes      TEXT,
    sql_text   TEXT,
    answer     TEXT,
    dur_s      NUMERIC,
    PRIMARY KEY (run_id, fixture_id)
);

CREATE TABLE IF NOT EXISTS ops.search_eval (
    run_id       BIGINT REFERENCES ops.runs(run_id) ON DELETE CASCADE,
    strategy     TEXT NOT NULL,
    perturbation TEXT NOT NULL,
    k            INT  NOT NULL,
    hits         INT  NOT NULL,
    total        INT  NOT NULL,
    recall       NUMERIC NOT NULL,
    PRIMARY KEY (run_id, strategy, perturbation)
);

CREATE TABLE IF NOT EXISTS ops.pytest_runs (
    run_id  BIGINT REFERENCES ops.runs(run_id) ON DELETE CASCADE,
    nodeid  TEXT NOT NULL,
    outcome TEXT NOT NULL,
    dur_s   NUMERIC,
    PRIMARY KEY (run_id, nodeid)
);

-- Live embedding-health view. Drives the uppercase-collapse indicator.
CREATE OR REPLACE VIEW ops.embedding_health AS
SELECT
    table_name,
    column_name,
    COUNT(*)                        AS rows_total,
    COUNT(DISTINCT content)         AS distinct_contents,
    COUNT(DISTINCT embedding::text) AS distinct_embeddings,
    ROUND(COUNT(DISTINCT embedding::text)::numeric / NULLIF(COUNT(*), 0), 4)
                                    AS distinct_ratio
FROM ai.text_embeddings
GROUP BY table_name, column_name;

-- Convenience view: latest run per track.
CREATE OR REPLACE VIEW ops.latest_runs AS
SELECT DISTINCT ON (track) run_id, track, ran_at, source_path, notes
FROM ops.runs
ORDER BY track, ran_at DESC;

-- Grants for ai_reader (the role Metabase connects as).
GRANT USAGE ON SCHEMA ops TO ai_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO ai_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT ON TABLES TO ai_reader;

-- pg_restore created rpt without granting to ai_reader. Fix it.
GRANT USAGE ON SCHEMA rpt TO ai_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA rpt TO ai_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA rpt GRANT SELECT ON TABLES TO ai_reader;
