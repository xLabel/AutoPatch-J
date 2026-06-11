from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import MEMORY_VERSION
from .text_utils import now_iso


@dataclass(frozen=True, slots=True)
class MemoryEpisode:
    id: str
    intent: str
    user_goal: str
    assistant_result: str
    summary: str
    summary_status: str
    scope_paths: list[str]
    importance: int
    created_at: str
    last_accessed_at: str
    access_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "intent": self.intent,
            "user_goal": self.user_goal,
            "assistant_result": self.assistant_result,
            "summary": self.summary,
            "summary_status": self.summary_status,
            "scope_paths": list(self.scope_paths),
            "importance": self.importance,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
        }


@dataclass(frozen=True, slots=True)
class MemoryTopic:
    id: str
    label: str
    summary: str
    related_episode_ids: list[str]
    last_touched_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "summary": self.summary,
            "related_episode_ids": list(self.related_episode_ids),
            "last_touched_at": self.last_touched_at,
        }


@dataclass(frozen=True, slots=True)
class SemanticMemoryItem:
    id: str
    type: str
    label: str
    summary: str
    source_episode_ids: list[str]
    confidence: str
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "summary": self.summary,
            "source_episode_ids": list(self.source_episode_ids),
            "confidence": self.confidence,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ProceduralMemoryItem:
    id: str
    type: str
    label: str
    summary: str
    source_episode_ids: list[str]
    confidence: str
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "summary": self.summary,
            "source_episode_ids": list(self.source_episode_ids),
            "confidence": self.confidence,
            "status": self.status,
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
class MemoryMaintenance:
    last_consolidated_at: str = ""
    last_compacted_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_consolidated_at": self.last_consolidated_at,
            "last_compacted_at": self.last_compacted_at,
        }


@dataclass(frozen=True, slots=True)
class MemoryDocument:
    updated_at: str
    active_topics: list[MemoryTopic] = field(default_factory=list)
    pending_episode_ids: list[str] = field(default_factory=list)
    episodes: list[MemoryEpisode] = field(default_factory=list)
    repo_profile: RepoProfile = field(default_factory=RepoProfile)
    user_preferences: list[SemanticMemoryItem] = field(default_factory=list)
    project_notes: list[SemanticMemoryItem] = field(default_factory=list)
    codebase_concepts: list[SemanticMemoryItem] = field(default_factory=list)
    collaboration_preferences: list[ProceduralMemoryItem] = field(default_factory=list)
    maintenance: MemoryMaintenance = field(default_factory=MemoryMaintenance)
    version: int = MEMORY_VERSION

    @classmethod
    def empty(cls) -> MemoryDocument:
        return cls(updated_at=now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "repo_profile": self.repo_profile.to_dict(),
            "working_memory": {
                "active_topics": [topic.to_dict() for topic in self.active_topics],
                "pending_episode_ids": list(self.pending_episode_ids),
            },
            "episodic_memory": {
                "episodes": [episode.to_dict() for episode in self.episodes],
            },
            "semantic_memory": {
                "user_preferences": [item.to_dict() for item in self.user_preferences],
                "project_notes": [item.to_dict() for item in self.project_notes],
                "codebase_concepts": [item.to_dict() for item in self.codebase_concepts],
            },
            "procedural_memory": {
                "collaboration_preferences": [
                    item.to_dict() for item in self.collaboration_preferences
                ],
            },
            "maintenance": self.maintenance.to_dict(),
        }
