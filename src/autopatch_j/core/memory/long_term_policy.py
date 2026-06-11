from __future__ import annotations


class LongTermMemoryPolicy:
    """程序侧长期记忆准入策略，避免短 LLM 无来源地写入长期状态。"""

    semantic_types = {"user_preference", "project_note", "codebase_concept"}
    procedural_types = {"collaboration_preference"}
    confidence_values = {"low", "medium", "high"}

    def allows_semantic_item(
        self,
        item_type: str,
        label: str,
        summary: str,
        source_episode_ids: list[str],
    ) -> bool:
        return bool(item_type in self.semantic_types and label and summary and source_episode_ids)

    def allows_procedural_item(
        self,
        item_type: str,
        label: str,
        summary: str,
        source_episode_ids: list[str],
    ) -> bool:
        return bool(item_type in self.procedural_types and label and summary and source_episode_ids)

    def normalize_confidence(self, value: str) -> str:
        cleaned = value.strip().lower()
        return cleaned if cleaned in self.confidence_values else "medium"
