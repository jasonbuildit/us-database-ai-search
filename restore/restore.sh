#!/usr/bin/env bash
# Restore the selected subset into the running postgres container.
# Usage: ./restore.sh             (uses /dump inside the container)
#        ./restore.sh /some/path  (host path; will be bind-mounted via DUMP_DIR)
set -euo pipefail

cd "$(dirname "$0")/.."

SEGMENTS="restore/segments.txt"
[[ -f "$SEGMENTS" ]] || { echo "missing $SEGMENTS — run restore/pick-segments.sh first"; exit 1; }

# Container sees the dump at /dump (bind-mounted via DUMP_DIR in compose).
docker compose cp "$SEGMENTS" postgres:/tmp/segments.txt

docker compose exec -T postgres pg_restore \
  --username=postgres \
  --dbname=usaspending \
  --jobs=4 \
  --use-list=/tmp/segments.txt \
  --no-owner --no-privileges \
  /dump

docker compose exec -T postgres psql -U postgres -d usaspending -c 'ANALYZE;'
docker compose exec -T postgres psql -U postgres -d usaspending -c '\dt+'
