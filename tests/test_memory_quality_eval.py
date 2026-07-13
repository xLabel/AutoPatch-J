from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autopatch_j.core.domain import IntentType
from autopatch_j.core.memory import MemoryNotFoundError
from autopatch_j.core.memory.manager import MemoryManager
from autopatch_j.llm.factory import build_default_llm_client
from autopatch_j.llm.options import LLMCallPurpose


CORPUS_PATH = Path(__file__).parent / "fixtures" / "memory_quality_v2.json"
LIVE_EVAL_ENV = "AUTOPATCH_RUN_MEMORY_LIVE_EVAL"


def _load_cases() -> list[dict[str, Any]]:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert corpus["version"] == 2
    return corpus["cases"]


QUALITY_CASES = _load_cases()


class DeterministicMemoryLLM:
    """为质量 corpus 返回可复现 JSON，同时保留真实 Pipeline 校验。"""

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        self.purposes: list[LLMCallPurpose] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        purpose: LLMCallPurpose = LLMCallPurpose.REACT,
        **_: Any,
    ) -> SimpleNamespace:
        assert tools is None
        payload = json.loads(messages[-1]["content"])
        assert isinstance(payload, dict)
        self.purposes.append(purpose)
        if purpose is LLMCallPurpose.MEMORY_EXTRACTION:
            result = self._extract(payload)
        elif purpose is LLMCallPurpose.MEMORY_CONSOLIDATION:
            result = self._consolidate(payload)
        else:  # pragma: no cover - Pipeline 只能使用两个 Memory purpose
            raise AssertionError(f"unexpected purpose: {purpose}")
        return SimpleNamespace(content=json.dumps(result, ensure_ascii=False))

    def _extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        turns = payload["turns"]
        assert turns
        current = turns[-1]
        user_text = current["user"]
        compaction = f"当前讨论：{user_text}"
        candidates: list[dict[str, Any]] = []

        if self.case_id == "explicit-output-preference":
            candidates.append(
                self._candidate(
                    current,
                    kind="user_preference",
                    title="中文回答与结论优先",
                    content="以后默认用中文回答，结论放在最前面。",
                    aliases=["中文回答", "结论优先"],
                )
            )
        elif self.case_id == "implicit-style-is-not-preference":
            candidates.append(
                self._candidate(
                    current,
                    kind="user_preference",
                    title="简短回答",
                    content="这次简单说一下",
                    aliases=["简短回答"],
                )
            )
        elif self.case_id == "explicit-project-decision":
            candidates.append(
                self._candidate(
                    current,
                    kind="project_decision",
                    title="旧 memory schema 兼容策略",
                    content="旧 memory schema 不兼容，直接删除。",
                    aliases=["旧 schema", "backward compatibility"],
                )
            )
        elif self.case_id == "assistant-proposal-user-confirmation" and user_text.startswith("同意"):
            previous = payload["adjacent_previous_turn"]
            assert previous is not None
            candidates.append(
                {
                    "kind": "project_decision",
                    "title": "reset 与 memory 的管理边界",
                    "content": "reset 只清项目工作台，memory 由 memory 命令单独管理。",
                    "aliases": ["reset 边界", "memory 管理"],
                    "sources": [
                        {
                            "turn_id": previous["turn_id"],
                            "role": "assistant",
                            "quote": previous["assistant"],
                        },
                        {
                            "turn_id": current["turn_id"],
                            "role": "user",
                            "quote": user_text,
                        },
                    ],
                }
            )
        elif self.case_id == "code-fact-is-not-long-term-memory":
            candidates.append(
                self._candidate(
                    current,
                    kind="project_decision",
                    title="Java 17 配置",
                    content="pom.xml 使用 Java 17",
                    aliases=["Java version"],
                )
            )
        elif self.case_id == "assistant-only-claim-is-not-decision":
            candidates.append(
                self._candidate(
                    current,
                    kind="discussion_context",
                    title="是否使用向量库的讨论",
                    content="正在讨论是否使用向量库，尚未形成用户决定。",
                    aliases=["向量库讨论"],
                )
            )
        elif self.case_id == "decision-supersedes-old-decision":
            if user_text.startswith("改一下"):
                candidates.append(
                    self._candidate(
                        current,
                        kind="project_decision",
                        title="Memory v2 无向量检索方案",
                        content="最终不使用向量检索，采用小索引和按需读取。",
                        aliases=["向量检索", "小索引", "按需读取"],
                    )
                )
            else:
                candidates.append(
                    self._candidate(
                        current,
                        kind="project_decision",
                        title="Memory v2 向量检索方案",
                        content="Memory v2 使用向量检索。",
                        aliases=["向量检索"],
                    )
                )
        elif self.case_id == "discussion-does-not-cross-new-thread":
            candidates.append(
                self._candidate(
                    current,
                    kind="discussion_context",
                    title="调度器重试策略讨论",
                    content="正在讨论调度器的重试策略，从 retry backoff 开始。",
                    aliases=["重试策略", "retry backoff"],
                )
            )
        elif self.case_id == "forget-suppresses-derived-memory":
            candidates.append(
                self._candidate(
                    current,
                    kind="user_preference",
                    title="测试执行顺序",
                    content="运行测试时默认先跑聚焦测试，再跑完整回归。",
                    aliases=["测试顺序", "聚焦测试", "完整回归"],
                )
            )
        elif self.case_id == "unrelated-query-has-no-hit":
            candidates.append(
                self._candidate(
                    current,
                    kind="project_decision",
                    title="Memory 使用 SQLite 存储",
                    content="项目使用 SQLite 保存 Memory，不引入向量数据库。",
                    aliases=["SQLite Memory", "向量数据库"],
                )
            )
        elif self.case_id == "english-explicit-preference":
            candidates.append(
                self._candidate(
                    current,
                    kind="user_preference",
                    title="Conclusion-first Chinese answers",
                    content="Going forward, lead with the conclusion and answer in Chinese.",
                    aliases=["conclusion first", "Chinese answers"],
                )
            )
        elif self.case_id == "english-temporary-is-not-preference":
            candidates.append(
                self._candidate(
                    current,
                    kind="user_preference",
                    title="Short answers",
                    content="For this answer, keep it short.",
                    aliases=["short answers"],
                )
            )
        elif self.case_id == "english-explicit-project-decision":
            candidates.append(
                self._candidate(
                    current,
                    kind="project_decision",
                    title="Local SQLite Memory",
                    content="Keep Memory local and adopt SQLite.",
                    aliases=["local Memory", "SQLite storage"],
                )
            )
        elif self.case_id == "english-code-fact-is-not-memory":
            candidates.append(
                self._candidate(
                    current,
                    kind="project_decision",
                    title="Java 21 build configuration",
                    content="build.gradle targets Java 21.",
                    aliases=["Java version"],
                )
            )
        elif self.case_id == "english-undecided-is-not-decision":
            candidates.append(
                self._candidate(
                    current,
                    kind="project_decision",
                    title="Vector database",
                    content="Use a vector database.",
                    aliases=["vector database"],
                )
            )
        elif (
            self.case_id == "english-assistant-proposal-user-confirmation"
            and user_text.startswith("Sounds good")
        ):
            previous = payload["adjacent_previous_turn"]
            assert previous is not None
            candidates.append(
                {
                    "kind": "project_decision",
                    "title": "reset and Memory boundary",
                    "content": "Keep reset separate from Memory management.",
                    "aliases": ["reset boundary", "Memory management"],
                    "sources": [
                        {
                            "turn_id": previous["turn_id"],
                            "role": "assistant",
                            "quote": previous["assistant"],
                        },
                        {
                            "turn_id": current["turn_id"],
                            "role": "user",
                            "quote": user_text,
                        },
                    ],
                }
            )

        return {"thread_compaction": compaction, "candidates": candidates}

    @staticmethod
    def _candidate(
        turn: dict[str, Any],
        *,
        kind: str,
        title: str,
        content: str,
        aliases: list[str],
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "title": title,
            "content": content,
            "aliases": aliases,
            "sources": [
                {
                    "turn_id": turn["turn_id"],
                    "role": "user",
                    "quote": turn["user"],
                }
            ],
        }

    def _consolidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidates = payload["candidates"]
        assert candidates
        active_items = payload["active_items"]
        operations: list[dict[str, Any]] = []
        for candidate in candidates:
            operation = "create"
            target_id = None
            if (
                self.case_id == "decision-supersedes-old-decision"
                and candidate["content"].startswith("最终不使用")
            ):
                assert len(active_items) == 1
                operation = "supersede"
                target_id = active_items[0]["id"]
            operations.append(
                {
                    "operation": operation,
                    "candidate_ids": [candidate["id"]],
                    "target_id": target_id,
                    "title": candidate["title"],
                    "content": candidate["content"],
                    "synopsis": candidate["content"],
                    "aliases": candidate["aliases"],
                    "keywords": candidate["aliases"],
                }
            )
        return {"operations": operations}


