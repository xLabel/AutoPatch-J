"""扫描目标的真实路径、Java 范围及项目状态目录隔离。"""

from __future__ import annotations

import pathlib

from autopatch_j.core.project import repo_path


def select_semgrep_targets(
    repo_root: pathlib.Path, scope: list[str]
) -> list[str]:
    """筛选安全的仓库内 Java 源码目标，排除项目状态及越界软链接。"""
    if not scope:
        return ["."]

    targets: list[str] = []
    for entry in scope:
        try:
            candidate = repo_path.resolve_repo_path(repo_root, entry)
        except repo_path.UnsafeRepoPathError:
            continue
        if not candidate.exists():
            continue
        rel_path = repo_path.to_repo_relative_path(repo_root, candidate)
        if repo_path.is_project_state_path(rel_path):
            continue
        if candidate.is_dir():
            targets.append(pathlib.Path(rel_path).as_posix())
            continue
        if candidate.suffix.lower() == ".java":
            targets.append(pathlib.Path(rel_path).as_posix())
    return targets
