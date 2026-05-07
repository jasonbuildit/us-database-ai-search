#!/usr/bin/env bash
# Smoke-test the four FastAPI endpoints. Captures responses + timing into ./out/.
# Re-run: ./run.sh
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p out
BASE="${BASE:-http://localhost:8000}"
SUMMARY="out/summary.tsv"
: > "$SUMMARY"  # truncate at start of run

run() {
  local name="$1" method="$2" path="$3" body="${4:-}"
  local out="out/${name}.json" meta="out/${name}.meta"
  echo "==> ${name}: ${method} ${path}"
  local t0 t1 status bytes dur
  t0=$(date +%s)
  if [[ "$method" == "GET" ]]; then
    curl -sS -o "$out" -w '%{http_code}\n' "${BASE}${path}" > "$meta"
  else
    curl -sS -o "$out" -w '%{http_code}\n' \
      -H 'content-type: application/json' \
      -d "$body" "${BASE}${path}" > "$meta"
  fi
  t1=$(date +%s)
  status=$(cat "$meta")
  dur=$((t1 - t0))
  bytes=$(wc -c < "$out" | tr -d ' ')
  echo "  status=${status} dur=${dur}s bytes=${bytes}"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$name" "$method" "$path" "$status" "$dur" "$bytes" >> "$SUMMARY"
}

run health GET /health
run ask_simple   POST /ask    '{"q":"How many agencies are in the public.agency table?"}'
run ask_recip    POST /ask    '{"q":"List 5 distinct business types from rpt.recipient_lookup."}'
run search_basic POST /search '{"q":"university of california","k":5,"table_name":"rpt.recipient_lookup"}'
run search_acro  POST /search '{"q":"NASA","k":5,"table_name":"rpt.recipient_lookup"}'
run agent_simple POST /agent  '{"q":"How many rows are in public.agency?"}'

echo
echo "Done. Results in $(pwd)/out/"
