from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import MEMORY_VERSION
from .text_utils import now_iso


@dataclass(frozen=True, slots=True)
class RecentTurn:
    id: str
    intent: str
    user_text: str
    assistant_text: str
    summary: str
    summary_status: str
    scope_paths: list[str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "intent": self.intent,
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
            "summary": self.summary,
            "summary_status": self.summary_status,
            "scope_paths": list(self.scope_paths),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class MemoryTopic:
    id: str
    label: str
    summary: str
    related_turn_ids: list[str]
    last_touched_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "summary": self.summary,
            "related_turn_ids": list(self.related_turn_ids),
            "last_touched_at": self.last_touched_at,
        }


@dataclass(frozen=True, slots=True)
class LongTermMemoryItem:
    id: str
    type: str
    label: str
    summary: str
    status: str
    source: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "summary": self.summary,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class RepoProfile:
    build_tool: str = ""
    java_version: str = ""
    project_name: str = ""
    modules: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "build_tool": self.build_tool,
            "java_version": self.java_version,
            "project_name": self.project_name,
            "modules": list(self.modules),
            "frameworks": list(self.frameworks),
            "source_files": list(self.source_files),
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class MemoryDocument:
    updated_at: str
    recent_turns: list[RecentTurn] = field(default_factory=list)
    active_topics: list[MemoryTopic] = field(default_factory=list)
    repo_profile: RepoProfile = field(default_factory=RepoProfile)
    durable_preferences: list[LongTermMemoryItem] = field(default_factory=list)
    project_notes: list[LongTermMemoryItem] = field(default_factory=list)
    version: int = MEMORY_VERSION

    @classmethod
    def empty(cls) -> MemoryDocument:
        return cls(updated_at=now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "working_memory": {
                "active_topics": [topic.to_dict() for topic in self.active_topics],
                "recent_turns": [turn.to_dict() for turn in self.recent_turns],
            },
            "repo_profile": self.repo_profile.to_dict(),
            "long_term_memory": {
                "durable_preferences": [item.to_dict() for item in self.durable_preferences],
                "project_notes": [item.to_dict() for item in self.project_notes],
            },
        }
