#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PYTHON_BIN="${PYTHON:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

dependencies_available() {
  "$PYTHON_BIN" -c "import rich, textual" >/dev/null 2>&1
}

in_virtualenv() {
  "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import sys

in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or hasattr(sys, "real_prefix")
raise SystemExit(0 if in_venv else 1)
PY
}

install_dependencies() {
  if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    echo "pip is not available for $PYTHON_BIN" >&2
    echo "Install pip, then rerun this script." >&2
    exit 1
  fi

  local -a pip_args
  if in_virtualenv; then
    pip_args=(-m pip install -e "$SCRIPT_DIR")
  else
    pip_args=(-m pip install --user -e "$SCRIPT_DIR")
  fi

  echo "Installing missing branch-rebaser dependencies..." >&2
  "$PYTHON_BIN" "${pip_args[@]}"
}

if ! dependencies_available; then
  install_dependencies
fi

if ! dependencies_available; then
  echo "Unable to import required dependencies after installation." >&2
  exit 1
fi

export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m branch_rebaser "$@"
