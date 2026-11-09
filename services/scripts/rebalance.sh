#!/usr/bin/env bash
# Ручной перенос контестов между шардами.
#   ./services/scripts/rebalance.sh add-shard 2 --dry-run
#   ./services/scripts/rebalance.sh add-shard 2
#   ./services/scripts/rebalance.sh remove-shard 1
# В конце печатается новое значение ACTIVE_SHARDS — его нужно одинаково задать в .env всех сервисов.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/services/storage/src${PYTHONPATH:+:$PYTHONPATH}"
python -m storage.cli rebalance "$@"
