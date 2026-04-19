from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR_NAME = ".autopatch"
CONFIG_FILE_NAME = "config.json"
SESSION_FILE_NAME = "session.json"
INDEX_FILE_NAME = "index.json"
ARTIFACT_DIRS = ("findings", "logs", "patches", "validations")


@dataclass(slots=True)
class SessionState:
    repo_root: str | None = None
    active_scope: list[str] = field(default_factory=list)
    recent_mentions: list[str] = field(default_factory=list)
    current_goal: str | None = None
    active_findings_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "active_scope": list(self.active_scope),
            "recent_mentions": list(self.recent_mentions),
            "current_goal": self.current_goal,
            "active_findings_id": self.active_findings_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SessionState":
        return cls(
            repo_root=str(data["repo_root"]) if data.get("repo_root") else None,
            active_scope=[str(item) for item in data.get("active_scope", [])],
            recent_mentions=[str(item) for item in data.get("recent_mentions", [])],
            current_goal=str(data["current_goal"]) if data.get("current_goal") else None,
            active_findings_id=(
                str(data["active_findings_id"]) if data.get("active_findings_id") else None
            ),
        )


def app_dir(repo_root: Path) -> Path:
    return repo_root / APP_DIR_NAME


def config_file(repo_root: Path) -> Path:
    return app_dir(repo_root) / CONFIG_FILE_NAME


def session_file(repo_root: Path) -> Path:
    return app_dir(repo_root) / SESSION_FILE_NAME


def index_file(repo_root: Path) -> Path:
    return app_dir(repo_root) / INDEX_FILE_NAME


def ensure_project_layout(repo_root: Path) -> None:
    base_dir = app_dir(repo_root)
    base_dir.mkdir(parents=True, exist_ok=True)
    for dirname in ARTIFACT_DIRS:
        (base_dir / dirname).mkdir(exist_ok=True)


def save_config(repo_root: Path) -> None:
    payload = {"repo_root": str(repo_root.resolve())}
    config_file(repo_root).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_session(repo_root: Path, session: SessionState) -> None:
    session_file(repo_root).write_text(
        json.dumps(session.to_dict(), indent=2),
        encoding="utf-8",
    )


def load_session(repo_root: Path) -> SessionState:
    target = session_file(repo_root)
    if not target.exists():
        return SessionState(repo_root=str(repo_root.resolve()))
    payload = json.loads(target.read_text(encoding="utf-8"))
    return SessionState.from_dict(payload)
