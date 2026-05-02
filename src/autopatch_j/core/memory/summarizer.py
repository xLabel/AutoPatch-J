from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autopatch_j.llm.client import LLMCallPurpose, LLMClient

from .manager import MemoryManager
from .prompts import MEMORY_SUMMARY_SYSTEM_PROMPT
from .schema import MemorySummaryTrigger

MAX_SUMMARY_PENDING_TURNS = 4
MAX_SUMMARY_EXISTING_ITEMS = 20
MAX_PROJECT_EVIDENCE_ITEMS = 4
MAX_PROJECT_EVIDENCE_TEXT = 700


@dataclass(frozen=True, slots=True)
class MemorySummaryResult:
    delta: dict[str, Any]
    allowed_project_evidence_ids: set[str]


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

    def try_summarize(
        self,
        last_user_text: str = "",
        trigger: MemorySummaryTrigger | None = None,
    ) -> bool:
        result = self.summarize_delta(last_user_text, trigger)
        if result is None:
            return False
        return self.memory_manager.apply_delta(
            result.delta,
            allowed_project_evidence_ids=result.allowed_project_evidence_ids,
        )

    def summarize_delta(
        self,
        last_user_text: str = "",
        trigger: MemorySummaryTrigger | None = None,
    ) -> MemorySummaryResult | None:
        effective_trigger = trigger or self.memory_manager.find_summary_trigger(last_user_text)
        if effective_trigger is None:
            return None

        project_evidence = self._collect_project_evidence()
        payload = self._build_payload(effective_trigger, project_evidence)
        if not payload["pending_turns"]:
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
            delta = self._parse_delta(response.content)
        except Exception:
            return None

        if delta is None:
            return None
        return MemorySummaryResult(
            delta=delta,
            allowed_project_evidence_ids={item["evidence_id"] for item in project_evidence},
        )

    def _build_payload(
        self,
        trigger: MemorySummaryTrigger,
        project_evidence: list[dict[str, str]],
    ) -> dict[str, Any]:
        memory = self.memory_manager.load()
        pending_turns = [
            self._turn_payload(turn)
            for turn in memory["working_memory"]["recent_turns"]
            if turn.get("summary_status") == "pending"
        ][-MAX_SUMMARY_PENDING_TURNS:]

        return {
            "trigger": trigger.name.lower(),
            "pending_turns": pending_turns,
            "active_topics": memory["working_memory"]["active_topics"][-MAX_SUMMARY_EXISTING_ITEMS:],
            "durable_preferences": memory["long_term_memory"]["durable_preferences"][
                -MAX_SUMMARY_EXISTING_ITEMS:
            ],
            "project_facts": memory["long_term_memory"]["project_facts"][-MAX_SUMMARY_EXISTING_ITEMS:],
            "project_evidence": project_evidence,
        }

    def _turn_payload(self, turn: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": turn.get("id", ""),
            "intent": turn.get("intent", ""),
            "user_text": turn.get("user_text", ""),
            "assistant_text": turn.get("assistant_text", ""),
            "scope_paths": turn.get("scope_paths", []),
            "created_at": turn.get("created_at", ""),
        }

    def _parse_delta(self, content: str) -> dict[str, Any] | None:
        text = content.strip()
        if not text:
            return None
        if text.startswith("```"):
            text = self._strip_fenced_json(text)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and start < end:
                text = text[start : end + 1]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _collect_project_evidence(self) -> list[dict[str, str]]:
        if self.repo_root is None:
            return []

        candidates = ("README_CN.md", "README.md", "pom.xml", "build.gradle", "settings.gradle")
        evidence: list[dict[str, str]] = []
        for index, name in enumerate(candidates, start=1):
            path = self.repo_root / name
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            compact = " ".join(content.split())
            if not compact:
                continue
            evidence.append(
                {
                    "evidence_id": f"project_evidence_{index}",
                    "source": name,
                    "text": compact[:MAX_PROJECT_EVIDENCE_TEXT],
                }
            )
            if len(evidence) >= MAX_PROJECT_EVIDENCE_ITEMS:
                break
        return evidence

    def _strip_fenced_json(self, text: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
