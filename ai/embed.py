"""Batch-embed text columns into ai.text_embeddings using Ollama."""
from __future__ import annotations
import argparse
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import httpx
from db import pool

OLLAMA = os.environ["OLLAMA_HOST"].rstrip("/")
MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")

# Per-thread short ids ("t0", "t1", ...) assigned in order of first observation.
_thread_ids: dict[int, str] = {}
_thread_lock = threading.Lock()


def _tid() -> str:
    tid = threading.get_ident()
    with _thread_lock:
        if tid not in _thread_ids:
            _thread_ids[tid] = f"t{len(_thread_ids)}"
        return _thread_ids[tid]


def _embed_one(client: httpx.Client, text: str, verbose: bool) -> list[float]:
    t0 = time.monotonic()
    r = client.post(
        f"{OLLAMA}/api/embeddings",
        json={"model": MODEL, "prompt": text},
    )
    r.raise_for_status()
    vec = r.json()["embedding"]
    if verbose:
        print(f"[{_tid()}] ok len={len(text):>5d} dur={int((time.monotonic()-t0)*1000):>4d}ms",
              flush=True)
    return vec


def _embed_batch_native(client: httpx.Client, texts: list[str], verbose: bool) -> list[list[float]]:
    """Single HTTP call with input array — Ollama batches inside the model."""
    t0 = time.monotonic()
    r = client.post(
        f"{OLLAMA}/api/embed",
        json={"model": MODEL, "input": texts},
    )
    r.raise_for_status()
    vecs = r.json()["embeddings"]
    if verbose:
        total_chars = sum(len(t) for t in texts)
        dur_ms = int((time.monotonic() - t0) * 1000)
        per_item = dur_ms / max(len(texts), 1)
        print(
            f"[{_tid()}] batch n={len(texts)} chars={total_chars} "
            f"dur={dur_ms}ms ({per_item:.0f}ms/item)",
            flush=True,
        )
    return vecs


def embed_batch(
    texts: list[str],
    concurrency: int = 1,
    verbose: bool = False,
    native_batch: bool = False,
) -> list[list[float]]:
    """Embed a batch of texts.

    native_batch=False (default): one HTTP call per text to /api/embeddings,
                                  optionally parallelized by `concurrency`.
                                  Fastest on CPU-only Ollama.
    native_batch=True:            one HTTP call to /api/embed with input array.
                                  Slower on CPU; kept for GPU setups.
    """
    with httpx.Client(timeout=600) as client:
        if native_batch:
            return _embed_batch_native(client, texts, verbose)
        if concurrency <= 1:
            return [_embed_one(client, t, verbose) for t in texts]
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            return list(ex.map(lambda t: _embed_one(client, t, verbose), texts))


def _build_sql(
    table: str,
    column: str,
    pk: str,
    min_length: int,
    stratify_by: list[str] | None,
    per_stratum: int | None,
    tablesample_pct: float | None,
) -> str:
    where = f"{column} IS NOT NULL AND length({column}) >= {min_length}"
    sample = f" TABLESAMPLE SYSTEM ({tablesample_pct})" if tablesample_pct else ""
    if stratify_by:
        partition = ", ".join(stratify_by)
        cap = f"AND rn <= {per_stratum}" if per_stratum else ""
        return (
            f"WITH ranked AS ("
            f"  SELECT {pk}::text AS id, {column} AS content,"
            f"         ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY random()) AS rn"
            f"  FROM {table}{sample} WHERE {where}"
            f") SELECT id, content FROM ranked WHERE 1=1 {cap} LIMIT %s"
        )
    # Only randomize when we've already shrunk the candidate set with
    # TABLESAMPLE — full ORDER BY random() on a huge table is brutal.
    order = " ORDER BY random()" if tablesample_pct else ""
    return (
        f"SELECT {pk}::text, {column} FROM {table}{sample} "
        f"WHERE {where}{order} LIMIT %s"
    )


def run(
    table: str,
    column: str,
    pk: str,
    limit: int,
    batch: int,
    concurrency: int,
    min_length: int,
    stratify_by: list[str] | None,
    per_stratum: int | None,
    tablesample_pct: float | None,
    verbose: bool,
    native_batch: bool,
) -> None:
    sql = _build_sql(table, column, pk, min_length, stratify_by, per_stratum, tablesample_pct)
    print(f"selecting up to {limit} rows...", flush=True)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SET statement_timeout = '30min'")
        cur.execute(sql, (limit,))
        rows = cur.fetchall()

    print(f"embedding {len(rows)} rows from {table}.{column} (concurrency={concurrency})",
          flush=True)
    run_t0 = time.monotonic()
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        ids = [r[0] for r in chunk]
        texts = [r[1] for r in chunk]
        b0 = time.monotonic()
        vecs = embed_batch(texts, concurrency=concurrency, verbose=verbose,
                           native_batch=native_batch)
        b_dur = time.monotonic() - b0
        with pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO ai.text_embeddings
                  (table_name, column_name, row_id, content, embedding)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (table_name, column_name, row_id)
                DO UPDATE SET content = EXCLUDED.content,
                              embedding = EXCLUDED.embedding
                """,
                [
                    (table, column, rid, txt, vec)
                    for rid, txt, vec in zip(ids, texts, vecs)
                ],
            )
        done = i + len(chunk)
        elapsed = time.monotonic() - run_t0
        overall_rate = done / elapsed if elapsed else 0.0
        batch_rate = len(chunk) / b_dur if b_dur else 0.0
        print(
            f"  upserted {done}/{len(rows)} "
            f"batch={b_dur:.1f}s ({batch_rate:.1f}/s) "
            f"overall={overall_rate:.1f}/s "
            f"threads_seen={len(_thread_ids)}",
            flush=True,
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--column", required=True)
    ap.add_argument("--pk", default="id")
    ap.add_argument("--limit", type=int, default=10_000)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--concurrency", type=int, default=1,
                    help="parallel HTTP requests to Ollama per batch")
    ap.add_argument("--min-length", type=int, default=8,
                    help="skip rows where length(column) < this")
    ap.add_argument("--stratify-by", default="",
                    help="comma-separated columns to stratify the sample over "
                         "(uses ROW_NUMBER OVER PARTITION BY ... ORDER BY random())")
    ap.add_argument("--per-stratum", type=int, default=None,
                    help="cap rows per stratum (only with --stratify-by)")
    ap.add_argument("--tablesample-pct", type=float, default=None,
                    help="TABLESAMPLE SYSTEM (pct) — fast pre-filter for huge tables")
    ap.add_argument("--verbose", action="store_true",
                    help="log every embedding request with thread id and latency")
    ap.add_argument("--native-batch", dest="native_batch", action="store_true",
                    help="use Ollama /api/embed with input array (slower on CPU; "
                         "kept for parity with GPU setups)")
    ap.set_defaults(native_batch=False)
    args = ap.parse_args()
    stratify = [s.strip() for s in args.stratify_by.split(",") if s.strip()] or None
    run(
        args.table, args.column, args.pk, args.limit, args.batch,
        args.concurrency, args.min_length, stratify, args.per_stratum,
        args.tablesample_pct, args.verbose, args.native_batch,
    )
