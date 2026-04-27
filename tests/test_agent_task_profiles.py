from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from autopatch_j.agent.session import AgentSession
from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.agent.llm_client import LLMClient, LLMResponse
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.patch_verifier import PatchVerifier


def _build_agent(tmp_path: Path, mock_llm: MagicMock) -> AutoPatchAgent:
    repo_root = tmp_path
    (repo_root / "src" / "main" / "java" / "demo").mkdir(parents=True)
    artifacts = ArtifactManager(repo_root)
    symbol_indexer = SymbolIndexer(repo_root)
    patch_engine = PatchEngine(repo_root)
    fetcher = CodeFetcher(repo_root)
    symbol_indexer.rebuild_index()
    session = AgentSession(repo_root, artifacts, symbol_indexer, patch_engine, fetcher)
    return AutoPatchAgent(session=session, llm=mock_llm)


def _fetch_tool_names(mock_llm: MagicMock) -> list[str]:
    tools = mock_llm.chat.call_args.kwargs["tools"]
    return [tool["function"]["name"] for tool in tools]


def test_perform_code_explain_uses_navigation_tool_profile_by_default(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_explain("@User.java explain code", scope=None)

    assert _fetch_tool_names(mock_llm) == ["search_symbols", "read_source_code"]


def test_perform_code_explain_disables_symbol_search_in_single_file_mode(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_explain("@User.java explain code", scope=None, allow_symbol_search=False)

    assert _fetch_tool_names(mock_llm) == ["read_source_code"]


def test_perform_code_audit_uses_finding_driven_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_audit("@User.java audit code", current_finding=MagicMock(), force_reread=False)

    assert _fetch_tool_names(mock_llm) == [
        "get_finding_detail",
        "read_source_code",
        "propose_patch",
    ]


def test_perform_zero_finding_review_uses_lightweight_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_zero_finding_review("@User.java 检查代码", file_path="User.java")

    assert _fetch_tool_names(mock_llm) == [
        "read_source_code",
        "propose_patch",
    ]


def test_perform_patch_revise_uses_rewrite_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_patch_revise("add one comment", current_item=MagicMock(), remaining_items=[])

    assert _fetch_tool_names(mock_llm) == [
        "search_symbols",
        "read_source_code",
        "get_finding_detail",
        "propose_patch",
    ]


def test_perform_general_chat_disables_tool_calls(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="chat answer")
    agent = _build_agent(tmp_path, mock_llm)

    response = agent.perform_general_chat("what does this project do")

    assert response == "chat answer"
    assert _fetch_tool_names(mock_llm) == []


def test_dehydrate_history_preserves_tool_sequence_and_compresses_old_tools(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)
    long_content = "x" * 260

    agent.messages = [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_source_code", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "read_source_code",
            "content": long_content,
        },
        {
            "role": "tool",
            "tool_call_id": "call-2",
            "name": "scan_project",
            "content": long_content,
        },
        {"role": "assistant", "content": "recent"},
        {"role": "user", "content": "third"},
        {"role": "assistant", "content": "latest"},
        {"role": "user", "content": "second"},
    ]

    dehydrated = agent._dehydrate_history("system prompt")

    assert [message["role"] for message in dehydrated] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert dehydrated[3]["content"].endswith("... [已脱水压缩] ...")
    assert dehydrated[4]["content"] == long_content


def test_model_label_returns_llm_model_name() -> None:
    llm = LLMClient(api_key="key", base_url="https://example.com", model="deepseek-v4-flash")
    session = AgentSession(
        repo_root=Path("."),
        artifacts=MagicMock(),
        symbol_indexer=MagicMock(),
        patch_engine=MagicMock(),
        fetcher=MagicMock(),
    )
    agent = AutoPatchAgent(session=session, llm=llm)
    assert agent.model_label == "deepseek-v4-flash"
