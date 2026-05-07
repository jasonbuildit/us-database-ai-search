"""Unit tests for ai/agent.py multi-tool loop and arg-binding regression."""
from types import SimpleNamespace
import pytest


def _block(**kw):
    return SimpleNamespace(**kw)


def _resp(*content):
    return SimpleNamespace(content=list(content))


def test_run_handles_three_tool_kinds_then_finalizes(monkeypatch):
    import agent

    sql_use = _block(type="tool_use", id="a", name="sql_query",
                     input={"sql": "SELECT 1"})
    vec_use = _block(type="tool_use", id="b", name="vector_search",
                     input={"query": "x", "k": 3})
    url_use = _block(type="tool_use", id="c", name="fetch_url",
                     input={"url": "https://example.com"})
    final = _resp(_block(type="text", text="done"))

    seq = iter([_resp(sql_use), _resp(vec_use), _resp(url_use), final])
    monkeypatch.setattr(agent.client.messages, "create", lambda **_: next(seq))
    monkeypatch.setattr(agent, "schema_digest", lambda: "DIGEST")
    monkeypatch.setattr(agent, "_run_sql", lambda sql: {"rows": [[1]]})
    monkeypatch.setattr(agent, "_vector_search",
                        lambda query, k=10, table_name=None: [{"ok": True}])
    monkeypatch.setattr(agent, "_fetch_url", lambda u: "<html/>")

    out = agent.run("hi")
    assert out["answer"] == "done"
    assert [t["tool"] for t in out["trace"]] == \
        ["sql_query", "vector_search", "fetch_url"]


def test_run_stops_at_max_steps(monkeypatch):
    import agent

    tool = _block(type="tool_use", id="x", name="sql_query",
                  input={"sql": "SELECT 1"})
    monkeypatch.setattr(agent.client.messages, "create",
                        lambda **_: _resp(tool))
    monkeypatch.setattr(agent, "schema_digest", lambda: "DIGEST")
    monkeypatch.setattr(agent, "_run_sql", lambda sql: {"rows": []})

    out = agent.run("loop")
    assert out["answer"].startswith("Step budget")
    assert len(out["trace"]) == agent.MAX_STEPS


def test_vector_search_arg_binding_no_table_name(monkeypatch):
    """Regression for the placeholder/param mismatch fixed on 2026-05-07.

    Without table_name there must be exactly 5 params for 5 placeholders;
    with table_name, exactly 7 for 7. We capture the args without hitting
    the DB.
    """
    import agent

    captured = {}

    class _Cur:
        def execute(self, sql, args):
            captured["sql"] = sql
            captured["args"] = list(args)
        @property
        def description(self):
            return []
        def fetchall(self):
            return []

    class _Ctx:
        def __enter__(self):
            return _Cur()
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(agent, "embed_batch", lambda texts: [[0.0] * 768])
    monkeypatch.setattr(agent, "readonly_cursor", lambda *a, **k: _Ctx())

    agent._vector_search("alpha", k=5)
    assert captured["sql"].count("%s") == len(captured["args"]), captured
    assert len(captured["args"]) == 5

    captured.clear()
    agent._vector_search("alpha", k=5, table_name="rpt.recipient_lookup")
    assert captured["sql"].count("%s") == len(captured["args"]), captured
    assert len(captured["args"]) == 7
    # Ordering: vec, table_name, vec, query, table_name, query, k
    assert captured["args"][1] == "rpt.recipient_lookup"
    assert captured["args"][3] == "alpha"
    assert captured["args"][4] == "rpt.recipient_lookup"
    assert captured["args"][5] == "alpha"
    assert captured["args"][6] == 5
