#!/usr/bin/env bash
# Применяет миграции на всех шардах из .env (POSTGRES_SHARD_<N>_*). Запускать из любого места.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/services/storage/src${PYTHONPATH:+:$PYTHONPATH}"
python -m storage.cli migrate "$@"
