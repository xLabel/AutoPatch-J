from __future__ import annotations

from pathlib import Path


class UnsafeRepoPathError(ValueError):
    """用户输入路径无法安全解析到当前仓库内部。"""


def normalize_repo_path(path: str) -> str:
    """统一仓库相对路径表示，保留后续安全解析需要的信息。"""
    clean = str(path or "").replace("\\", "/").strip()
    while clean.startswith("./"):
        clean = clean[2:]
    return clean or "."


def resolve_repo_path(repo_root: Path, path: str) -> Path:
    """
    将用户提供的仓库相对路径解析为真实路径。

    只接受相对路径，并在 resolve 后确认目标仍位于 repo_root 内，防止 ../、
    绝对路径或软链接把读取/扫描/补丁操作带出当前仓库。
    """

    normalized = normalize_repo_path(path)
    candidate = Path(normalized)
    if candidate.is_absolute() or "\x00" in normalized:
        raise UnsafeRepoPathError(f"路径必须是仓库内相对路径：{path}")

    repo_abs = repo_root.resolve()
    target_abs = (repo_abs / normalized).resolve()
    try:
        target_abs.relative_to(repo_abs)
    except ValueError as exc:
        raise UnsafeRepoPathError(f"路径超出项目根目录范围：{path}") from exc
    return target_abs


def try_resolve_repo_path(repo_root: Path, path: str) -> Path | None:
    try:
        return resolve_repo_path(repo_root, path)
    except UnsafeRepoPathError:
        return None


def to_repo_relative_path(repo_root: Path, path: Path) -> str:
    repo_abs = repo_root.resolve()
    target_abs = path.resolve()
    try:
        return target_abs.relative_to(repo_abs).as_posix()
    except ValueError as exc:
        raise UnsafeRepoPathError(f"路径超出项目根目录范围：{path}") from exc
