"""Score /ask against a small fixture set with property-based assertions.

Each fixture asserts on result-set properties (row count, column presence,
SQL substring) rather than a brittle reference-SQL match — Claude reorders
columns and picks different aliases.

Costs ~5 Claude API calls (one per fixture) at sonnet-4-6 prices. Will
fail end-to-end while the Anthropic credit balance is low; the score
script also reads cached responses from ./responses/ so you can re-score
without re-spending.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path
import urllib.request
import urllib.error

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures.json"
CACHE = HERE / "responses"
CACHE.mkdir(exist_ok=True)


def call_ask(base: str, q: str, timeout: int = 90) -> dict:
    body = json.dumps({"q": q}).encode()
    req = urllib.request.Request(
        f"{base}/ask",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def check(fix: dict, resp: dict) -> tuple[bool, list[str]]:
    notes: list[str] = []
    e = fix.get("expect", {})
    sql = (resp.get("sql") or "").strip()
    answer = (resp.get("answer") or "").strip()
    result = resp.get("result") or {}
    rows = result.get("row_count")
    ok = True

    if "sql_starts_with" in e and not sql.upper().startswith(e["sql_starts_with"].upper()):
        ok = False; notes.append(f"sql does not start with {e['sql_starts_with']!r}")
    if "row_count_eq" in e and rows != e["row_count_eq"]:
        ok = False; notes.append(f"row_count={rows!r}, expected {e['row_count_eq']}")
    if "row_count_min" in e and (rows is None or rows < e["row_count_min"]):
        ok = False; notes.append(f"row_count={rows!r}, expected >= {e['row_count_min']}")
    for key in ("sql_contains_any", "sql_contains_any_2"):
        if key in e:
            needles = e[key]
            if not any(n in sql for n in needles):
                ok = False; notes.append(f"sql missing any of {needles}")
    if "answer_contains_any" in e:
        needles = e["answer_contains_any"]
        if not any(n.lower() in answer.lower() for n in needles):
            ok = False; notes.append(f"answer missing any of {needles}")
    if "answer_must_not_contain" in e:
        for n in e["answer_must_not_contain"]:
            if n.lower() in (answer + " " + sql).lower():
                ok = False; notes.append(f"forbidden token in output: {n}")
    if e.get("sql_must_be_select_or_null"):
        if sql and not sql.upper().lstrip("(").startswith(("SELECT", "WITH")):
            ok = False; notes.append(f"non-SELECT sql executed: {sql[:60]!r}")
    return ok, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("BASE", "http://localhost:8000"))
    ap.add_argument("--use-cache-only", action="store_true",
                    help="Score using cached responses without calling /ask.")
    args = ap.parse_args()

    fixtures = json.loads(FIXTURES.read_text())
    summary = {"pass": 0, "fail": 0, "error": 0, "items": []}

    for fix in fixtures:
        cache_path = CACHE / f"{fix['id']}.json"
        resp: dict | None = None
        err: str | None = None

        if args.use_cache_only and cache_path.exists():
            resp = json.loads(cache_path.read_text())
        elif args.use_cache_only:
            err = "no cached response"
        else:
            try:
                t0 = time.monotonic()
                resp = call_ask(args.base, fix["q"])
                resp["_dur_s"] = round(time.monotonic() - t0, 2)
                cache_path.write_text(json.dumps(resp, indent=2))
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                err = f"{type(e).__name__}: {e}"

        if err:
            summary["error"] += 1
            summary["items"].append({"id": fix["id"], "ok": False, "error": err})
            print(f"  ERROR {fix['id']}: {err}")
            continue

        ok, notes = check(fix, resp or {})
        summary["pass" if ok else "fail"] += 1
        summary["items"].append({"id": fix["id"], "ok": ok, "notes": notes,
                                  "sql": (resp or {}).get("sql"),
                                  "answer": (resp or {}).get("answer")})
        print(f"  {'PASS' if ok else 'FAIL'} {fix['id']}" + (f" — {'; '.join(notes)}" if notes else ""))

    out = HERE / "results.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\n{summary['pass']} pass / {summary['fail']} fail / {summary['error']} error")
    print(f"Detail: {out}")


if __name__ == "__main__":
    main()
