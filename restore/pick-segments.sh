#!/usr/bin/env bash
# Generate restore/segments.txt — a selective TOC list pg_restore will consume.
# Usage: ./pick-segments.sh /path/to/dump-dir [table1 table2 ...]
# Defaults to a self-contained 5-table slice useful for AI exploration.
set -euo pipefail

DUMP_DIR="${1:?usage: pick-segments.sh DUMP_DIR [table ...]}"
shift || true

DEFAULT_TABLES=(
  public.agency
  rpt.recipient_lookup
)

TABLES=("$@")
[[ ${#TABLES[@]} -eq 0 ]] && TABLES=("${DEFAULT_TABLES[@]}")

OUT="$(dirname "$0")/segments.txt"
TMP="$(mktemp)"
pg_restore -l "$DUMP_DIR" > "$TMP"

# pg_restore -l TOC format: "id; oid oid KIND schema name owner"
# (schema and name are space-separated, NOT schema.name).
# Tables are accepted as "schema.name" or bare "name" (defaults to public).
{
  grep -E '^;' "$TMP" || true
  grep -E ' (SCHEMA|EXTENSION|COMMENT) ' "$TMP" | grep -v 'TABLE DATA' || true
  for spec in "${TABLES[@]}"; do
    if [[ "$spec" == *.* ]]; then
      schema="${spec%%.*}"; t="${spec#*.}"
    else
      schema="public"; t="$spec"
    fi
    grep -E " (TABLE|TABLE DATA|INDEX|CONSTRAINT|FK CONSTRAINT|DEFAULT|TRIGGER|TABLE ATTACH) ${schema} ${t}( |$)" "$TMP" || true
    # Sequences that back this table's columns may live in a different schema (e.g. public.${t}_id_seq).
    grep -E " SEQUENCE (public|raw|rpt|int) ${t}_[A-Za-z0-9_]+( |$)" "$TMP" || true
    grep -E " SEQUENCE OWNED BY (public|raw|rpt|int) ${t}_[A-Za-z0-9_]+( |$)" "$TMP" || true
    grep -E " SEQUENCE SET (public|raw|rpt|int) ${t}_[A-Za-z0-9_]+( |$)" "$TMP" || true
  done
} | awk '!seen[$0]++' > "$OUT"

echo "wrote $OUT"
echo "tables: ${TABLES[*]}"
echo "entries: $(wc -l < "$OUT")"
rm -f "$TMP"
