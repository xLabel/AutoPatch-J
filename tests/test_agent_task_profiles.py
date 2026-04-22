from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.agent.llm_client import LLMResponse
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.patch_engine import PatchEngine


def _build_agent(tmp_path: Path, mock_llm: MagicMock) -> AutoPatchAgent:
    repo_root = tmp_path
    (repo_root / "src" / "main" / "java" / "demo").mkdir(parents=True)
    artifacts = ArtifactManager(repo_root)
    indexer = IndexService(repo_root)
    patch_engine = PatchEngine(repo_root)
    fetcher = CodeFetcher(repo_root)
    indexer.perform_rebuild()
    return AutoPatchAgent(repo_root, artifacts, indexer, patch_engine, fetcher, llm=mock_llm)


def _fetch_tool_names(mock_llm: MagicMock) -> list[str]:
    tools = mock_llm.chat.call_args.kwargs["tools"]
    return [tool["function"]["name"] for tool in tools]


def test_perform_code_explain_uses_read_only_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="解释完成")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_explain("@User.java 解释一下代码")

    assert _fetch_tool_names(mock_llm) == ["search_symbols", "read_source_code"]


def test_perform_code_audit_uses_finding_driven_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="审计完成")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_audit("@User.java 检查代码")

    assert _fetch_tool_names(mock_llm) == [
        "get_finding_detail",
        "read_source_code",
        "propose_patch",
    ]


def test_perform_patch_revise_uses_rewrite_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="重写完成")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_patch_revise("加一句注释")

    assert _fetch_tool_names(mock_llm) == [
        "search_symbols",
        "read_source_code",
        "get_finding_detail",
        "propose_patch",
    ]


def test_chat_keeps_legacy_full_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="legacy done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.chat("检查代码")

    assert _fetch_tool_names(mock_llm) == [
        "scan_project",
        "propose_patch",
        "search_symbols",
        "read_source_code",
        "get_finding_detail",
    ]


def test_perform_general_chat_disables_tool_calls(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="闲聊回答")
    agent = _build_agent(tmp_path, mock_llm)

    response = agent.perform_general_chat("这个项目复杂吗")

    assert response == "闲聊回答"
    assert _fetch_tool_names(mock_llm) == []
