"""Live tests that require the running stack (postgres + ollama on localhost).

Auto-skip if the services aren't reachable.
"""
import os
import socket

import pytest


def _port_open(host: str, port: int, timeout=0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def db_alive():
    if not _port_open("localhost", 5432):
        pytest.skip("postgres not reachable on localhost:5432")


@pytest.fixture(scope="module")
def ollama_alive():
    if not _port_open("localhost", 11434):
        pytest.skip("ollama not reachable on localhost:11434")


def test_readonly_cursor_blocks_writes(db_alive):
    from db import readonly_cursor
    import psycopg

    with readonly_cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            cur.execute("CREATE TABLE IF NOT EXISTS _smoke_ro_check (x int)")


def test_readonly_cursor_statement_timeout(db_alive):
    from db import readonly_cursor
    import psycopg

    with readonly_cursor(statement_timeout_ms=200) as cur:
        with pytest.raises(psycopg.errors.QueryCanceled):
            cur.execute("SELECT pg_sleep(2)")


def test_schema_digest_includes_known_tables(db_alive):
    from schema_introspect import build_digest

    out = build_digest()
    assert "## public.agency" in out
    assert "## rpt.recipient_lookup" in out
    # `ai` schema is excluded by design.
    assert "## ai.text_embeddings" not in out


def test_embed_batch_returns_768d_vectors(ollama_alive):
    from embed import embed_batch

    [v] = embed_batch(["the quick brown fox"])
    assert len(v) == 768
    assert all(isinstance(x, float) for x in v[:8])


def test_embed_distinct_after_normalize(ollama_alive):
    from embed import embed_batch, normalize_for_embedding

    a, b = embed_batch([
        normalize_for_embedding("JOHN SMITH"),
        normalize_for_embedding("JANE DOE"),
    ])
    assert a != b
