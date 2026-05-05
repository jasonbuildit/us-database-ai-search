"""NL -> SQL via Claude tool-use, executed read-only against Postgres."""
from __future__ import annotations
import os
from functools import lru_cache
from anthropic import Anthropic
from db import readonly_cursor
from schema_introspect import build_digest

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ROW_LIMIT = int(os.environ.get("SQL_ROW_LIMIT", "1000"))
TIMEOUT_MS = int(os.environ.get("SQL_STATEMENT_TIMEOUT_MS", "60000"))

client = Anthropic()

SYSTEM = (
    "You translate analytical questions about the USAspending database into "
    "Postgres SQL. Use only tables that appear in the schema digest. Write a "
    "single SELECT statement. Always include an explicit LIMIT (<= "
    f"{ROW_LIMIT}). Prefer CTEs over subqueries. Use ILIKE for fuzzy text. "
    "When the user asks for a chart-style answer, return tidy columns suited "
    "to plotting. Call run_sql exactly once with your final query, then "
    "summarize the result in <= 4 sentences."
)

TOOLS = [
    {
        "name": "run_sql",
        "description": "Execute a read-only Postgres SELECT and return rows.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    }
]


@lru_cache(maxsize=1)
def schema_digest() -> str:
    return build_digest()


def _run_sql(sql: str) -> dict:
    with readonly_cursor(statement_timeout_ms=TIMEOUT_MS) as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description] if cur.description else []
        rows = cur.fetchmany(ROW_LIMIT) if cur.description else []
    return {"columns": cols, "rows": [list(r) for r in rows], "row_count": len(rows)}


def ask(question: str) -> dict:
    messages: list[dict] = [{"role": "user", "content": question}]
    sql_used: str | None = None
    result_payload: dict | None = None

    for _ in range(4):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=[
                {"type": "text", "text": SYSTEM},
                {
                    "type": "text",
                    "text": "Schema digest:\n" + schema_digest(),
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            tools=TOOLS,
            messages=messages,
        )

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            text = "".join(b.text for b in resp.content if b.type == "text")
            return {"answer": text, "sql": sql_used, "result": result_payload}

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for tu in tool_uses:
            if tu.name == "run_sql":
                sql_used = tu.input["sql"]
                try:
                    result_payload = _run_sql(sql_used)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": str(result_payload)[:20000],
                        }
                    )
                except Exception as e:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "is_error": True,
                            "content": f"{type(e).__name__}: {e}",
                        }
                    )
        messages.append({"role": "user", "content": tool_results})

    return {
        "answer": "Stopped after tool-call budget exhausted.",
        "sql": sql_used,
        "result": result_payload,
    }
