#!/usr/bin/env bash
# Применяет миграции и загружает демо-данные.
# По умолчанию используется шард 0.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/services/storage/src${PYTHONPATH:+:$PYTHONPATH}"

SHARD="${1:-0}"

if [[ "$SHARD" != "0" && "$SHARD" != "1" ]]; then
  echo "Ошибка: номер шарда должен быть 0 или 1." >&2
  exit 2
fi

alembic -c services/storage/alembic.ini \
  -x "shard=$SHARD" upgrade head

python -m storage.seed.seed --shard "$SHARD"