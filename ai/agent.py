"""Multi-tool Claude agent: SQL + hybrid vector/keyword search + URL fetch."""
from __future__ import annotations
import os
import httpx
from anthropic import Anthropic
from db import readonly_cursor, pool
from embed import embed_batch
from text_to_sql import _run_sql, schema_digest, MODEL, ROW_LIMIT

client = Anthropic()
MAX_STEPS = 8

SYSTEM = (
    "You are an analyst exploring the USAspending database. You have three "
    "tools: sql_query (Postgres SELECT), vector_search (hybrid semantic + "
    "keyword search over embedded text columns), and fetch_url (read a "
    "public web page). Plan briefly, call tools as needed, and answer "
    f"concisely. SQL must be a single SELECT with LIMIT <= {ROW_LIMIT}."
)

TOOLS = [
    {
        "name": "sql_query",
        "description": "Run a read-only Postgres SELECT.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
    {
        "name": "vector_search",
        "description": (
            "Hybrid search over previously embedded text columns. "
            "Returns the top matches by reciprocal-rank fusion of cosine "
            "similarity and full-text rank."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 10},
                "table_name": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": "GET a URL and return up to 8 KB of text.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
]


def _vector_search(query: str, k: int = 10, table_name: str | None = None) -> list[dict]:
    [vec] = embed_batch([query])
    where = "WHERE TRUE"
    params: list = []
    if table_name:
        where += " AND table_name = %s"
        params.append(table_name)

    sql = f"""
        WITH vec AS (
            SELECT id, table_name, column_name, row_id, content,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS r
            FROM ai.text_embeddings
            {where}
            ORDER BY embedding <=> %s::vector
            LIMIT 50
        ),
        kw AS (
            SELECT id, ROW_NUMBER() OVER (
                       ORDER BY ts_rank(tsv, plainto_tsquery('english', %s)) DESC
                   ) AS r
            FROM ai.text_embeddings
            {where} AND tsv @@ plainto_tsquery('english', %s)
            LIMIT 50
        )
        SELECT v.table_name, v.column_name, v.row_id, v.content,
               (1.0/(60 + v.r) + COALESCE(1.0/(60 + kw.r), 0)) AS score
        FROM vec v LEFT JOIN kw ON kw.id = v.id
        ORDER BY score DESC
        LIMIT %s
    """
    args = [vec, *params, vec, *params, query, query, *params, k]
    with readonly_cursor() as cur:
        cur.execute(sql, args)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _fetch_url(url: str) -> str:
    with httpx.Client(timeout=15, follow_redirects=True) as c:
        r = c.get(url, headers={"User-Agent": "usaspending-ai/0.1"})
        r.raise_for_status()
        return r.text[:8000]


def run(question: str) -> dict:
    messages: list[dict] = [{"role": "user", "content": question}]
    trace: list[dict] = []

    for _ in range(MAX_STEPS):
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
            return {"answer": text, "trace": trace}

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for tu in tool_uses:
            try:
                if tu.name == "sql_query":
                    out = _run_sql(tu.input["sql"])
                elif tu.name == "vector_search":
                    out = _vector_search(**tu.input)
                elif tu.name == "fetch_url":
                    out = _fetch_url(tu.input["url"])
                else:
                    out = {"error": f"unknown tool {tu.name}"}
                trace.append({"tool": tu.name, "input": tu.input})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": str(out)[:20000],
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

    return {"answer": "Step budget exhausted.", "trace": trace}
