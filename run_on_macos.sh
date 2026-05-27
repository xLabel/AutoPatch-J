#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] Python not found. Please install Python 3.10 or higher."
    exit 1
fi

if [ ! -d "$ROOT_DIR/.venv" ]; then
    echo "[BOOTSTRAP] Creating virtual environment..."
    python3 -m venv "$ROOT_DIR/.venv"
fi

echo "[DEPENDENCY] Syncing project dependencies from pyproject.toml..."
"$ROOT_DIR/.venv/bin/python" -m pip install --quiet -e "$ROOT_DIR"

if [ -z "${AUTOPATCH_LLM_API_KEY:-}" ]; then
    echo "[ERROR] AUTOPATCH_LLM_API_KEY is not set in your shell environment."
    echo "Please export it before running this script, for example:"
    echo "export AUTOPATCH_LLM_API_KEY='your-api-key'"
    exit 1
fi

echo "[SCANNER] Ensuring managed Semgrep runtime..."
"$ROOT_DIR/.venv/bin/python" - <<'PY'
from autopatch_j.scanners.semgrep import install_managed_semgrep_runtime

status, message = install_managed_semgrep_runtime()
print(f"[{status.upper()}] {message}")
raise SystemExit(0 if status == "ok" else 1)
PY

echo "[START] Launching AutoPatch-J (Demo Mode)..."
cd "$ROOT_DIR/examples/demo-repo"
"$ROOT_DIR/.venv/bin/python" -m autopatch_j
