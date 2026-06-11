from __future__ import annotations

from typing import Any

from .constants import (
    MAX_ACTIVE_TOPICS,
    MAX_ASSISTANT_TEXT,
    MAX_EPISODES,
    MAX_LABEL,
    MAX_PENDING_EPISODE_IDS,
    MAX_PROCEDURAL_ITEMS,
    MAX_REPO_PROFILE_ITEMS,
    MAX_REPO_PROFILE_TEXT,
    MAX_SCOPE_PATHS,
    MAX_SEMANTIC_ITEMS,
    MAX_SUMMARY,
    MAX_USER_TEXT,
    MEMORY_VERSION,
    ORDINARY_INTENTS,
)
from .models import (
    MemoryDocument,
    MemoryEpisode,
    MemoryMaintenance,
    MemoryTopic,
    ProceduralMemoryItem,
    RepoProfile,
    SemanticMemoryItem,
)
from .text_utils import (
    clip_text,
    generate_id,
    non_empty,
    normalize_scope_paths,
    normalize_string_list,
    now_iso,
)


SEMANTIC_TYPES = {"user_preference", "project_note", "codebase_concept"}
PROCEDURAL_TYPES = {"collaboration_preference"}
CONFIDENCE_VALUES = {"low", "medium", "high"}


class MemoryNormalizer:
    """Builds a safe memory document for the current memory JSON schema only."""

    def normalize(self, raw: Any) -> dict[str, Any]:
        return self.normalize_document(raw).to_dict()

    def normalize_document(self, raw: Any) -> MemoryDocument:
        if not isinstance(raw, dict) or raw.get("version") != MEMORY_VERSION:
            return MemoryDocument.empty()

        working = raw.get("working_memory") if isinstance(raw.get("working_memory"), dict) else {}
        episodic = raw.get("episodic_memory") if isinstance(raw.get("episodic_memory"), dict) else {}
        semantic = raw.get("semantic_memory") if isinstance(raw.get("semantic_memory"), dict) else {}
        procedural = (
            raw.get("procedural_memory") if isinstance(raw.get("procedural_memory"), dict) else {}
        )

        episodes = self._normalize_episodes(episodic.get("episodes"))
        episode_ids = {episode.id for episode in episodes}
        pending_episode_ids = self._normalize_pending_episode_ids(
            working.get("pending_episode_ids"),
            episodes,
            episode_ids,
        )

        return MemoryDocument(
            updated_at=str(raw.get("updated_at") or now_iso()),
            active_topics=self._normalize_topics(working.get("active_topics"), episode_ids),
            pending_episode_ids=pending_episode_ids,
            episodes=episodes,
            repo_profile=self._normalize_repo_profile(raw.get("repo_profile")),
            user_preferences=self._normalize_semantic_items(
                semantic.get("user_preferences"),
                "user_preference",
                episode_ids,
            ),
            project_notes=self._normalize_semantic_items(
                semantic.get("project_notes"),
                "project_note",
                episode_ids,
            ),
            codebase_concepts=self._normalize_semantic_items(
                semantic.get("codebase_concepts"),
                "codebase_concept",
                episode_ids,
            ),
            collaboration_preferences=self._normalize_procedural_items(
                procedural.get("collaboration_preferences"),
                "collaboration_preference",
                episode_ids,
            ),
            maintenance=self._normalize_maintenance(raw.get("maintenance")),
        )

    def empty(self) -> dict[str, Any]:
        return MemoryDocument.empty().to_dict()

    def _normalize_episodes(self, raw_episodes: Any) -> list[MemoryEpisode]:
        if not isinstance(raw_episodes, list):
            return []
        episodes: list[MemoryEpisode] = []
        allowed_intents = {intent.value for intent in ORDINARY_INTENTS}
        for raw in raw_episodes:
            if not isinstance(raw, dict) or raw.get("intent") not in allowed_intents:
                continue
            episode_id = non_empty(raw.get("id"), generate_id("episode"))
            created_at = non_empty(raw.get("created_at"), now_iso())
            episodes.append(
                MemoryEpisode(
                    id=episode_id,
                    intent=raw["intent"],
                    user_goal=clip_text(raw.get("user_goal", ""), MAX_USER_TEXT),
                    assistant_result=clip_text(raw.get("assistant_result", ""), MAX_ASSISTANT_TEXT),
                    summary=clip_text(raw.get("summary", ""), MAX_SUMMARY),
                    summary_status="ready" if raw.get("summary_status") == "ready" else "pending",
                    scope_paths=normalize_scope_paths(raw.get("scope_paths"))[:MAX_SCOPE_PATHS],
                    importance=self._normalize_importance(raw.get("importance")),
                    created_at=created_at,
                    last_accessed_at=non_empty(raw.get("last_accessed_at"), created_at),
                    access_count=self._normalize_count(raw.get("access_count")),
                )
            )
        return episodes[-MAX_EPISODES:]

    def _normalize_pending_episode_ids(
        self,
        raw_ids: Any,
        episodes: list[MemoryEpisode],
        episode_ids: set[str],
    ) -> list[str]:
        if isinstance(raw_ids, list):
            ids = [
                str(item).strip()
                for item in raw_ids
                if str(item).strip() in episode_ids
            ]
        else:
            ids = [episode.id for episode in episodes if episode.summary_status == "pending"]
        return list(dict.fromkeys(ids))[-MAX_PENDING_EPISODE_IDS:]

    def _normalize_topics(self, raw_topics: Any, episode_ids: set[str]) -> list[MemoryTopic]:
        if not isinstance(raw_topics, list):
            return []
        topics: list[MemoryTopic] = []
        for raw in raw_topics:
            if not isinstance(raw, dict):
                continue
            label = clip_text(raw.get("label", ""), MAX_LABEL)
            summary = clip_text(raw.get("summary", ""), MAX_SUMMARY)
            if not label or not summary:
                continue
            topics.append(
                MemoryTopic(
                    id=non_empty(raw.get("id"), generate_id("topic")),
                    label=label,
                    summary=summary,
                    related_episode_ids=self._normalize_source_episode_ids(
                        raw.get("related_episode_ids"),
                        episode_ids,
                    ),
                    last_touched_at=non_empty(raw.get("last_touched_at"), now_iso()),
                )
            )
        return sorted(topics, key=lambda item: item.last_touched_at)[-MAX_ACTIVE_TOPICS:]

    def _normalize_semantic_items(
        self,
        raw_items: Any,
        item_type: str,
        episode_ids: set[str],
    ) -> list[SemanticMemoryItem]:
        if not isinstance(raw_items, list):
            return []
        items: list[SemanticMemoryItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = self._normalize_semantic_item(raw, item_type, episode_ids)
            if item:
                items.append(item)
        return sorted(items, key=lambda item: (item.status == "active", item.updated_at))[
            -MAX_SEMANTIC_ITEMS:
        ]

    def _normalize_semantic_item(
        self,
        raw: dict[str, Any],
        item_type: str,
        episode_ids: set[str],
    ) -> SemanticMemoryItem | None:
        label = clip_text(raw.get("label", ""), MAX_LABEL)
        summary = clip_text(raw.get("summary", ""), MAX_SUMMARY)
        source_episode_ids = self._normalize_source_episode_ids(raw.get("source_episode_ids"), episode_ids)
        if not label or not summary or not source_episode_ids:
            return None
        now = now_iso()
        return SemanticMemoryItem(
            id=non_empty(raw.get("id"), generate_id("mem")),
            type=item_type if item_type in SEMANTIC_TYPES else "project_note",
            label=label,
            summary=summary,
            source_episode_ids=source_episode_ids,
            confidence=self._normalize_confidence(raw.get("confidence")),
            status="inactive" if raw.get("status") == "inactive" else "active",
            created_at=non_empty(raw.get("created_at"), now),
            updated_at=non_empty(raw.get("updated_at"), now),
        )

    def _normalize_procedural_items(
        self,
        raw_items: Any,
        item_type: str,
        episode_ids: set[str],
    ) -> list[ProceduralMemoryItem]:
        if not isinstance(raw_items, list):
            return []
        items: list[ProceduralMemoryItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = self._normalize_procedural_item(raw, item_type, episode_ids)
            if item:
                items.append(item)
        return sorted(items, key=lambda item: (item.status == "active", item.updated_at))[
            -MAX_PROCEDURAL_ITEMS:
        ]

    def _normalize_procedural_item(
        self,
        raw: dict[str, Any],
        item_type: str,
        episode_ids: set[str],
    ) -> ProceduralMemoryItem | None:
        label = clip_text(raw.get("label", ""), MAX_LABEL)
        summary = clip_text(raw.get("summary", ""), MAX_SUMMARY)
        source_episode_ids = self._normalize_source_episode_ids(raw.get("source_episode_ids"), episode_ids)
        if not label or not summary or not source_episode_ids:
            return None
        now = now_iso()
        return ProceduralMemoryItem(
            id=non_empty(raw.get("id"), generate_id("mem")),
            type=item_type if item_type in PROCEDURAL_TYPES else "collaboration_preference",
            label=label,
            summary=summary,
            source_episode_ids=source_episode_ids,
            confidence=self._normalize_confidence(raw.get("confidence")),
            status="inactive" if raw.get("status") == "inactive" else "active",
            created_at=non_empty(raw.get("created_at"), now),
            updated_at=non_empty(raw.get("updated_at"), now),
        )

    def _normalize_repo_profile(self, raw_profile: Any) -> RepoProfile:
        if not isinstance(raw_profile, dict):
            return RepoProfile()
        return RepoProfile(
            build_tool=clip_text(raw_profile.get("build_tool", ""), MAX_REPO_PROFILE_TEXT),
            java_version=clip_text(raw_profile.get("java_version", ""), MAX_REPO_PROFILE_TEXT),
            project_name=clip_text(raw_profile.get("project_name", ""), MAX_REPO_PROFILE_TEXT),
            modules=normalize_string_list(
                raw_profile.get("modules"),
                MAX_REPO_PROFILE_ITEMS,
                MAX_REPO_PROFILE_TEXT,
            ),
            frameworks=normalize_string_list(
                raw_profile.get("frameworks"),
                MAX_REPO_PROFILE_ITEMS,
                MAX_REPO_PROFILE_TEXT,
            ),
            source_files=normalize_string_list(
                raw_profile.get("source_files"),
                MAX_REPO_PROFILE_ITEMS,
                MAX_REPO_PROFILE_TEXT,
            ),
            updated_at=non_empty(raw_profile.get("updated_at"), ""),
        )

    def _normalize_maintenance(self, raw: Any) -> MemoryMaintenance:
        if not isinstance(raw, dict):
            return MemoryMaintenance()
        return MemoryMaintenance(
            last_consolidated_at=non_empty(raw.get("last_consolidated_at"), ""),
            last_compacted_at=non_empty(raw.get("last_compacted_at"), ""),
        )

    def _normalize_source_episode_ids(self, raw_ids: Any, episode_ids: set[str]) -> list[str]:
        return [
            value
            for value in normalize_string_list(raw_ids, 20, 120)
            if value in episode_ids
        ]

    def _normalize_confidence(self, raw: Any) -> str:
        value = str(raw or "").strip().lower()
        return value if value in CONFIDENCE_VALUES else "medium"

    def _normalize_importance(self, raw: Any) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 3
        return min(5, max(1, value))

    def _normalize_count(self, raw: Any) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 0
        return max(0, value)
