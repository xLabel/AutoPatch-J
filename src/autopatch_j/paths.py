from __future__ import annotations

from pathlib import Path

PROJECT_STATE_DIR_NAME = ".autopatch-j"
USER_STATE_DIR_NAME = ".autopatch-j"


def project_state_dir(repo_root: Path) -> Path:
    return repo_root / PROJECT_STATE_DIR_NAME


def user_state_dir() -> Path:
    return Path.home() / USER_STATE_DIR_NAME
