"""Scripted Metabase setup: admin user, Postgres connection, cards, dashboard.

Idempotent. Re-runs reuse existing entities by name. Use --reset to
delete the dashboard + cards by name before recreating (handy while
iterating on layout).

  smoke-tests/04-pytest/.venv/bin/python metabase/setup_metabase.py
  metabase/setup_metabase.py --reset
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

MB = os.environ.get("METABASE_URL", "http://localhost:3000")
ADMIN_EMAIL = os.environ.get("MB_ADMIN_EMAIL", "admin@local.test")
ADMIN_PASSWORD = os.environ.get("MB_ADMIN_PASSWORD", "Smoke!Tests-2026")
ADMIN_FIRST = "Admin"
ADMIN_LAST = "Local"
SITE_NAME = "USAspending AI"

DB_NAME = "USAspending"
DB_DETAILS = {
    "host": os.environ.get("MB_PG_HOST", "postgres"),  # compose hostname
    "port": int(os.environ.get("MB_PG_PORT", "5432")),
    "dbname": "usaspending",
    "user": os.environ.get("MB_PG_USER", "ai_reader"),
    "password": os.environ.get("MB_PG_PASSWORD", "ai_reader"),
    "ssl": False,
    "tunnel-enabled": False,
    "advanced-options": False,
}

DASHBOARD_NAME = "USAspending AI — Service & Smoke Tests"

# (name, description, sql, display, viz_settings, layout {row,col,size_x,size_y})
CARDS: list[dict] = [
    # Live service state ---------------------------------------------------
    {
        "name": "[Service] Embedding health by column",
        "description": "distinct_ratio < 0.5 indicates the upstream uppercase-collapse bug.",
        "sql": "SELECT table_name, column_name, rows_total, distinct_contents, "
               "distinct_embeddings, distinct_ratio "
               "FROM ops.embedding_health "
               "ORDER BY distinct_ratio",
        "display": "table",
        "viz_settings": {},
        "layout": {"row": 0, "col": 0, "size_x": 12, "size_y": 4},
    },
    {
        "name": "[Service] Restored table sizes (bytes)",
        "description": "Total relation size for the restored slices.",
        "sql": "SELECT n.nspname || '.' || c.relname AS table, "
               "pg_total_relation_size(c.oid) AS bytes "
               "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
               "WHERE c.relkind = 'r' AND n.nspname IN ('public','rpt','ai') "
               "ORDER BY bytes DESC LIMIT 10",
        "display": "row",
        "viz_settings": {"graph.dimensions": ["table"], "graph.metrics": ["bytes"]},
        "layout": {"row": 0, "col": 12, "size_x": 12, "size_y": 4},
    },
    {
        "name": "[Service] Schema digest column count",
        "description": "Total non-excluded columns visible to the AI service.",
        "sql": "SELECT COUNT(*) AS columns_in_digest "
               "FROM information_schema.columns "
               "WHERE table_schema NOT IN ('pg_catalog','information_schema','ai')",
        "display": "scalar",
        "viz_settings": {},
        "layout": {"row": 4, "col": 0, "size_x": 6, "size_y": 3},
    },
    # Smoke + pytest status -----------------------------------------------
    {
        "name": "[Latest] Pytest outcomes",
        "description": "Most recent pytest run outcome breakdown.",
        "sql": "WITH latest AS (SELECT run_id FROM ops.runs "
               "WHERE track='pytest' ORDER BY ran_at DESC LIMIT 1) "
               "SELECT outcome, COUNT(*) AS n FROM ops.pytest_runs "
               "WHERE run_id IN (SELECT run_id FROM latest) "
               "GROUP BY outcome ORDER BY n DESC",
        "display": "pie",
        "viz_settings": {"pie.dimension": "outcome", "pie.metric": "n"},
        "layout": {"row": 4, "col": 6, "size_x": 6, "size_y": 3},
    },
    {
        "name": "[Latest] Endpoint smoke status",
        "description": "Most recent endpoint-smoke run.",
        "sql": "WITH latest AS (SELECT run_id FROM ops.runs "
               "WHERE track='endpoints' ORDER BY ran_at DESC LIMIT 1) "
               "SELECT name, method, path, status, dur_s, bytes "
               "FROM ops.endpoint_smoke "
               "WHERE run_id IN (SELECT run_id FROM latest) "
               "ORDER BY name",
        "display": "table",
        "viz_settings": {},
        "layout": {"row": 4, "col": 12, "size_x": 12, "size_y": 3},
    },
    # /ask eval history ---------------------------------------------------
    {
        "name": "[/ask] Pass rate over time",
        "description": "Pass rate per /ask eval run.",
        "sql": "SELECT r.ran_at, "
               "AVG(CASE WHEN a.passed THEN 1.0 ELSE 0.0 END) AS pass_rate, "
               "COUNT(*) AS fixtures "
               "FROM ops.runs r JOIN ops.ask_eval a USING(run_id) "
               "WHERE r.track='ask_eval' "
               "GROUP BY r.ran_at ORDER BY r.ran_at",
        "display": "line",
        "viz_settings": {"graph.dimensions": ["ran_at"], "graph.metrics": ["pass_rate"]},
        "layout": {"row": 7, "col": 0, "size_x": 12, "size_y": 4},
    },
    {
        "name": "[/ask] Per-fixture trend",
        "description": "1 = pass, 0 = fail per (fixture, run).",
        "sql": "SELECT a.fixture_id, r.ran_at::date AS day, "
               "MAX(CASE WHEN a.passed THEN 1 ELSE 0 END) AS passed "
               "FROM ops.runs r JOIN ops.ask_eval a USING(run_id) "
               "WHERE r.track='ask_eval' "
               "GROUP BY a.fixture_id, day "
               "ORDER BY a.fixture_id, day",
        "display": "table",
        "viz_settings": {},
        "layout": {"row": 7, "col": 12, "size_x": 12, "size_y": 4},
    },
    {
        "name": "[/ask] Latency per fixture (latest run)",
        "description": "Per-fixture latency seconds for the most recent /ask run.",
        "sql": "WITH latest AS (SELECT run_id FROM ops.runs "
               "WHERE track='ask_eval' ORDER BY ran_at DESC LIMIT 1) "
               "SELECT fixture_id, dur_s FROM ops.ask_eval "
               "WHERE run_id IN (SELECT run_id FROM latest) "
               "ORDER BY dur_s DESC NULLS LAST",
        "display": "row",
        "viz_settings": {"graph.dimensions": ["fixture_id"], "graph.metrics": ["dur_s"]},
        "layout": {"row": 11, "col": 0, "size_x": 12, "size_y": 4},
    },
    # Search-eval recall@k ------------------------------------------------
    {
        "name": "[/search] Overall recall@k trend",
        "description": "vector vs bm25 vs rrf, recall on '__overall__' over time.",
        "sql": "SELECT r.ran_at, s.strategy, s.recall "
               "FROM ops.runs r JOIN ops.search_eval s USING(run_id) "
               "WHERE r.track='search_eval' AND s.perturbation='__overall__' "
               "ORDER BY r.ran_at, s.strategy",
        "display": "line",
        "viz_settings": {"graph.dimensions": ["ran_at", "strategy"],
                         "graph.metrics": ["recall"]},
        "layout": {"row": 11, "col": 12, "size_x": 12, "size_y": 4},
    },
    {
        "name": "[/search] Recall by perturbation (latest run)",
        "description": "Heatmap-friendly: rows=perturbation, columns=strategy.",
        "sql": "WITH latest AS (SELECT run_id FROM ops.runs "
               "WHERE track='search_eval' ORDER BY ran_at DESC LIMIT 1) "
               "SELECT perturbation, strategy, recall FROM ops.search_eval "
               "WHERE run_id IN (SELECT run_id FROM latest) "
               "  AND perturbation <> '__overall__' "
               "ORDER BY perturbation, strategy",
        "display": "table",
        "viz_settings": {"table.pivot": True,
                         "table.pivot_column": "strategy",
                         "table.cell_column": "recall"},
        "layout": {"row": 15, "col": 0, "size_x": 24, "size_y": 5},
    },
]


# ---------------------------------------------------------------- HTTP --

class Client:
    def __init__(self):
        self.session: str | None = None

    def _req(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        url = MB.rstrip("/") + path
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if self.session:
            headers["X-Metabase-Session"] = self.session
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = r.read().decode() or "{}"
                return r.status, json.loads(payload) if payload.strip().startswith(("[", "{")) else {"raw": payload}
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(txt)
            except json.JSONDecodeError:
                return e.code, {"raw": txt}

    def get(self, path):  return self._req("GET", path)
    def post(self, path, body): return self._req("POST", path, body)
    def put(self, path, body):  return self._req("PUT", path, body)
    def delete(self, path):     return self._req("DELETE", path)


# ------------------------------------------------------------ helpers --

def ensure_admin(c: Client):
    status, props = c.get("/api/session/properties")
    if status != 200:
        sys.exit(f"Metabase unreachable at {MB}: HTTP {status} {props}")
    if props.get("has-user-setup"):
        # Already set up — log in.
        status, body = c.post("/api/session",
                              {"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        if status != 200:
            sys.exit(
                f"Metabase already set up but admin login failed: {body}.\n"
                f"Set MB_ADMIN_EMAIL/MB_ADMIN_PASSWORD to existing creds, "
                f"or wipe the metabase volume to start fresh.")
        c.session = body["id"]
        print(f"  logged in as {ADMIN_EMAIL}")
        return
    token = props.get("setup-token")
    if not token:
        sys.exit("No setup-token and not yet setup — Metabase in unknown state.")
    payload = {
        "token": token,
        "user": {
            "first_name": ADMIN_FIRST, "last_name": ADMIN_LAST,
            "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
            "site_name": SITE_NAME,
        },
        "prefs": {"site_name": SITE_NAME, "allow_tracking": False},
        "database": None,
    }
    status, body = c.post("/api/setup", payload)
    if status not in (200, 201):
        sys.exit(f"setup failed: HTTP {status} {body}")
    c.session = body["id"]
    print(f"  admin created: {ADMIN_EMAIL}")


def ensure_database(c: Client) -> int:
    status, body = c.get("/api/database")
    items = body.get("data", body) if isinstance(body, dict) else body
    if isinstance(items, list):
        for db in items:
            if db.get("name") == DB_NAME:
                print(f"  reusing database id={db['id']}")
                return db["id"]
    payload = {"name": DB_NAME, "engine": "postgres", "details": DB_DETAILS,
               "is_full_sync": True, "is_on_demand": False}
    status, body = c.post("/api/database", payload)
    if status not in (200, 201):
        sys.exit(f"add database failed: HTTP {status} {body}")
    print(f"  created database id={body['id']}")
    return body["id"]


def wait_for_ops_tables(c: Client, db_id: int, timeout_s: int = 60):
    c.post(f"/api/database/{db_id}/sync_schema", {})
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, body = c.get(f"/api/database/{db_id}/metadata")
        if status == 200:
            tables = {t["schema"] + "." + t["name"] for t in body.get("tables", [])}
            need = {"ops.runs", "ops.endpoint_smoke", "ops.ask_eval",
                    "ops.search_eval", "ops.pytest_runs", "ops.embedding_health"}
            missing = need - tables
            if not missing:
                print(f"  ops.* tables visible to Metabase ({len(tables)} total)")
                return
        time.sleep(2)
    sys.exit(f"timed out waiting for ops.* tables to appear in Metabase metadata")


def upsert_card(c: Client, db_id: int, spec: dict, existing_by_name: dict[str, dict]) -> int:
    body = {
        "name": spec["name"],
        "description": spec.get("description"),
        "display": spec["display"],
        "visualization_settings": spec.get("viz_settings", {}),
        "dataset_query": {
            "type": "native",
            "native": {"query": spec["sql"]},
            "database": db_id,
        },
    }
    if spec["name"] in existing_by_name:
        cid = existing_by_name[spec["name"]]["id"]
        status, resp = c.put(f"/api/card/{cid}", body)
        if status not in (200, 202):
            sys.exit(f"update card '{spec['name']}' failed: HTTP {status} {resp}")
        return cid
    status, resp = c.post("/api/card", body)
    if status not in (200, 201):
        sys.exit(f"create card '{spec['name']}' failed: HTTP {status} {resp}")
    return resp["id"]


def fetch_existing_cards(c: Client) -> dict[str, dict]:
    status, body = c.get("/api/card")
    items = body if isinstance(body, list) else body.get("data", [])
    return {it["name"]: it for it in items if isinstance(it, dict)}


def fetch_dashboard(c: Client, name: str) -> dict | None:
    status, body = c.get("/api/dashboard")
    items = body if isinstance(body, list) else body.get("data", [])
    for d in items:
        if d.get("name") == name:
            return d
    return None


def reset(c: Client):
    dash = fetch_dashboard(c, DASHBOARD_NAME)
    if dash:
        c.delete(f"/api/dashboard/{dash['id']}")
        print(f"  deleted dashboard id={dash['id']}")
    existing = fetch_existing_cards(c)
    for spec in CARDS:
        if spec["name"] in existing:
            cid = existing[spec["name"]]["id"]
            c.delete(f"/api/card/{cid}")
            print(f"  deleted card id={cid} ({spec['name']})")


def attach_cards(c: Client, dashboard_id: int, card_specs_with_ids):
    """PUT /api/dashboard/<id> with the full dashcards array (Metabase >= 0.49)."""
    dashcards = []
    for spec, card_id in card_specs_with_ids:
        L = spec["layout"]
        dashcards.append({
            "id": -abs(hash(spec["name"])) % (10**9),  # negative id = create
            "card_id": card_id,
            "row": L["row"], "col": L["col"],
            "size_x": L["size_x"], "size_y": L["size_y"],
            "parameter_mappings": [],
            "visualization_settings": {},
        })
    status, body = c.put(f"/api/dashboard/{dashboard_id}",
                         {"dashcards": dashcards})
    if status not in (200, 202):
        # Older Metabase: fall back to per-card POST.
        for spec, card_id in card_specs_with_ids:
            L = spec["layout"]
            c.post(f"/api/dashboard/{dashboard_id}/cards",
                   {"cardId": card_id, "row": L["row"], "col": L["col"],
                    "size_x": L["size_x"], "size_y": L["size_y"]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="Delete dashboard + named cards before recreating.")
    args = ap.parse_args()

    c = Client()
    print(f"Metabase: {MB}")
    ensure_admin(c)
    db_id = ensure_database(c)
    wait_for_ops_tables(c, db_id)

    if args.reset:
        reset(c)

    existing = fetch_existing_cards(c)
    pairs = []
    for spec in CARDS:
        cid = upsert_card(c, db_id, spec, existing)
        pairs.append((spec, cid))
        print(f"  card {cid:>3}: {spec['name']}")

    dash = fetch_dashboard(c, DASHBOARD_NAME)
    if dash is None:
        status, dash = c.post("/api/dashboard",
                              {"name": DASHBOARD_NAME,
                               "description": "Live state, smoke results, "
                                              "/ask eval, search recall — auto-built."})
        if status not in (200, 201):
            sys.exit(f"create dashboard failed: HTTP {status} {dash}")
        print(f"  created dashboard id={dash['id']}")
    else:
        print(f"  reusing dashboard id={dash['id']}")

    attach_cards(c, dash["id"], pairs)
    print(f"\nDone. Open: {MB}/dashboard/{dash['id']}")


if __name__ == "__main__":
    main()
