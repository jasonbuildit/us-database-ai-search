"""Load smoke-test artifacts into ops.* tables for Metabase.

Idempotent in the sense that every invocation creates *new* run rows so
trend cards have fresh history. Use --truncate to wipe ops.* between
loads (handy while iterating on the dashboard).

Usage:
  smoke-tests/04-pytest/.venv/bin/python metabase/load_results.py
  metabase/load_results.py --tracks ask_eval search_eval
  metabase/load_results.py --truncate
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[1]
SMOKE = REPO / "smoke-tests"
ENDPOINTS_DIR = SMOKE / "01-endpoints"
ASK_RESULTS = SMOKE / "02-ask-eval" / "results.json"
SEARCH_RESULTS = SMOKE / "03-search-eval" / "results.json"
PYTEST_DIR = SMOKE / "04-pytest"

ALL_TRACKS = ("endpoints", "ask_eval", "search_eval", "pytest")


def _conn() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", "postgres"),
        dbname=os.environ.get("PGDATABASE", "usaspending"),
    )


def _new_run(cur, track: str, source: Path | None, notes: str | None = None) -> int:
    cur.execute(
        "INSERT INTO ops.runs (track, source_path, notes) VALUES (%s, %s, %s) "
        "RETURNING run_id",
        (track, str(source) if source else None, notes),
    )
    return cur.fetchone()[0]


def load_endpoints(cur) -> int | None:
    summary = ENDPOINTS_DIR / "out" / "summary.tsv"
    if not summary.exists():
        print(f"  endpoints: no summary at {summary}, skipping")
        return None
    rows = []
    for line in summary.read_text().splitlines():
        if not line.strip():
            continue
        name, method, path, status, dur_s, b = line.split("\t")
        rows.append((name, method, path, int(status), float(dur_s), int(b)))
    if not rows:
        print("  endpoints: summary empty, skipping")
        return None
    run_id = _new_run(cur, "endpoints", summary)
    cur.executemany(
        "INSERT INTO ops.endpoint_smoke "
        "(run_id, name, method, path, status, dur_s, bytes) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [(run_id, *r) for r in rows],
    )
    print(f"  endpoints: run_id={run_id}, {len(rows)} rows")
    return run_id


def load_ask_eval(cur) -> int | None:
    if not ASK_RESULTS.exists():
        print(f"  ask_eval: no {ASK_RESULTS}, skipping")
        return None
    data = json.loads(ASK_RESULTS.read_text())
    items = data.get("items", [])
    if not items:
        print("  ask_eval: results.json has no items, skipping")
        return None
    run_id = _new_run(cur, "ask_eval", ASK_RESULTS,
                      f"{data.get('pass',0)}/{len(items)} pass")
    cached_dir = ASK_RESULTS.parent / "responses"
    rows = []
    for it in items:
        cached: dict = {}
        cache_path = cached_dir / f"{it['id']}.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
            except json.JSONDecodeError:
                pass
        rows.append((
            run_id,
            it["id"],
            bool(it.get("ok")),
            "; ".join(it.get("notes", []) or []) or None,
            it.get("sql") or cached.get("sql"),
            it.get("answer") or cached.get("answer"),
            cached.get("_dur_s"),
        ))
    cur.executemany(
        "INSERT INTO ops.ask_eval "
        "(run_id, fixture_id, passed, notes, sql_text, answer, dur_s) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        rows,
    )
    print(f"  ask_eval: run_id={run_id}, {len(rows)} fixtures")
    return run_id


def load_search_eval(cur) -> int | None:
    if not SEARCH_RESULTS.exists():
        print(f"  search_eval: no {SEARCH_RESULTS}, skipping")
        return None
    data = json.loads(SEARCH_RESULTS.read_text())
    k = int(data.get("k", 0))
    rows = []
    for strat, v in data.get("overall", {}).items():
        rows.append((strat, "__overall__", k, v["hits"], v["total"], v["recall"]))
    for pert, by_strat in data.get("by_perturbation", {}).items():
        for strat, v in by_strat.items():
            rows.append((strat, pert, k, v["hits"], v["total"], v["recall"]))
    if not rows:
        print("  search_eval: empty, skipping")
        return None
    run_id = _new_run(cur, "search_eval", SEARCH_RESULTS,
                      f"table={data.get('table')} sample={data.get('sample')} k={k}")
    cur.executemany(
        "INSERT INTO ops.search_eval "
        "(run_id, strategy, perturbation, k, hits, total, recall) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [(run_id, *r) for r in rows],
    )
    print(f"  search_eval: run_id={run_id}, {len(rows)} rows")
    return run_id


def load_pytest(cur) -> int | None:
    venv = PYTEST_DIR / ".venv"
    if not venv.exists():
        print(f"  pytest: no venv at {venv}, skipping")
        return None
    py = venv / "bin" / "python"
    # Best-effort install of the json-report plugin.
    subprocess.run(
        [str(py), "-m", "pip", "install", "-q", "pytest-json-report"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        proc = subprocess.run(
            [str(py), "-m", "pytest", "-q",
             "--json-report", f"--json-report-file={report}"],
            cwd=PYTEST_DIR,
            capture_output=True, text=True,
        )
        if not report.exists():
            print(f"  pytest: report not produced. exit={proc.returncode}")
            print(proc.stdout[-400:])
            return None
        rep = json.loads(report.read_text())
    tests = rep.get("tests", [])
    if not tests:
        print("  pytest: report empty, skipping")
        return None
    summary = rep.get("summary", {})
    run_id = _new_run(
        cur, "pytest", report.name,
        f"{summary.get('passed',0)} passed, {summary.get('failed',0)} failed, "
        f"{summary.get('skipped',0)} skipped",
    )
    rows = [
        (run_id, t["nodeid"], t.get("outcome", "unknown"),
         (t.get("call") or {}).get("duration"))
        for t in tests
    ]
    cur.executemany(
        "INSERT INTO ops.pytest_runs (run_id, nodeid, outcome, dur_s) "
        "VALUES (%s, %s, %s, %s)",
        rows,
    )
    print(f"  pytest: run_id={run_id}, {len(rows)} tests "
          f"({summary.get('passed',0)}p/{summary.get('failed',0)}f/{summary.get('skipped',0)}s)")
    return run_id


LOADERS = {
    "endpoints": load_endpoints,
    "ask_eval": load_ask_eval,
    "search_eval": load_search_eval,
    "pytest": load_pytest,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", default=",".join(ALL_TRACKS),
                    help="Comma-separated subset of: " + ", ".join(ALL_TRACKS))
    ap.add_argument("--truncate", action="store_true",
                    help="Wipe all ops.* run history before loading.")
    args = ap.parse_args()
    tracks = [t.strip() for t in args.tracks.split(",") if t.strip()]
    bad = [t for t in tracks if t not in LOADERS]
    if bad:
        sys.exit(f"unknown tracks: {bad}")

    with _conn() as conn, conn.cursor() as cur:
        if args.truncate:
            cur.execute("TRUNCATE ops.runs RESTART IDENTITY CASCADE")
            print("Truncated ops.runs (CASCADE)")
        print(f"Loading tracks: {tracks}")
        for t in tracks:
            LOADERS[t](cur)
        conn.commit()


if __name__ == "__main__":
    main()
