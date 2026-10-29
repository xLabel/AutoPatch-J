"""仓库相对路径、真实目标和项目状态目录的统一准入。"""

from __future__ import annotations

import pathlib

PROJECT_STATE_DIR_NAME = ".autopatch-j"


class UnsafeRepoPathError(ValueError):
    """用户输入路径无法安全解析到当前仓库内部。"""


def normalize_repo_path(path: str) -> str:
    """统一仓库相对路径表示，保留后续安全解析需要的信息。"""
    clean = (path or "").replace("\\", "/").strip()
    while clean.startswith("./"):
        clean = clean[2:]
    return clean or "."


def is_project_state_path(path: str) -> bool:
    """归一化仓库相对路径是否属于项目状态目录。"""

    parts = pathlib.Path(normalize_repo_path(path)).parts
    return bool(
        parts and parts[0].casefold() == PROJECT_STATE_DIR_NAME.casefold()
    )


def resolve_repo_path(repo_root: pathlib.Path, path: str) -> pathlib.Path:
    """将用户提供的仓库相对路径解析为真实路径。

    只接受相对路径，并在 resolve 后确认目标仍位于 repo_root 内，防止 ../、
    绝对路径或软链接把读取/扫描/补丁操作带出当前仓库。
    """

    normalized = normalize_repo_path(path)
    candidate = pathlib.Path(normalized)
    if candidate.is_absolute() or "\x00" in normalized:
        raise UnsafeRepoPathError(f"路径必须是仓库内相对路径：{path}")

    repo_abs = repo_root.resolve()
    target_abs = (repo_abs / normalized).resolve()
    try:
        target_abs.relative_to(repo_abs)
    except ValueError as exc:
        raise UnsafeRepoPathError(f"路径超出项目根目录范围：{path}") from exc
    return target_abs


def resolve_source_path(repo_root: pathlib.Path, path: str) -> pathlib.Path:
    """解析仓库内源码真实目标，拒绝项目状态目录及指向它的软链接。"""
    target = resolve_repo_path(repo_root, path)
    relative = to_repo_relative_path(repo_root, target)
    if is_project_state_path(relative):
        raise UnsafeRepoPathError(f"项目状态目录不属于源码范围：{path}")
    return target


def try_resolve_repo_path(
    repo_root: pathlib.Path, path: str
) -> pathlib.Path | None:
    """解析仓库内相对路径，越界或非法输入时返回 None。"""
    try:
        return resolve_repo_path(repo_root, path)
    except UnsafeRepoPathError:
        return None


def to_repo_relative_path(repo_root: pathlib.Path, path: pathlib.Path) -> str:
    """将真实仓库内目标转为 POSIX 相对路径；越界则拒绝。"""
    repo_abs = repo_root.resolve()
    target_abs = path.resolve()
    try:
        return target_abs.relative_to(repo_abs).as_posix()
    except ValueError as exc:
        raise UnsafeRepoPathError(f"路径超出项目根目录范围：{path}") from exc
