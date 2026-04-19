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
class PendingEdit:
    file_path: str
    old_string: str
    new_string: str
    diff: str
    validation_status: str
    validation_message: str
    rationale: str | None = None
    source_artifact_id: str | None = None
    source_finding_index: int | None = None
    source_check_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "old_string": self.old_string,
            "new_string": self.new_string,
            "diff": self.diff,
            "validation_status": self.validation_status,
            "validation_message": self.validation_message,
            "rationale": self.rationale,
            "source_artifact_id": self.source_artifact_id,
            "source_finding_index": self.source_finding_index,
            "source_check_id": self.source_check_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "PendingEdit":
        return cls(
            file_path=str(data.get("file_path", "")),
            old_string=str(data.get("old_string", "")),
            new_string=str(data.get("new_string", "")),
            diff=str(data.get("diff", "")),
            validation_status=str(data.get("validation_status", "")),
            validation_message=str(data.get("validation_message", "")),
            rationale=str(data.get("rationale", "")) if data.get("rationale") else None,
            source_artifact_id=(
                str(data.get("source_artifact_id")) if data.get("source_artifact_id") else None
            ),
            source_finding_index=(
                int(data.get("source_finding_index"))
                if data.get("source_finding_index") is not None
                else None
            ),
            source_check_id=(
                str(data.get("source_check_id")) if data.get("source_check_id") else None
            ),
        )


@dataclass(slots=True)
class ProjectConfig:
    repo_root: str
    scanner_name: str | None = None
    semgrep_config: str | None = None
    semgrep_bin: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "scanner_name": self.scanner_name,
            "semgrep_config": self.semgrep_config,
            "semgrep_bin": self.semgrep_bin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ProjectConfig":
        return cls(
            repo_root=str(data.get("repo_root", "")),
            scanner_name=str(data.get("scanner_name")) if data.get("scanner_name") else None,
            semgrep_config=(
                str(data.get("semgrep_config")) if data.get("semgrep_config") else None
            ),
            semgrep_bin=str(data.get("semgrep_bin")) if data.get("semgrep_bin") else None,
        )


@dataclass(slots=True)
class SessionState:
    repo_root: str | None = None
    active_scope: list[str] = field(default_factory=list)
    recent_mentions: list[str] = field(default_factory=list)
    current_goal: str | None = None
    active_findings_id: str | None = None
    last_validation_id: str | None = None
    pending_edit: PendingEdit | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "active_scope": list(self.active_scope),
            "recent_mentions": list(self.recent_mentions),
            "current_goal": self.current_goal,
            "active_findings_id": self.active_findings_id,
            "last_validation_id": self.last_validation_id,
            "pending_edit": self.pending_edit.to_dict() if self.pending_edit else None,
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
            last_validation_id=(
                str(data["last_validation_id"]) if data.get("last_validation_id") else None
            ),
            pending_edit=(
                PendingEdit.from_dict(data["pending_edit"])
                if isinstance(data.get("pending_edit"), dict)
                else None
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


def save_config(repo_root: Path, config: ProjectConfig | None = None) -> None:
    resolved_root = str(repo_root.resolve())
    payload = (
        config.to_dict()
        if config is not None
        else ProjectConfig(repo_root=resolved_root).to_dict()
    )
    payload["repo_root"] = resolved_root
    config_file(repo_root).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_config(repo_root: Path) -> ProjectConfig:
    target = config_file(repo_root)
    if not target.exists():
        return ProjectConfig(repo_root=str(repo_root.resolve()))
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return ProjectConfig(repo_root=str(repo_root.resolve()))
    config = ProjectConfig.from_dict(payload)
    if not config.repo_root:
        config.repo_root = str(repo_root.resolve())
    return config


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
