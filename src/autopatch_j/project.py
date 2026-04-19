from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autopatch_j.indexer import IndexEntry, build_index, load_index, save_index, summarize_index
from autopatch_j.session import (
    APP_DIR_NAME,
    CONFIG_FILE_NAME,
    ProjectConfig,
    SessionState,
    config_file,
    ensure_project_layout,
    index_file,
    load_config,
    load_session,
    save_config,
    save_session,
)


@dataclass(slots=True)
class ProjectSummary:
    repo_root: str
    indexed_entries: int
    indexed_files: int
    indexed_java_files: int
    indexed_directories: int


def initialize_project(repo_root: Path) -> tuple[SessionState, list[IndexEntry], ProjectSummary]:
    repo_root = repo_root.expanduser().resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise ValueError(f"Project path does not exist or is not a directory: {repo_root}")

    ensure_project_layout(repo_root)
    save_config(repo_root)

    index = build_index(repo_root)
    save_index(index_file(repo_root), index)

    session = SessionState(repo_root=str(repo_root))
    save_session(repo_root, session)

    summary = summarize_index(index)
    return (
        session,
        index,
        ProjectSummary(
            repo_root=str(repo_root),
            indexed_entries=summary["entries"],
            indexed_files=summary["files"],
            indexed_java_files=summary["java_files"],
            indexed_directories=summary["directories"],
        ),
    )


def load_project(repo_root: Path) -> tuple[SessionState, list[IndexEntry]]:
    repo_root = repo_root.expanduser().resolve()
    return load_session(repo_root), load_index(index_file(repo_root))


def load_project_config(repo_root: Path) -> ProjectConfig:
    repo_root = repo_root.expanduser().resolve()
    return load_config(repo_root)


def save_project_config(repo_root: Path, config: ProjectConfig) -> None:
    repo_root = repo_root.expanduser().resolve()
    save_config(repo_root, config)


def refresh_project_index(repo_root: Path) -> tuple[list[IndexEntry], ProjectSummary]:
    repo_root = repo_root.expanduser().resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise ValueError(f"Project path does not exist or is not a directory: {repo_root}")

    index = build_index(repo_root)
    save_index(index_file(repo_root), index)
    summary = summarize_index(index)
    return (
        index,
        ProjectSummary(
            repo_root=str(repo_root),
            indexed_entries=summary["entries"],
            indexed_files=summary["files"],
            indexed_java_files=summary["java_files"],
            indexed_directories=summary["directories"],
        ),
    )


def discover_repo_root(start: Path) -> Path | None:
    start = start.expanduser().resolve()
    for candidate in (start, *start.parents):
        config = candidate / APP_DIR_NAME / CONFIG_FILE_NAME
        if config.exists():
            return candidate
    return None
