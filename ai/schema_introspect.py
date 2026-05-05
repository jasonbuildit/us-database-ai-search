"""Produce a compact schema digest for prompt-caching as Claude context."""
from __future__ import annotations
import json
from db import readonly_cursor

EXCLUDE_SCHEMAS = ("pg_catalog", "information_schema", "ai")


def build_digest() -> str:
    with readonly_cursor() as cur:
        cur.execute("""
            SELECT table_schema, table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema NOT IN %s
            ORDER BY table_schema, table_name, ordinal_position
        """, (EXCLUDE_SCHEMAS,))
        cols = cur.fetchall()

        cur.execute("""
            SELECT tc.table_schema, tc.table_name, kcu.column_name,
                   ccu.table_schema, ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema   = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema    = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema NOT IN %s
        """, (EXCLUDE_SCHEMAS,))
        fks = cur.fetchall()

        tables: dict[str, dict] = {}
        for s, t, c, dt, nn in cols:
            tables.setdefault(f"{s}.{t}", {"columns": [], "fks": []})
            tables[f"{s}.{t}"]["columns"].append(
                {"name": c, "type": dt, "nullable": nn == "YES"}
            )
        for s, t, c, fs, ft, fc in fks:
            tables[f"{s}.{t}"]["fks"].append(
                {"column": c, "ref": f"{fs}.{ft}.{fc}"}
            )

    lines = ["# USAspending Postgres schema digest", ""]
    for name, meta in sorted(tables.items()):
        lines.append(f"## {name}")
        for col in meta["columns"]:
            null = "" if col["nullable"] else " NOT NULL"
            lines.append(f"- {col['name']} {col['type']}{null}")
        for fk in meta["fks"]:
            lines.append(f"- FK {fk['column']} -> {fk['ref']}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(build_digest())
