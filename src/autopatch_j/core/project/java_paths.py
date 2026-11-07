"""扫描与语法取证共用的 Java 路径展开，始终复核真实仓库边界。"""

from collections.abc import Collection
import os
import pathlib

from autopatch_j.core.project import repo_path


def raise_walk_error(error: OSError) -> None:
    """目录读取失败必须中止，不能遗漏文件后报告查询完整。"""
    raise error


def expand(
    root: pathlib.Path, paths: tuple[str, ...], ignored_dirs: Collection[str]
) -> tuple[str, ...]:
    """目录遍历过滤忽略项；显式文件准入与源码读取保持一致。"""
    root = root.resolve()
    result = set()
    for name in paths:
        target = repo_path.resolve_source_path(root, name)
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = []
            for directory, dirs, names in os.walk(
                target, onerror=raise_walk_error
            ):
                dirs[:] = [
                    item
                    for item in dirs
                    if item not in ignored_dirs and item != ".autopatch-j"
                ]
                candidates.extend(
                    pathlib.Path(directory) / item
                    for item in names
                    if pathlib.Path(item).suffix.lower() == ".java"
                )
        else:
            raise FileNotFoundError(f"Java 查询入口不存在：{name}")
        for path in candidates:
            resolved = repo_path.resolve_source_path(
                root, path.relative_to(root).as_posix()
            )
            if resolved.suffix.lower() == ".java":
                result.add(resolved.relative_to(root).as_posix())
    return tuple(sorted(result))
