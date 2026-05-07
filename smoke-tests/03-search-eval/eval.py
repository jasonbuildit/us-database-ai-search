"""Recall@k for vector / BM25 / RRF on rpt.recipient_lookup embeddings.

Strategy: pick N rows from ai.text_embeddings as ground truth, generate
queries by perturbing the stored content (truncation, drop-word, lower,
swap-words, edit-distance typo). For each perturbed query, score each
strategy by whether the *source* row's id is in the top-K results.

Run from the repo root with the ai container up:
  python smoke-tests/03-search-eval/eval.py --table rpt.recipient_lookup \\
         --sample 200 --k 10 > smoke-tests/03-search-eval/results.json

Embeddings must be healthy. With the current uppercase-collapse bug
(see top-level smoke-tests/README.md) recall@k for vector and RRF
will be near zero — that itself is a useful baseline to compare against
once embeddings are rebuilt.
"""
from __future__ import annotations
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ai"))

os.environ.setdefault("PGHOST", "localhost")
os.environ.setdefault("PGPORT", "5432")
os.environ.setdefault("PGUSER", "postgres")
os.environ.setdefault("PGPASSWORD", "postgres")
os.environ.setdefault("PGDATABASE", "usaspending")
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")

from db import readonly_cursor       # noqa: E402
from embed import embed_batch        # noqa: E402


def perturb(text: str, rng: random.Random) -> dict[str, str]:
    """Return a dict of perturbation_name -> query string."""
    words = text.split()
    out = {"identity": text, "lower": text.lower()}
    if len(words) >= 2:
        out["drop_first"] = " ".join(words[1:])
        out["drop_last"] = " ".join(words[:-1])
        swapped = words[:]
        i = rng.randrange(len(swapped) - 1)
        swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
        out["swap_adjacent"] = " ".join(swapped)
    if len(text) > 6:
        i = rng.randrange(1, len(text) - 1)
        out["typo"] = text[:i] + text[i + 1] + text[i] + text[i + 2:]
        out["truncate"] = text[: max(4, len(text) // 2)]
    return out


def search_vector(query: str, k: int, table_name: str) -> list[int]:
    [vec] = embed_batch([query])
    sql = """
        SELECT id FROM ai.text_embeddings
        WHERE table_name = %s
        ORDER BY embedding <=> %s::vector LIMIT %s
    """
    with readonly_cursor() as cur:
        cur.execute(sql, (table_name, vec, k))
        return [r[0] for r in cur.fetchall()]


def search_bm25(query: str, k: int, table_name: str) -> list[int]:
    sql = """
        SELECT id FROM ai.text_embeddings
        WHERE table_name = %s
          AND tsv @@ plainto_tsquery('english', %s)
        ORDER BY ts_rank(tsv, plainto_tsquery('english', %s)) DESC
        LIMIT %s
    """
    with readonly_cursor() as cur:
        cur.execute(sql, (table_name, query, query, k))
        return [r[0] for r in cur.fetchall()]


def search_rrf(query: str, k: int, table_name: str) -> list[int]:
    [vec] = embed_batch([query])
    sql = """
        WITH vec AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS r
            FROM ai.text_embeddings WHERE table_name = %s
            ORDER BY embedding <=> %s::vector LIMIT 50
        ),
        kw AS (
            SELECT id, ROW_NUMBER() OVER (
                       ORDER BY ts_rank(tsv, plainto_tsquery('english', %s)) DESC
                   ) AS r
            FROM ai.text_embeddings
            WHERE table_name = %s AND tsv @@ plainto_tsquery('english', %s)
            LIMIT 50
        )
        SELECT v.id FROM vec v LEFT JOIN kw ON kw.id = v.id
        ORDER BY (1.0/(60 + v.r) + COALESCE(1.0/(60 + kw.r), 0)) DESC
        LIMIT %s
    """
    with readonly_cursor() as cur:
        cur.execute(sql, (vec, table_name, vec, query, table_name, query, k))
        return [r[0] for r in cur.fetchall()]


STRATEGIES = {"vector": search_vector, "bm25": search_bm25, "rrf": search_rrf}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="rpt.recipient_lookup")
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    with readonly_cursor() as cur:
        cur.execute(
            """SELECT id, content FROM ai.text_embeddings
               WHERE table_name = %s
               ORDER BY random() LIMIT %s""",
            (args.table, args.sample),
        )
        truth = cur.fetchall()

    print(f"# sampled {len(truth)} rows from {args.table}", file=sys.stderr)
    summary = {s: {"hits": 0, "total": 0} for s in STRATEGIES}
    by_perturb = {}
    t0 = time.monotonic()
    for i, (gold_id, content) in enumerate(truth):
        for pname, q in perturb(content, rng).items():
            for sname, fn in STRATEGIES.items():
                ids = fn(q, args.k, args.table)
                hit = gold_id in ids
                summary[sname]["hits"] += int(hit)
                summary[sname]["total"] += 1
                bp = by_perturb.setdefault(pname, {s: {"hits": 0, "total": 0} for s in STRATEGIES})
                bp[sname]["hits"] += int(hit)
                bp[sname]["total"] += 1
        if (i + 1) % 25 == 0:
            print(f"  progress: {i+1}/{len(truth)} elapsed={time.monotonic()-t0:.1f}s",
                  file=sys.stderr)

    out = {
        "table": args.table,
        "sample": args.sample,
        "k": args.k,
        "overall": {
            s: {**v, "recall": v["hits"] / max(v["total"], 1)}
            for s, v in summary.items()
        },
        "by_perturbation": {
            p: {s: {**v, "recall": v["hits"] / max(v["total"], 1)}
                for s, v in d.items()}
            for p, d in by_perturb.items()
        },
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
