from __future__ import annotations

from pathlib import Path

from autopatch_j.core.project import UnsafeRepoPathError, resolve_repo_path, to_repo_relative_path


def select_semgrep_targets(repo_root: Path, scope: list[str]) -> list[str]:
    if not scope:
        return ["."]

    targets: list[str] = []
    for entry in scope:
        try:
            candidate = resolve_repo_path(repo_root, entry)
        except UnsafeRepoPathError:
            continue
        if not candidate.exists():
            continue
        rel_path = to_repo_relative_path(repo_root, candidate)
        if candidate.is_dir():
            targets.append(Path(rel_path).as_posix())
            continue
        if candidate.suffix.lower() == ".java":
            targets.append(Path(rel_path).as_posix())
    return targets
