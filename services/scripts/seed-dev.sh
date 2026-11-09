#!/usr/bin/env bash
# Применяет миграции на всех шардах и загружает повторяемые демо-данные на шарды-владельцы.
# Безопасно запускать повторно. Чистое окружение: docker compose ... down -v, затем этот скрипт.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/services/storage/src${PYTHONPATH:+:$PYTHONPATH}"
python -m storage.cli migrate
python -m storage.cli seed "$@"
python -m storage.cli distribution
