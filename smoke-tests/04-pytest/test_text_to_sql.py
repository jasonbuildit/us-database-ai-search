"""Unit tests for ai/text_to_sql.py — Anthropic client is monkeypatched."""
from types import SimpleNamespace
import pytest


def _block(**kw):
    return SimpleNamespace(**kw)


def _mk_resp(*content):
    return SimpleNamespace(content=list(content))


def test_ask_returns_when_no_tool_use(monkeypatch):
    import text_to_sql as t

    canned = _mk_resp(_block(type="text", text="Hello, no tool needed."))
    monkeypatch.setattr(t.client.messages, "create", lambda **_: canned)
    monkeypatch.setattr(t, "schema_digest", lambda: "DIGEST")

    out = t.ask("ping")
    assert out == {"answer": "Hello, no tool needed.", "sql": None, "result": None}


def test_ask_runs_one_sql_then_summarizes(monkeypatch):
    import text_to_sql as t

    tool_use = _block(type="tool_use", id="t1", name="run_sql",
                      input={"sql": "SELECT 1"})
    final = _mk_resp(_block(type="text", text="One row."))

    calls = iter([_mk_resp(tool_use), final])
    monkeypatch.setattr(t.client.messages, "create", lambda **_: next(calls))
    monkeypatch.setattr(t, "schema_digest", lambda: "DIGEST")
    monkeypatch.setattr(t, "_run_sql",
                        lambda sql: {"columns": ["?"], "rows": [[1]], "row_count": 1})

    out = t.ask("how many?")
    assert out["sql"] == "SELECT 1"
    assert out["answer"] == "One row."
    assert out["result"]["row_count"] == 1


def test_ask_stops_after_tool_budget(monkeypatch):
    import text_to_sql as t

    tool_use = _block(type="tool_use", id="t1", name="run_sql",
                      input={"sql": "SELECT 1"})
    monkeypatch.setattr(t.client.messages, "create", lambda **_: _mk_resp(tool_use))
    monkeypatch.setattr(t, "schema_digest", lambda: "DIGEST")

    runs = []
    monkeypatch.setattr(t, "_run_sql",
                        lambda sql: runs.append(sql) or
                                    {"columns": [], "rows": [], "row_count": 0})

    out = t.ask("loop me")
    assert out["answer"].startswith("Stopped after tool-call budget")
    # The for-loop in ask() runs 4 iterations; each iteration that sees a
    # tool_use also runs _run_sql once.
    assert len(runs) == 4


def test_ask_surfaces_sql_error_to_model(monkeypatch):
    import text_to_sql as t

    tool_use = _block(type="tool_use", id="t1", name="run_sql",
                      input={"sql": "SELECT bogus"})
    final = _mk_resp(_block(type="text", text="recovered"))
    calls = iter([_mk_resp(tool_use), final])
    monkeypatch.setattr(t.client.messages, "create", lambda **_: next(calls))
    monkeypatch.setattr(t, "schema_digest", lambda: "DIGEST")

    def boom(_):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(t, "_run_sql", boom)

    out = t.ask("trigger")
    assert out["answer"] == "recovered"
    assert out["sql"] == "SELECT bogus"
    assert out["result"] is None


def test_row_limit_is_int_and_positive():
    import text_to_sql as t
    assert isinstance(t.ROW_LIMIT, int) and t.ROW_LIMIT > 0
