from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autopatch_j.llm.client import LLMClient
from autopatch_j.llm.options import LLMCallPurpose

from .delta_parser import MemoryDeltaParser
from .manager import MemoryManager
from .prompts import MEMORY_SUMMARY_SYSTEM_PROMPT
from .repo_profile import RepoProfileCollector
from .triggers import MemorySummaryTrigger

MAX_SUMMARY_PENDING_EPISODES = 4
MAX_SUMMARY_EXISTING_ITEMS = 20


@dataclass(frozen=True, slots=True)
class MemorySummaryResult:
    delta: dict[str, Any]
    repo_profile: dict[str, Any] | None


class MemorySummarizer:
    """用短 LLM 生成普通问答记忆 delta，再交给 manager 做硬校验写回。"""

    def __init__(
        self,
        memory_manager: MemoryManager,
        llm: LLMClient,
        repo_root: Path | None = None,
    ) -> None:
        self.memory_manager = memory_manager
        self.llm = llm
        self.repo_root = repo_root
        self.repo_profile_collector = RepoProfileCollector(repo_root)
        self.delta_parser = MemoryDeltaParser()

    def try_summarize(
        self,
        last_user_text: str = "",
        trigger: MemorySummaryTrigger | None = None,
    ) -> bool:
        result = self.summarize_delta(last_user_text, trigger)
        if result is None:
            return False
        return self.memory_manager.apply_delta(result.delta, repo_profile=result.repo_profile)

    def summarize_delta(
        self,
        last_user_text: str = "",
        trigger: MemorySummaryTrigger | None = None,
    ) -> MemorySummaryResult | None:
        effective_trigger = trigger or self.memory_manager.find_summary_trigger(last_user_text)
        if effective_trigger is None:
            return None

        repo_profile = self.repo_profile_collector.collect()
        payload = self._build_payload(effective_trigger, repo_profile)
        if not payload["pending_episodes"]:
            return None

        try:
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": MEMORY_SUMMARY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False, indent=2),
                    },
                ],
                tools=None,
                purpose=LLMCallPurpose.MEMORY_SUMMARY,
            )
            delta = self.delta_parser.parse(response.content)
        except Exception:
            return None

        if delta is None:
            return None
        return MemorySummaryResult(
            delta=delta,
            repo_profile=repo_profile if repo_profile.get("source_files") else None,
        )

    def _build_payload(
        self,
        trigger: MemorySummaryTrigger,
        repo_profile: dict[str, Any],
    ) -> dict[str, Any]:
        memory = self.memory_manager.load()
        pending_ids = set(memory["working_memory"]["pending_episode_ids"])
        pending_episodes = [
            self._episode_payload(episode)
            for episode in memory["episodic_memory"]["episodes"]
            if episode.get("id") in pending_ids
        ][-MAX_SUMMARY_PENDING_EPISODES:]

        return {
            "trigger": trigger.name.lower(),
            "pending_episodes": pending_episodes,
            "active_topics": memory["working_memory"]["active_topics"][-MAX_SUMMARY_EXISTING_ITEMS:],
            "semantic_memory": {
                "user_preferences": memory["semantic_memory"]["user_preferences"][
                    -MAX_SUMMARY_EXISTING_ITEMS:
                ],
                "project_notes": memory["semantic_memory"]["project_notes"][-MAX_SUMMARY_EXISTING_ITEMS:],
                "codebase_concepts": memory["semantic_memory"]["codebase_concepts"][
                    -MAX_SUMMARY_EXISTING_ITEMS:
                ],
            },
            "procedural_memory": {
                "collaboration_preferences": memory["procedural_memory"]["collaboration_preferences"][
                    -MAX_SUMMARY_EXISTING_ITEMS:
                ],
            },
            "repo_profile": repo_profile,
        }

    def _episode_payload(self, episode: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": episode.get("id", ""),
            "intent": episode.get("intent", ""),
            "user_goal": episode.get("user_goal", ""),
            "assistant_result": episode.get("assistant_result", ""),
            "scope_paths": episode.get("scope_paths", []),
            "created_at": episode.get("created_at", ""),
        }
