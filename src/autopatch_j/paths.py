from __future__ import annotations

import os
from pathlib import Path

# 全局状态目录：存放扫描器二进制、通用规则等
GLOBAL_STATE_DIR_NAME = ".autopatch-j"
# 项目本地状态目录：存放补丁草案、索引数据库、Session 等
PROJECT_STATE_DIR_NAME = ".autopatch-j"


def get_user_home_dir() -> Path:
    """获取用户主目录 (~/)"""
    return Path.home()


def get_global_state_dir() -> Path:
    """获取全局状态目录 (~/.autopatch-j)"""
    path = get_user_home_dir() / GLOBAL_STATE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_project_state_dir(repo_root: Path) -> Path:
    """获取项目本地状态目录 (.autopatch-j)"""
    path = repo_root / PROJECT_STATE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def discover_repo_root(cwd: Path) -> Path | None:
    """向上查找包含 .git 或 .autopatch-j 的目录作为仓库根目录"""
    current = cwd.resolve()
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists() or (parent / PROJECT_STATE_DIR_NAME).exists():
            return parent
    return None


def get_semgrep_runtime_dir() -> Path:
    """获取全局 Semgrep 运行时目录"""
    path = get_global_state_dir() / "scanners" / "semgrep"
    path.mkdir(parents=True, exist_ok=True)
    return path


# 向后兼容别名 (用于 scanners 模块)
project_state_dir = get_project_state_dir
user_state_dir = get_global_state_dir
