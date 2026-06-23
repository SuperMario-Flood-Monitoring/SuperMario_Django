#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This repair is only needed on macOS."
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${1:-$BACKEND_DIR/.venv}"
PYTHON_BIN="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  echo "Usage: $0 [path-to-venv]" >&2
  exit 1
fi

TOOLKIT_DIR="$("$PYTHON_BIN" - <<'PY'
from pathlib import Path
import swmm.toolkit

print(Path(swmm.toolkit.__file__).resolve().parent)
PY
)"

if [[ ! -d "$TOOLKIT_DIR" ]]; then
  echo "swmm-toolkit directory not found." >&2
  exit 1
fi

echo "Repairing macOS metadata/signatures in: $TOOLKIT_DIR"
xattr -cr "$TOOLKIT_DIR"
codesign --force --sign - "$TOOLKIT_DIR"/*.dylib "$TOOLKIT_DIR"/*.so

"$PYTHON_BIN" - <<'PY'
from pyswmm import Links, Nodes, Simulation

print("PySWMM import OK")
PY
