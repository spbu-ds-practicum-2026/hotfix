#!/usr/bin/env bash
# Применяет миграции на оба шарда. Запускать из любого места.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

for SHARD in 0 1; do
  echo "== миграции: шард $SHARD =="
  alembic -c services/storage/alembic.ini \
    -x "shard=$SHARD" upgrade head
done