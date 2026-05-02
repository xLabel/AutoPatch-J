from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from autopatch_j.agent.agent import Agent
from autopatch_j.llm.client import LLMClient, LLMResponse
from autopatch_j.agent.prompts import (
    build_code_explain_user_prompt,
    build_patch_explain_user_prompt,
    build_task_system_prompt,
)
from autopatch_j.agent.session import AgentSession
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.models import (
    CodeScope,
    CodeScopeKind,
    IntentType,
    PatchDraftData,
    PatchReviewItem,
    PatchReviewStatus,
)
from autopatch_j.core.memory import MemoryManager
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.workspace_manager import WorkspaceManager


def _build_agent(tmp_path: Path, mock_llm: MagicMock) -> Agent:
    repo_root = tmp_path
    (repo_root / "src" / "main" / "java" / "demo").mkdir(parents=True)
    artifact_manager = ArtifactManager(repo_root)
    workspace_manager = WorkspaceManager(artifact_manager)
    memory_manager = MemoryManager(artifact_manager.state_dir / "memory.json")
    symbol_indexer = SymbolIndexer(repo_root)
    patch_engine = PatchEngine(repo_root)
    code_fetcher = CodeFetcher(repo_root)
    symbol_indexer.rebuild_index()
    session = AgentSession(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        symbol_indexer=symbol_indexer,
        patch_engine=patch_engine,
        code_fetcher=code_fetcher,
        memory_manager=memory_manager,
    )
    agent = Agent(session=session, llm=mock_llm)
    agent.memory_summary_scheduler = None
    return agent


def _fetch_tool_names(mock_llm: MagicMock) -> list[str]:
    tools = mock_llm.chat.call_args.kwargs["tools"]
    return [tool["function"]["name"] for tool in tools]


def test_perform_code_explain_uses_navigation_tool_profile_by_default(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_explain("@User.java explain code", scope=None)

    assert _fetch_tool_names(mock_llm) == ["search_symbols", "read_source_code"]


def test_task_system_prompt_declares_java_context() -> None:
    prompt = build_task_system_prompt(
        intent=IntentType.CODE_AUDIT,
        pending_file=None,
        last_scan=None,
    )

    assert "当前目标代码默认是 Java" in prompt
    assert "JDK 标准库行为" in prompt


def test_perform_code_explain_disables_symbol_search_in_single_file_mode(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_explain("@User.java explain code", scope=None, allow_symbol_search=False)

    assert _fetch_tool_names(mock_llm) == ["read_source_code"]


def test_memory_is_shared_by_code_explain_and_general_chat(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.side_effect = [
        LLMResponse(content="这是一个演示项目。"),
        LLMResponse(content="Optional 用于表达可能为空的值。"),
        LLMResponse(content="{}"),
    ]
    agent = _build_agent(tmp_path, mock_llm)
    scope = CodeScope(
        kind=CodeScopeKind.PROJECT,
        source_roots=["."],
        focus_files=["src/main/java/demo/Demo.java"],
        is_locked=False,
    )

    agent.perform_code_explain("这个项目是干什么的", scope=scope)
    agent.perform_general_chat("Optional 怎么用")

    second_messages = mock_llm.chat.call_args_list[1].kwargs["messages"]
    assert "普通问答记忆" in second_messages[0]["content"]
    assert "这个项目是干什么的" in second_messages[0]["content"]

    memory = agent.session.memory_manager.load()
    recent_turns = memory["working_memory"]["recent_turns"]
    assert len(recent_turns) == 2
    assert recent_turns[0]["intent"] == IntentType.CODE_EXPLAIN.value
    assert recent_turns[0]["scope_paths"] == ["src/main/java/demo/Demo.java"]
    assert recent_turns[1]["intent"] == IntentType.GENERAL_CHAT.value
    assert "Optional" in recent_turns[1]["user_text"]


def test_reset_history_keeps_memory_unless_explicitly_cleared(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="chat answer")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_general_chat("Java Optional 怎么用")
    agent.reset_history()

    memory = agent.session.memory_manager.load()
    assert "Java Optional" in memory["working_memory"]["recent_turns"][0]["user_text"]

    agent.reset_history(clear_memory=True)

    memory = agent.session.memory_manager.load()
    assert memory["working_memory"]["recent_turns"] == []


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

    agent.perform_patch_revise("add one comment", current_item=MagicMock())

    assert _fetch_tool_names(mock_llm) == [
        "search_symbols",
        "read_source_code",
        "get_finding_detail",
        "revise_patch",
    ]


def test_perform_patch_explain_keeps_read_only_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_patch_explain("这个补丁是什么意思", current_item=MagicMock())

    assert _fetch_tool_names(mock_llm) == [
        "search_symbols",
        "read_source_code",
    ]


def test_patch_explain_prompt_keeps_cli_answer_compact() -> None:
    item = PatchReviewItem(
        item_id="p1",
        file_path="src/main/java/demo/LegacyConfig.java",
        finding_ids=["F1"],
        status=PatchReviewStatus.PENDING,
        draft=PatchDraftData(
            file_path="src/main/java/demo/LegacyConfig.java",
            old_string='MessageDigest.getInstance("MD5")',
            new_string='MessageDigest.getInstance("SHA-256")',
            diff='- MessageDigest.getInstance("MD5")\n+ MessageDigest.getInstance("SHA-256")',
            validation_status="ok",
            validation_message="ok",
            rationale="将弱哈希算法升级为 SHA-256。",
        ),
    )

    prompt = build_patch_explain_user_prompt(item, "这个补丁是什么意思")

    assert "先直接回答用户问题" in prompt
    assert "不要重复粘贴补丁 diff" in prompt
    assert "不要输出长篇 Markdown 报告" in prompt
    assert "用户问题:\n这个补丁是什么意思" in prompt


def test_patch_explain_system_prompt_limits_report_style() -> None:
    prompt = build_task_system_prompt(
        intent=IntentType.PATCH_EXPLAIN,
        pending_file="src/main/java/demo/LegacyConfig.java",
        last_scan="scan-1",
        memory_context="- general_chat; 用户关注: Optional",
    )

    assert "控制在 3 到 5 行" in prompt
    assert "不要复述完整 diff" in prompt
    assert "才读取源码补充判断" in prompt
    assert "普通问答记忆" not in prompt


def test_project_code_explain_prompt_uses_lightweight_project_context() -> None:
    scope = CodeScope(
        kind=CodeScopeKind.PROJECT,
        source_roots=["."],
        focus_files=["src/main/java/demo/App.java", "src/main/java/demo/UserService.java"],
        is_locked=False,
    )

    prompt = build_code_explain_user_prompt(
        "这个项目是干什么的",
        scope,
        project_context="项目轻量上下文\n- 项目根目录名: demo-repo",
    )

    assert "项目级代码讲解" in prompt
    assert "项目轻量上下文" in prompt
    assert "scan-xxxx" in prompt
    assert "src/main/java/demo/App.java" in prompt


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
        artifact_manager=MagicMock(),
        workspace_manager=MagicMock(),
        symbol_indexer=MagicMock(),
        patch_engine=MagicMock(),
        code_fetcher=MagicMock(),
    )
    agent = Agent(session=session, llm=llm)
    assert agent.model_label == "deepseek-v4-flash"
