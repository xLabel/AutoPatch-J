#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

hold_on_failure() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        echo "AutoPatch-J 未能正常启动或关闭，请查看上方错误。"
        if [ -t 0 ] && [ -t 1 ]; then
            read -r -p "按回车关闭..." reply || true
        fi
    fi
    exit "$result"
}
trap hold_on_failure EXIT

if ! command -v python3 >/dev/null 2>&1; then
    echo "无法启动：未找到 Python，请安装 Python 3.14 或更高版本。"
    exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 14))'; then
    echo "无法启动：需要 Python 3.14 或更高版本。"
    exit 1
fi

if [ ! -d "$ROOT_DIR/.venv" ]; then
    echo "[BOOTSTRAP] Creating virtual environment..."
    if ! python3 -m venv "$ROOT_DIR/.venv"; then
        echo "无法启动：运行环境创建失败，请检查上方错误。"
        exit 1
    fi
fi

if ! "$ROOT_DIR/.venv/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3, 14))'; then
    echo "无法启动：现有 .venv 的 Python 版本不符合要求，请重建环境。"
    exit 1
fi

echo "[DEPENDENCY] Syncing project dependencies from pyproject.toml..."
if ! "$ROOT_DIR/.venv/bin/python" -m pip install --quiet -c "$ROOT_DIR/constraints.txt" -e "$ROOT_DIR"; then
    echo "无法启动：依赖安装失败，请检查上方安装错误。"
    exit 1
fi

echo "[START] Launching AutoPatch-J (Demo Mode)..."
cd "$ROOT_DIR/examples/demo-repo"
"$ROOT_DIR/.venv/bin/python" -m autopatch_j
