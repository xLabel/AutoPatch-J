from __future__ import annotations

from typing import Any

from autopatch_j.core.models import IntentType

from .schema import (
    LONG_TERM_SIGNALS,
    MAX_PROMPT_PENDING_TURNS,
    MAX_PROMPT_READY_SUMMARIES,
    ORDINARY_INTENTS,
    PROJECT_SIGNALS,
)


class MemoryPromptContextBuilder:
    """从已保存的 memory 中挑选少量相关内容注入普通问答提示词。"""

    def build(self, memory: dict[str, Any], intent: IntentType, current_user_text: str = "") -> str:
        if intent not in ORDINARY_INTENTS:
            return ""

        sections: list[str] = []
        durable_preferences = self._select_relevant_items(
            memory["long_term_memory"]["durable_preferences"],
            current_user_text,
            limit=5,
            always_include=True,
        )
        if durable_preferences:
            sections.append(self._format_items("长期偏好", durable_preferences))

        if intent is IntentType.CODE_EXPLAIN or self._looks_project_related(current_user_text):
            project_facts = self._select_relevant_items(
                memory["long_term_memory"]["project_facts"],
                current_user_text,
                limit=5,
                always_include=intent is IntentType.CODE_EXPLAIN,
            )
            if project_facts:
                sections.append(self._format_items("项目事实", project_facts))

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