def _evaluate_case(case: dict[str, Any], db_path: Path, llm: Any) -> None:
    manager = MemoryManager(db_path=db_path, llm=llm)
    try:
        for turn in case["turns"]:
            handle = manager.begin_turn(
                intent=IntentType.GENERAL_CHAT,
                user_text=turn["user"],
            )
            manager.complete_turn(handle.id, assistant_text=turn["assistant"])
            flush = manager.flush_once(reason="quality-eval")
            assert flush.failed == 0, flush.errors

        items = manager.list_items()
        active_kinds = {item.kind for item in items}
        expected_kinds = set(case.get("expected_kinds", ()))
        assert expected_kinds <= active_kinds, case["id"]
        assert set(case.get("forbidden_kinds", ())).isdisjoint(active_kinds), case["id"]
        if not expected_kinds:
            assert items == [], case["id"]

        details = [manager.read(item.id) for item in items]
        required_roles = set(case.get("required_source_roles", ()))
        if required_roles:
            assert any(
                required_roles <= {source.role for source in detail.sources}
                for detail in details
                if detail.kind in expected_kinds
            ), case["id"]

        expected_content = case.get("expected_active_content")
        if expected_content:
            matching = [detail for detail in details if expected_content in detail.content]
            assert len(matching) == 1, case["id"]
            assert matching[0].revision == 2, case["id"]

        for query in case.get("queries", ()):
            hits = manager.search(query)
            assert hits, f"{case['id']}: query={query!r}"
            assert any(hit.kind in expected_kinds for hit in hits), (
                f"{case['id']}: query={query!r} returned the wrong kind"
            )

        for query in case.get("no_hit_queries", ()):
            assert manager.search(query) == [], (
                f"{case['id']}: unrelated query={query!r} returned a hit"
            )

        forget = case.get("forget")
        if forget is not None:
            matching = [detail for detail in details if detail.kind == forget["kind"]]
            assert len(matching) == 1, case["id"]
            forgotten_id = matching[0].id

            result = manager.forget(forgotten_id)

            assert result.forgotten is True
            assert result.raw_turns_retained is True
            assert all(item.id != forgotten_id for item in manager.list_items())
            assert manager.show_item(forgotten_id).status == "forgotten"
            for query in forget["queries"]:
                assert manager.search(query) == [], (
                    f"{case['id']}: forgotten query={query!r} returned a hit"
                )
            with pytest.raises(MemoryNotFoundError):
                manager.read(forgotten_id)

        if "after_new_expected_hits" in case:
            manager.start_new_thread()
            for query in case.get("queries", ()):
                assert manager.search(query) == case["after_new_expected_hits"]
    finally:
        manager.close()


