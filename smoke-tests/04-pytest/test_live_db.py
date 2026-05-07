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


def test_readonly_cursor_does_not_block_writes_BUG(db_alive):
    """Documents a bug in ai/db.py: with autocommit=True the
    `SET TRANSACTION READ ONLY` issued at cursor open ends with the
    implicit txn for that SET statement and does NOT apply to
    subsequent statements. Flip this test once the pool is reworked
    (e.g. open with autocommit=False, or set
    `default_transaction_read_only = on` per-session).
    """
    from db import readonly_cursor
    import psycopg

    with readonly_cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)
        cur.execute("CREATE TABLE IF NOT EXISTS _smoke_ro_check (x int)")
        cur.execute("DROP TABLE _smoke_ro_check")  # cleanup
    # If you reach here, writes weren't blocked — the bug is still present.


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


def test_embed_uppercase_collapse_is_documented(ollama_alive):
    """Documents the nomic-embed bug — passes today, fails after model swap."""
    from embed import embed_batch

    a, b = embed_batch(["JOHN SMITH", "JANE DOE"])
    assert a == b, ("Uppercase short-name embeddings used to collapse to the "
                    "same vector. If this assertion fails, the embedder was "
                    "fixed/swapped — flip this test to assert a != b and "
                    "rebuild the search-quality eval baseline.")
