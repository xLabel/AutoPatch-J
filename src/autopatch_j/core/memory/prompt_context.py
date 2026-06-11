from __future__ import annotations

from typing import Any

from autopatch_j.core.domain import IntentType

from .constants import (
    MAX_PROMPT_PENDING_TURNS,
    MAX_PROMPT_READY_SUMMARIES,
    ORDINARY_INTENTS,
)
from .models import MemoryDocument
from .signals import LONG_TERM_SIGNALS, PROJECT_SIGNALS


class MemoryPromptContextBuilder:
    """从已保存的 memory 中挑选少量相关内容注入普通问答提示词。"""

    def build(self, memory: MemoryDocument | dict[str, Any], intent: IntentType, current_user_text: str = "") -> str:
        if intent not in ORDINARY_INTENTS:
            return ""
        memory = self._as_dict(memory)

        sections: list[str] = []
        durable_preferences = self._select_relevant_items(
            memory["long_term_memory"]["durable_preferences"],
            current_user_text,
            limit=5,
            always_include=True,
        )
        if durable_preferences:
            sections.append(self._format_items("长期偏好", durable_preferences))

        project_context_allowed = intent is IntentType.CODE_EXPLAIN or self._looks_project_related(current_user_text)
        if project_context_allowed:
            repo_profile = self._format_repo_profile(memory.get("repo_profile"))
            if repo_profile:
                sections.append(repo_profile)

            project_notes = self._select_relevant_items(
                memory["long_term_memory"]["project_notes"],
                current_user_text,
                limit=5,
                always_include=intent is IntentType.CODE_EXPLAIN,
            )
            if project_notes:
                sections.append(self._format_items("项目讨论笔记", project_notes))

        active_topics = self._select_relevant_items(
            memory["working_memory"]["active_topics"],
            current_user_text,
            limit=3,
        )
        if active_topics:
            sections.append(self._format_items("近期话题", active_topics))

        ready_summaries = [
            turn
            for turn in reversed(memory["working_memory"]["recent_turns"])
            if turn.get("summary_status") == "ready" and turn.get("summary")
        ][:MAX_PROMPT_READY_SUMMARIES]
        if ready_summaries:
            sections.append(self._format_recent_summaries(ready_summaries))

        pending_turns = [
            turn
            for turn in reversed(memory["working_memory"]["recent_turns"])
            if turn.get("summary_status") == "pending" and turn.get("user_text")
        ][:MAX_PROMPT_PENDING_TURNS]
        if pending_turns:
            sections.append(self._format_pending_turns(pending_turns))

        return "\n\n".join(sections)

    def build_debug_summary(
        self,
        memory: MemoryDocument | dict[str, Any],
        intent: IntentType,
        current_user_text: str = "",
    ) -> str:
        if intent not in ORDINARY_INTENTS:
            return ""
        memory = self._as_dict(memory)

        lines = ["Memory 注入："]
        durable_preferences = self._select_relevant_items(
            memory["long_term_memory"]["durable_preferences"],
            current_user_text,
            limit=5,
            always_include=True,
        )
        self._append_debug_items(lines, "durable_preferences", durable_preferences)

        project_context_allowed = intent is IntentType.CODE_EXPLAIN or self._looks_project_related(current_user_text)
        if project_context_allowed:
            repo_profile_fields = self._repo_profile_debug_fields(memory.get("repo_profile"))
            if repo_profile_fields:
                lines.append(f"- repo_profile: {', '.join(repo_profile_fields)}")

            project_notes = self._select_relevant_items(
                memory["long_term_memory"]["project_notes"],
                current_user_text,
                limit=5,
                always_include=intent is IntentType.CODE_EXPLAIN,
            )
            self._append_debug_items(lines, "project_notes", project_notes)

        active_topics = self._select_relevant_items(
            memory["working_memory"]["active_topics"],
            current_user_text,
            limit=3,
        )
        self._append_debug_items(lines, "active_topics", active_topics)

        ready_count = sum(
            1
            for turn in memory["working_memory"]["recent_turns"]
            if turn.get("summary_status") == "ready" and turn.get("summary")
        )
        pending_count = sum(
            1
            for turn in memory["working_memory"]["recent_turns"]
            if turn.get("summary_status") == "pending" and turn.get("user_text")
        )
        if ready_count:
            lines.append(f"- recent_summaries: {min(ready_count, MAX_PROMPT_READY_SUMMARIES)} 条")
        if pending_count:
            lines.append(f"- pending_turns: {min(pending_count, MAX_PROMPT_PENDING_TURNS)} 条")

        if len(lines) == 1:
            lines.append("- 无匹配内容")
        return "\n".join(lines)

    def _as_dict(self, memory: MemoryDocument | dict[str, Any]) -> dict[str, Any]:
        if isinstance(memory, MemoryDocument):
            return memory.to_dict()
        return memory

    def _select_relevant_items(
        self,
        items: list[dict[str, Any]],
        current_user_text: str,
        limit: int,
        always_include: bool = False,
    ) -> list[dict[str, Any]]:
        active_items = [item for item in items if item.get("status", "active") == "active"]
        scored = [(self._relevance_score(item, current_user_text), item) for item in active_items]
        if not always_include:
            scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(
            key=lambda pair: (
                pair[0],
                pair[1].get("updated_at") or pair[1].get("last_touched_at") or "",
            ),
            reverse=True,
        )
        return [item for _, item in scored[:limit]]

    def _relevance_score(self, item: dict[str, Any], text: str) -> int:
        haystack = f"{item.get('label', '')} {item.get('summary', '')}".lower()
        needle = text.lower()
        if not needle:
            return 0
        score = 0
        for token in self._rough_tokens(needle):
            if len(token) >= 2 and token in haystack:
                score += 1
        if any(signal in needle for signal in LONG_TERM_SIGNALS):
            score += 1
        return score

    def _rough_tokens(self, text: str) -> list[str]:
        ascii_tokens = [part for part in "".join(ch if ch.isalnum() else " " for ch in text).split() if part]
        chinese_windows = [
            text[index : index + 2]
            for index in range(max(0, len(text) - 1))
            if "\u4e00" <= text[index] <= "\u9fff"
        ]
        return ascii_tokens + chinese_windows

    def _format_items(self, title: str, items: list[dict[str, Any]]) -> str:
        lines = [f"{title}："]
        for item in items:
            lines.append(f"- {item.get('label', '')}: {item.get('summary', '')}")
        return "\n".join(lines)

    def _format_repo_profile(self, profile: Any) -> str:
        if not isinstance(profile, dict):
            return ""
        fields = [
            ("构建工具", profile.get("build_tool", "")),
            ("Java 版本", profile.get("java_version", "")),
            ("项目名", profile.get("project_name", "")),
            ("模块", ", ".join(profile.get("modules", []) if isinstance(profile.get("modules"), list) else [])),
            (
                "明确依赖特征",
                ", ".join(profile.get("frameworks", []) if isinstance(profile.get("frameworks"), list) else []),
            ),
        ]
        lines = [f"- {label}: {value}" for label, value in fields if value]
        if not lines:
            return ""
        return "仓库元信息：\n" + "\n".join(lines)

    def _repo_profile_debug_fields(self, profile: Any) -> list[str]:
        if not isinstance(profile, dict):
            return []
        fields = []
        for key in ("build_tool", "java_version", "project_name"):
            if profile.get(key):
                fields.append(key)
        for key in ("modules", "frameworks"):
            value = profile.get(key)
            if isinstance(value, list) and value:
                fields.append(key)
        return fields

    def _append_debug_items(self, lines: list[str], label: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        item_labels = [str(item.get("label", "")).strip() or str(item.get("id", "")).strip() for item in items]
        lines.append(f"- {label}: {', '.join(item_labels)}")

    def _format_recent_summaries(self, turns: list[dict[str, Any]]) -> str:
        lines = ["近期问答摘要："]
        for turn in turns:
            lines.append(f"- {turn['intent']}: {turn['summary']}")
        return "\n".join(lines)

    def _format_pending_turns(self, turns: list[dict[str, Any]]) -> str:
        lines = ["尚未摘要的近期用户问题（只作为上下文线索）："]
        for turn in turns:
            lines.append(f"- {turn['intent']}: {turn['user_text']}")
        return "\n".join(lines)

    def _looks_project_related(self, text: str) -> bool:
        lowered = text.lower()
        return any(signal in lowered for signal in PROJECT_SIGNALS)
