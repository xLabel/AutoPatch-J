from __future__ import annotations

import re
from typing import Any

from autopatch_j.core.domain import IntentType

from .constants import (
    MAX_PROMPT_ACTIVE_TOPICS,
    MAX_PROMPT_PENDING_INPUTS,
    MAX_PROMPT_PROCEDURAL_ITEMS,
    MAX_PROMPT_RELATED_EPISODES,
    MAX_PROMPT_SEMANTIC_ITEMS,
    ORDINARY_INTENTS,
)
from .models import MemoryDocument


class MemoryPromptContextBuilder:
    """从 memory 中挑选少量相关内容，并渲染成主 LLM 更易阅读的结构化上下文正文。"""

    def build(self, memory: MemoryDocument | dict[str, Any], intent: IntentType, current_user_text: str = "") -> str:
        if intent not in ORDINARY_INTENTS:
            return ""
        memory = self._as_dict(memory)

        sections: list[str] = []
        procedural_items = self._select_relevant_items(
            memory["procedural_memory"]["collaboration_preferences"],
            current_user_text,
            limit=MAX_PROMPT_PROCEDURAL_ITEMS,
            always_include=True,
        )
        if procedural_items:
            sections.append(self._format_items("用户协作偏好", procedural_items))

        project_context_allowed = intent is IntentType.CODE_EXPLAIN or self._looks_project_related(current_user_text)
        if project_context_allowed:
            repo_profile = self._format_repo_profile(memory.get("repo_profile"))
            if repo_profile:
                sections.append(repo_profile)

            semantic_items = [
                *memory["semantic_memory"]["user_preferences"],
                *memory["semantic_memory"]["project_notes"],
                *memory["semantic_memory"]["codebase_concepts"],
            ]
            selected_semantic = self._select_relevant_items(
                semantic_items,
                current_user_text,
                limit=MAX_PROMPT_SEMANTIC_ITEMS,
                always_include=intent is IntentType.CODE_EXPLAIN,
            )
            if selected_semantic:
                sections.append(self._format_items("相关项目理解", selected_semantic))

        selected_episodes = self._select_relevant_episodes(
            memory["episodic_memory"]["episodes"],
            current_user_text,
            limit=MAX_PROMPT_RELATED_EPISODES,
        )
        if selected_episodes:
            sections.append(self._format_episodes(selected_episodes))

        active_topics = self._select_relevant_items(
            memory["working_memory"]["active_topics"],
            current_user_text,
            limit=MAX_PROMPT_ACTIVE_TOPICS,
        )
        if active_topics:
            sections.append(self._format_items("近期话题", active_topics))

        pending_inputs = self._pending_inputs(memory, limit=MAX_PROMPT_PENDING_INPUTS)
        if pending_inputs:
            sections.append(self._format_pending_inputs(pending_inputs))

        if not sections:
            return ""
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

        procedural_items = self._select_relevant_items(
            memory["procedural_memory"]["collaboration_preferences"],
            current_user_text,
            limit=MAX_PROMPT_PROCEDURAL_ITEMS,
            always_include=True,
        )
        self._append_debug_items(lines, "procedural_memory", procedural_items)

        project_context_allowed = intent is IntentType.CODE_EXPLAIN or self._looks_project_related(current_user_text)
        if project_context_allowed:
            repo_profile_fields = self._repo_profile_debug_fields(memory.get("repo_profile"))
            if repo_profile_fields:
                lines.append(f"- repo_profile: {', '.join(repo_profile_fields)}")

            semantic_items = [
                *memory["semantic_memory"]["user_preferences"],
                *memory["semantic_memory"]["project_notes"],
                *memory["semantic_memory"]["codebase_concepts"],
            ]
            selected_semantic = self._select_relevant_items(
                semantic_items,
                current_user_text,
                limit=MAX_PROMPT_SEMANTIC_ITEMS,
                always_include=intent is IntentType.CODE_EXPLAIN,
            )
            self._append_debug_items(lines, "semantic_memory", selected_semantic)

        selected_episodes = self._select_relevant_episodes(
            memory["episodic_memory"]["episodes"],
            current_user_text,
            limit=MAX_PROMPT_RELATED_EPISODES,
        )
        if selected_episodes:
            lines.append(f"- episodes: {len(selected_episodes)} 条")

        active_topics = self._select_relevant_items(
            memory["working_memory"]["active_topics"],
            current_user_text,
            limit=MAX_PROMPT_ACTIVE_TOPICS,
        )
        self._append_debug_items(lines, "active_topics", active_topics)

        pending_inputs = self._pending_inputs(memory, limit=MAX_PROMPT_PENDING_INPUTS)
        if pending_inputs:
            lines.append(f"- pending_inputs: {len(pending_inputs)} 条")

        return "\n".join(lines) if len(lines) > 1 else ""

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
        scored = [
            (self._relevance_score(item, current_user_text), item)
            for item in active_items
        ]
        selected = [
            item
            for score, item in sorted(scored, key=lambda pair: pair[0], reverse=True)
            if always_include or score > 0
        ]
        return selected[:limit]

    def _select_relevant_episodes(
        self,
        episodes: list[dict[str, Any]],
        current_user_text: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        ready = [
            episode
            for episode in episodes
            if episode.get("summary_status") == "ready" and episode.get("summary")
        ]
        scored = [
            (self._episode_score(episode, current_user_text), episode)
            for episode in ready
        ]
        return [
            episode
            for score, episode in sorted(scored, key=lambda pair: pair[0], reverse=True)
            if score > 0
        ][:limit]

    def _relevance_score(self, item: dict[str, Any], current_user_text: str) -> int:
        haystack = f"{item.get('label', '')} {item.get('summary', '')}".lower()
        score = self._lexical_score(haystack, current_user_text)
        score += self._confidence_score(item.get("confidence", ""))
        if item.get("status") == "active":
            score += 1
        return score

    def _episode_score(self, episode: dict[str, Any], current_user_text: str) -> int:
        haystack = (
            f"{episode.get('user_goal', '')} {episode.get('assistant_result', '')} "
            f"{episode.get('summary', '')} {' '.join(episode.get('scope_paths', []))}"
        ).lower()
        score = self._lexical_score(haystack, current_user_text)
        score += int(episode.get("importance") or 0)
        score += min(3, int(episode.get("access_count") or 0))
        return score

    def _lexical_score(self, haystack: str, current_user_text: str) -> int:
        terms = [
            term
            for term in re.split(r"\W+", current_user_text.lower())
            if len(term) >= 2
        ]
        if not terms:
            return 0
        return sum(2 for term in set(terms) if term in haystack)

    def _confidence_score(self, confidence: str) -> int:
        return {"high": 3, "medium": 2, "low": 1}.get(str(confidence), 2)

    def _looks_project_related(self, text: str) -> bool:
        lowered = text.lower()
        signals = (
            "项目",
            "仓库",
            "代码",
            "模块",
            "目录",
            "文件",
            "类",
            "方法",
            "workflow",
            "agent",
            "memory",
            "function",
            "patch",
        )
        return any(signal in lowered for signal in signals)

    def _format_items(self, title: str, items: list[dict[str, Any]]) -> str:
        lines = [f"### {title}"]
        for item in items:
            label = item.get("label", "")
            summary = item.get("summary", "")
            lines.append(f"- {label}: {summary}" if label else f"- {summary}")
        return "\n".join(lines)

    def _format_episodes(self, episodes: list[dict[str, Any]]) -> str:
        lines = ["### 相关经历摘要"]
        for episode in episodes:
            scope = ", ".join(episode.get("scope_paths", []))
            suffix = f"（scope: {scope}）" if scope else ""
            lines.append(f"- {episode.get('summary', '')}{suffix}")
        return "\n".join(lines)

    def _format_pending_inputs(self, episodes: list[dict[str, Any]]) -> str:
        lines = ["### 待摘要用户输入"]
        for episode in episodes:
            lines.append(f"- {episode.get('intent', '')}: {episode.get('user_goal', '')}")
        return "\n".join(lines)

    def _format_repo_profile(self, profile: Any) -> str:
        if not isinstance(profile, dict):
            return ""
        lines = ["### 当前项目画像"]
        fields = (
            ("build tool", profile.get("build_tool")),
            ("java version", profile.get("java_version")),
            ("project name", profile.get("project_name")),
        )
        for label, value in fields:
            if value:
                lines.append(f"- {label}: {value}")
        if profile.get("modules"):
            lines.append(f"- modules: {', '.join(profile['modules'])}")
        if profile.get("frameworks"):
            lines.append(f"- frameworks: {', '.join(profile['frameworks'])}")
        if profile.get("source_files"):
            lines.append(f"- sources: {', '.join(profile['source_files'])}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def _repo_profile_debug_fields(self, profile: Any) -> list[str]:
        if not isinstance(profile, dict):
            return []
        fields = []
        for key in ("build_tool", "java_version", "project_name"):
            if profile.get(key):
                fields.append(key)
        for key in ("modules", "frameworks", "source_files"):
            if profile.get(key):
                fields.append(key)
        return fields

    def _pending_inputs(self, memory: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        pending_ids = set(memory["working_memory"]["pending_episode_ids"])
        return [
            episode
            for episode in reversed(memory["episodic_memory"]["episodes"])
            if episode.get("id") in pending_ids and episode.get("user_goal")
        ][:limit]

    def _append_debug_items(self, lines: list[str], label: str, items: list[dict[str, Any]]) -> None:
        if items:
            names = ", ".join(item.get("label", "") for item in items if item.get("label"))
            lines.append(f"- {label}: {names}" if names else f"- {label}: {len(items)} 条")
