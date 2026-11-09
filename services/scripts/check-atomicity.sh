#!/usr/bin/env bash
# Проверка на живой базе: атомарность Submission + Outbox, идемпотентность, claim/fencing/lease, Watchdog.
# Аргумент (необязательно): номер шарда; по умолчанию — владелец демо-контеста.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/services/storage/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ $# -gt 0 ]]; then
  python -m storage.scripts.check_atomicity --shard "$1"
else
  python -m storage.scripts.check_atomicity
fi
