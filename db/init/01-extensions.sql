CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE ROLE ai_reader LOGIN PASSWORD 'ai_reader';
GRANT CONNECT ON DATABASE usaspending TO ai_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ai_reader;

CREATE SCHEMA IF NOT EXISTS ai;
GRANT USAGE ON SCHEMA ai TO ai_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA ai GRANT SELECT ON TABLES TO ai_reader;

CREATE TABLE IF NOT EXISTS ai.text_embeddings (
    id          BIGSERIAL PRIMARY KEY,
    table_name  TEXT NOT NULL,
    column_name TEXT NOT NULL,
    row_id      TEXT NOT NULL,
    content     TEXT NOT NULL,
    tsv         TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding   VECTOR(768),
    UNIQUE (table_name, column_name, row_id)
);

CREATE INDEX IF NOT EXISTS text_embeddings_tsv_idx
    ON ai.text_embeddings USING GIN (tsv);
CREATE INDEX IF NOT EXISTS text_embeddings_vec_idx
    ON ai.text_embeddings USING hnsw (embedding vector_cosine_ops);
