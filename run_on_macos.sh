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

if ! command -v uv >/dev/null 2>&1; then
    echo "无法启动：未找到 uv。安装指引：https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# uv 校验 Python、锁文件并准备项目环境；模型配置由 CLI 诊断。
echo "[START] Launching AutoPatch-J (Demo Mode)..."
cd "$ROOT_DIR/examples/demo-repo"
uv run --project "$ROOT_DIR" --locked --no-dev --no-python-downloads python -m autopatch_j
