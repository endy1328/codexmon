#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENV_FILE=${CODEXMON_ENV_FILE:-"$ROOT_DIR/.env"}

if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

export PYTHONPATH="${PYTHONPATH:-$ROOT_DIR/src}"
cd "$ROOT_DIR"
exec "${CODEXMON_PYTHON_BIN:-python3}" -m codexmon daemon serve "$@"