@pytest.mark.parametrize("case", QUALITY_CASES, ids=lambda case: case["id"])
def test_memory_quality_corpus_through_deterministic_pipeline(
    case: dict[str, Any],
    tmp_path: Path,
) -> None:
    llm = DeterministicMemoryLLM(case["id"])

    _evaluate_case(case, tmp_path / case["id"] / "memory.db", llm)

    assert llm.purposes.count(LLMCallPurpose.MEMORY_EXTRACTION) == len(case["turns"])
    if case["expected_kinds"]:
        assert LLMCallPurpose.MEMORY_CONSOLIDATION in llm.purposes
    else:
        assert LLMCallPurpose.MEMORY_CONSOLIDATION not in llm.purposes


@pytest.mark.skipif(
    os.getenv(LIVE_EVAL_ENV) != "1",
    reason=f"set {LIVE_EVAL_ENV}=1 to run the live Memory quality evaluation",
)
@pytest.mark.parametrize("case", QUALITY_CASES, ids=lambda case: case["id"])
def test_memory_quality_corpus_with_live_model(case: dict[str, Any], tmp_path: Path) -> None:
    llm = build_default_llm_client()
    assert llm is not None, "live Memory eval requires AUTOPATCH_LLM_API_KEY"

    _evaluate_case(case, tmp_path / "live" / case["id"] / "memory.db", llm)
