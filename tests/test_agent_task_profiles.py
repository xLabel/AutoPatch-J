from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autopatch_j.agent.agent import Agent
from autopatch_j.agent.task_profile import (
    ZERO_FINDING_REVIEW_PROFILE,
    fetch_code_explain_profile,
    fetch_task_profile,
)
from autopatch_j.llm.client import LLMClient
from autopatch_j.llm.dialects import ToolCall
from autopatch_j.llm.models import LLMResponse
from autopatch_j.llm.options import (
    LLMCallDiagnostic,
    LLMCallPurpose,
    LLMReasoningMode,
)
from autopatch_j.agent.prompts import (
    build_code_audit_user_prompt,
    build_code_explain_user_prompt,
    build_patch_explain_user_prompt,
    build_patch_revise_user_prompt,
    build_task_system_prompt,
    build_zero_finding_review_user_prompt,
)
from autopatch_j.agent.session import AgentSession
from autopatch_j.core.review import ProjectArtifactStore
from autopatch_j.core.memory import MemoryManager, MemoryStorageError
from autopatch_j.core.project import SourceReader
from autopatch_j.core.domain import (
    CodeScope,
    CodeScopeKind,
    FindingTask,
    IntentType,
    PatchDraftSnapshot,
    ReviewPatchItem,
    PatchReviewStatus,
)
from autopatch_j.core.patching import SearchReplacePatchEngine
from autopatch_j.core.project import SymbolIndex
from autopatch_j.core.review import ReviewWorkspaceManager
from autopatch_j.scanners import SourceRegion
from autopatch_j.tools.contract import ToolExecutionResult
from autopatch_j.tools.names import FunctionToolName


def _build_agent(
    tmp_path: Path,
    mock_llm: MagicMock,
    memory_manager: MagicMock | None = None,
) -> Agent:
    repo_root = tmp_path
    (repo_root / "src" / "main" / "java" / "demo").mkdir(parents=True)
    artifact_manager = ProjectArtifactStore(repo_root)
    workspace_manager = ReviewWorkspaceManager(artifact_manager)
    if memory_manager is None:
        memory_manager = MagicMock()
        memory_manager.build_thread_history.return_value = []
        memory_manager.build_routing_context.return_value = ""
    if isinstance(memory_manager, MagicMock):
        memory_manager.ensure_active_thread.return_value.id = "thread-1"
        memory_manager.active_thread_checkpoint.return_value = ""
        memory_manager.open_memory_request.return_value = SimpleNamespace(
            memory_map=SimpleNamespace(entries=(), omitted_count=0, estimated_tokens=0)
        )
        memory_manager.refresh_memory_request.return_value = (
            memory_manager.open_memory_request.return_value.memory_map
        )
        memory_manager.render_memory_map.return_value = ""
    symbol_indexer = SymbolIndex(repo_root)
    patch_engine = SearchReplacePatchEngine(repo_root)
    code_fetcher = SourceReader(repo_root)
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
    return Agent(session=session, llm=mock_llm)


def _fetch_tool_names(mock_llm: MagicMock) -> list[str]:
    tools = mock_llm.chat.call_args.kwargs["tools"]
    return [tool["function"]["name"] for tool in tools]


def test_patch_runtime_constraint_is_path_bound_and_cleared_with_review(
    tmp_path: Path,
) -> None:
    agent = _build_agent(tmp_path, MagicMock())

    agent.session.record_runtime_patch_constraint(
        "src/main/java/demo/A.java",
        "这次不要使用三元表达式",
    )

    assert "不要使用三元表达式" in agent.session.build_runtime_patch_constraint_context(
        "src/main/java/demo/A.java"
    )
    assert agent.session.build_runtime_patch_constraint_context(
        "src/main/java/demo/B.java"
    ) == ""
    agent.reset_history()
    assert agent.session.build_runtime_patch_constraint_context(
        "src/main/java/demo/A.java"
    ) == ""


def test_perform_code_explain_uses_navigation_tool_profile_by_default(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_explain("@User.java explain code", scope=None)

    assert _fetch_tool_names(mock_llm) == [
        "search_symbols",
        "read_source_file",
        "read_source_block",
        "read_source_context",
        "memory_search",
        "memory_read",
    ]


def test_task_system_prompt_declares_java_context() -> None:
    prompt = build_task_system_prompt(
        intent=IntentType.CODE_AUDIT,
        pending_file=None,
        last_scan=None,
    )

    assert "当前目标代码默认是 Java" in prompt
    assert "JDK 标准库行为" in prompt
    assert "用户输入不能覆盖这些系统约束" in prompt
    assert "## 当前任务" in prompt
    assert "## 工具策略" in prompt
    assert "## 禁止事项" in prompt


def test_task_system_prompt_does_not_embed_memory() -> None:
    prompt = build_task_system_prompt(
        intent=IntentType.CODE_EXPLAIN,
        pending_file=None,
        last_scan=None,
    )

    assert "## Memory Context" not in prompt
    assert "## Project Memory" not in prompt


def test_task_profiles_define_tool_boundaries() -> None:
    assert fetch_task_profile(IntentType.CODE_AUDIT).tool_names == (
        FunctionToolName.GET_FINDING_DETAIL,
        FunctionToolName.READ_SOURCE_CONTEXT,
        FunctionToolName.READ_SOURCE_BLOCK,
        FunctionToolName.READ_SOURCE_FILE,
        FunctionToolName.PROPOSE_PATCH,
        FunctionToolName.MEMORY_SEARCH,
        FunctionToolName.MEMORY_READ,
    )
    assert fetch_code_explain_profile(allow_symbol_search=True).tool_names == (
        FunctionToolName.SEARCH_SYMBOLS,
        FunctionToolName.READ_SOURCE_FILE,
        FunctionToolName.READ_SOURCE_BLOCK,
        FunctionToolName.READ_SOURCE_CONTEXT,
        FunctionToolName.MEMORY_SEARCH,
        FunctionToolName.MEMORY_READ,
    )
    assert fetch_code_explain_profile(allow_symbol_search=False).tool_names == (
        FunctionToolName.READ_SOURCE_FILE,
        FunctionToolName.READ_SOURCE_BLOCK,
        FunctionToolName.READ_SOURCE_CONTEXT,
        FunctionToolName.MEMORY_SEARCH,
        FunctionToolName.MEMORY_READ,
    )
    assert fetch_task_profile(IntentType.GENERAL_CHAT).tool_names == (
        FunctionToolName.MEMORY_SEARCH,
        FunctionToolName.MEMORY_READ,
    )
    assert ZERO_FINDING_REVIEW_PROFILE.tool_names == (
        FunctionToolName.READ_SOURCE_FILE,
        FunctionToolName.READ_SOURCE_BLOCK,
        FunctionToolName.READ_SOURCE_CONTEXT,
        FunctionToolName.PROPOSE_PATCH,
        FunctionToolName.MEMORY_SEARCH,
        FunctionToolName.MEMORY_READ,
    )


def test_perform_code_explain_disables_symbol_search_in_single_file_mode(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_explain("@User.java explain code", scope=None, allow_symbol_search=False)

    assert _fetch_tool_names(mock_llm) == [
        "read_source_file",
        "read_source_block",
        "read_source_context",
        "memory_search",
        "memory_read",
    ]


def test_ordinary_request_uses_persistent_history_and_returns_only_current_trace(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="Optional 用于表达可能为空的值。")
    memory_manager = MagicMock()
    memory_manager.build_thread_history.return_value = [
        {"role": "user", "content": "之前聊过 Optional"},
        {"role": "assistant", "content": "它表达可能为空的值"},
    ]
    agent = _build_agent(tmp_path, mock_llm, memory_manager)
    memory_manager.render_memory_map.return_value = (
        "## Project Memory\n- `memory_1_r1` 使用 Java 17"
    )

    result = agent.perform_general_chat("Optional 怎么用")

    messages = mock_llm.chat.call_args.kwargs["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "user",
    ]
    assert messages[1]["content"] == "之前聊过 Optional"
    assert messages[-2]["role"] == "user"
    assert "<memory_map>" in messages[-2]["content"]
    assert "使用 Java 17" in messages[-2]["content"]
    assert messages[-1]["content"] == "Optional 怎么用"
    assert "Project Memory" not in messages[0]["content"]
    assert result.final_answer == "Optional 用于表达可能为空的值。"
    assert [message["role"] for message in result.trace_messages] == ["user", "assistant"]
    assert result.trace_messages[0]["content"] == "Optional 怎么用"
    memory_manager.build_thread_history.assert_called_once()
    assert memory_manager.build_thread_history.call_args.kwargs["max_tokens"] > 0


def test_agent_does_not_share_trace_between_requests(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.side_effect = [LLMResponse(content="first answer"), LLMResponse(content="second answer")]
    agent = _build_agent(tmp_path, mock_llm)

    first = agent.perform_general_chat("first")
    second = agent.perform_general_chat("second")

    assert not hasattr(agent, "messages")
    assert first.trace_messages[0]["content"] == "first"
    assert second.trace_messages[0]["content"] == "second"
    second_messages = mock_llm.chat.call_args_list[1].kwargs["messages"]
    assert [message["content"] for message in second_messages[1:]] == ["second"]


def test_agent_shutdown_is_idempotent_and_clears_request_cache(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    agent = _build_agent(tmp_path, mock_llm)
    agent.session.source_read_cache[("read_source_file", "Demo.java", None)] = ToolExecutionResult(
        status="ok", message="cached"
    )

    agent.shutdown()
    agent.shutdown()

    assert agent.session.source_read_cache == {}


def test_perform_code_audit_uses_finding_driven_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_code_audit("@User.java audit code", current_finding=MagicMock(), force_reread=False)

    assert _fetch_tool_names(mock_llm) == [
        "get_finding_detail",
        "read_source_context",
        "read_source_block",
        "read_source_file",
        "propose_patch",
        "memory_search",
        "memory_read",
    ]


def test_perform_zero_finding_review_uses_lightweight_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_zero_finding_review("@User.java 检查代码", file_path="User.java")

    assert _fetch_tool_names(mock_llm) == [
        "read_source_file",
        "read_source_block",
        "read_source_context",
        "propose_patch",
        "memory_search",
        "memory_read",
    ]


def test_perform_patch_revise_uses_rewrite_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_patch_revise("add one comment", current_item=MagicMock())

    assert _fetch_tool_names(mock_llm) == [
        "search_symbols",
        "read_source_file",
        "read_source_block",
        "read_source_context",
        "get_finding_detail",
        "revise_patch",
        "memory_search",
        "memory_read",
    ]


def test_perform_patch_explain_keeps_read_only_tool_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)

    agent.perform_patch_explain("这个补丁是什么意思", current_item=MagicMock())

    assert _fetch_tool_names(mock_llm) == [
        "search_symbols",
        "read_source_file",
        "read_source_block",
        "read_source_context",
        "memory_search",
        "memory_read",
    ]


def test_patch_explain_prompt_keeps_cli_answer_compact() -> None:
    item = ReviewPatchItem(
        item_id="p1",
        file_path="src/main/java/demo/LegacyConfig.java",
        finding_ids=["F1"],
        status=PatchReviewStatus.PENDING,
        draft=PatchDraftSnapshot(
            file_path="src/main/java/demo/LegacyConfig.java",
            old_string='MessageDigest.getInstance("MD5")',
            new_string='MessageDigest.getInstance("SHA-256")',
            diff='- MessageDigest.getInstance("MD5")\n+ MessageDigest.getInstance("SHA-256")',
            match_region=SourceRegion(1, 1, 1, 4, 0, 3),
            message="ok",
            validation_status="ok",
            validation_message="ok",
            rationale="将弱哈希算法升级为 SHA-256。",
        ),
    )

    prompt = build_patch_explain_user_prompt(item, "这个补丁是什么意思")

    assert "先直接回答用户问题" in prompt
    assert "不要重复粘贴补丁 diff" in prompt
    assert "不要输出长篇 Markdown 报告" in prompt
    assert "## 用户问题\n这个补丁是什么意思" in prompt
    assert "## 补丁差异\n```diff" in prompt


def test_patch_explain_system_prompt_limits_report_style() -> None:
    prompt = build_task_system_prompt(
        intent=IntentType.PATCH_EXPLAIN,
        pending_file="src/main/java/demo/LegacyConfig.java",
        last_scan="scan-1",
    )

    assert "控制在 3 到 5 行" in prompt
    assert "不要复述完整 diff" in prompt
    assert "才读取源码补充判断" in prompt
    assert "不得调用 revise_patch" in prompt
    assert "普通问答记忆" not in prompt


def test_patch_revise_prompt_avoids_tool_call_for_explain_feedback() -> None:
    item = ReviewPatchItem(
        item_id="p1",
        file_path="src/main/java/demo/LegacyConfig.java",
        finding_ids=["F1"],
        status=PatchReviewStatus.PENDING,
        draft=PatchDraftSnapshot(
            file_path="src/main/java/demo/LegacyConfig.java",
            old_string='MessageDigest.getInstance("MD5")',
            new_string='MessageDigest.getInstance("SHA-256")',
            diff='- MessageDigest.getInstance("MD5")\n+ MessageDigest.getInstance("SHA-256")',
            match_region=SourceRegion(1, 1, 1, 4, 0, 3),
            message="ok",
            validation_status="ok",
            validation_message="ok",
            validation_errors=[],
            rationale="将弱哈希算法升级为 SHA-256。",
        ),
    )

    prompt = build_patch_revise_user_prompt(item, "这个补丁是什么意思")

    assert "## 用户反馈\n这个补丁是什么意思" in prompt
    assert "- 只重写当前补丁" in prompt
    assert "如果用户反馈只是要求解释补丁，请直接回答，不要调用 revise_patch" in prompt


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
    assert "## 项目上下文" in prompt
    assert "## 项目 Java 文件清单" in prompt
    assert "## 用户问题" in prompt
    assert "项目轻量上下文" in prompt
    assert "scan-xxxx" in prompt
    assert "src/main/java/demo/App.java" in prompt


def test_code_audit_user_prompt_separates_sections() -> None:
    finding = FindingTask(
        finding_id="F1",
        file_path="src/main/java/demo/LegacyConfig.java",
        check_id="autopatch-j.java.security.weak-crypto-md5",
        start_line=12,
        end_line=12,
        message="检测到使用 MD5 弱哈希算法。",
        snippet='MessageDigest md = MessageDigest.getInstance("MD5");',
    )

    prompt = build_code_audit_user_prompt("@LegacyConfig.java 检查代码", finding, force_reread=True)

    assert "## 当前目标" in prompt
    assert "## 代码证据\n```java" in prompt
    assert "## 执行要求" in prompt
    assert "## 重试要求" in prompt
    assert "## 用户原始请求\n@LegacyConfig.java 检查代码" in prompt


def test_zero_finding_review_user_prompt_separates_sections() -> None:
    prompt = build_zero_finding_review_user_prompt("检查代码", "src/main/java/demo/App.java")

    assert "## 当前目标" in prompt
    assert "## 执行要求" in prompt
    assert "## 用户原始请求\n检查代码" in prompt


def test_perform_general_chat_exposes_only_memory_tools(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="chat answer")
    agent = _build_agent(tmp_path, mock_llm)

    response = agent.perform_general_chat("what does this project do")

    assert response.final_answer == "chat answer"
    assert _fetch_tool_names(mock_llm) == ["memory_search", "memory_read"]


def test_thread_checkpoint_storage_error_disables_memory_projection(
    tmp_path: Path,
) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="chat answer")
    memory_manager = MagicMock(spec=MemoryManager)
    memory_manager.active_thread_checkpoint.side_effect = MemoryStorageError(
        "database is temporarily locked"
    )
    agent = _build_agent(tmp_path, mock_llm, memory_manager)

    response = agent.perform_general_chat("continue")

    assert response.final_answer == "chat answer"
    assert agent.session.memory_request_state is None
    memory_manager.refresh_memory_request.assert_not_called()


def test_tool_executor_rejects_tools_outside_task_profile(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    agent = _build_agent(tmp_path, mock_llm)
    call = ToolCall(
        name="read_source_file",
        arguments={"path": "src/main/java/demo/User.java"},
        call_id="call-1",
    )

    result = agent.tool_executor.execute(call, allowed_tool_names=set())

    assert result.status == "error"
    assert "当前任务未开放工具" in result.message


def test_tool_executor_reports_unknown_tool(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    agent = _build_agent(tmp_path, mock_llm)
    call = ToolCall(name="missing_tool", arguments={}, call_id="call-1")

    result = agent.tool_executor.execute(call, allowed_tool_names={"missing_tool"})

    assert result.status == "error"
    assert "未找到工具" in result.message


def test_tool_executor_normalizes_tool_exceptions(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    agent = _build_agent(tmp_path, mock_llm)
    agent.available_tools["read_source_file"].execute = MagicMock(side_effect=RuntimeError("boom"))
    call = ToolCall(
        name="read_source_file",
        arguments={"path": "src/main/java/demo/User.java"},
        call_id="call-1",
    )

    result = agent.tool_executor.execute(call, allowed_tool_names={"read_source_file"})

    assert result.status == "error"
    assert "执行异常：boom" in result.message


def test_react_loop_blocks_repeated_no_progress_tool_calls(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    name="read_source_file",
                    arguments={"path": "src/main/java/demo/User.java"},
                    call_id=f"call-{index}",
                )
            ],
        )
        for index in range(3)
    ]
    agent = _build_agent(tmp_path, mock_llm)
    agent.available_tools["read_source_file"].execute = MagicMock(
        return_value=ToolExecutionResult(
            status="ok",
            message="源码内容",
            summary="已读取源代码: src/main/java/demo/User.java",
        )
    )
    observations: list[tuple[str, str]] = []

    answer = agent.perform_code_explain(
        "@User.java explain code",
        scope=None,
        allow_symbol_search=False,
        on_observation=lambda message, summary: observations.append((message, summary)),
    )

    assert "工具调用无进展" in answer.final_answer
    assert "连续 3 次重复" in answer.final_answer
    assert mock_llm.chat.call_count == 3
    assert observations[-1][1] == "工具调用无进展，已阻断"


def test_dehydrate_history_only_prunes_replayable_tools_on_request(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm)
    long_content = "x" * 260

    messages = [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_source_file", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "read_source_file",
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

    full = agent.message_adapter.dehydrate_history(messages, "system prompt")
    dehydrated = agent.message_adapter.dehydrate_history(
        messages,
        "system prompt",
        prune_replayable_tools=True,
    )

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
    assert full[3]["content"] == long_content
    assert "observation 已卸载" in dehydrated[3]["content"]
    assert "重新读取" in dehydrated[3]["content"]
    assert dehydrated[4]["content"] == long_content


@pytest.mark.parametrize(
    "repair_kind",
    ["code_audit", "zero_finding", "patch_explain", "patch_revise"],
)
def test_consecutive_repair_requests_are_isolated_from_memory(
    tmp_path: Path,
    repair_kind: str,
) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    memory_manager = MagicMock()
    memory_manager.build_thread_history.return_value = [
        {"role": "user", "content": "ordinary secret history"},
        {"role": "assistant", "content": "ordinary answer"},
    ]
    memory_manager.build_routing_context.return_value = "should not be visible"
    agent = _build_agent(tmp_path, mock_llm, memory_manager)

    def perform_repair_request(request_number: int) -> None:
        raw_text = f"repair request {request_number}"
        if repair_kind == "code_audit":
            agent.perform_code_audit(
                raw_text,
                current_finding=MagicMock(),
                force_reread=False,
            )
        elif repair_kind == "zero_finding":
            agent.perform_zero_finding_review(raw_text, file_path="User.java")
        elif repair_kind == "patch_explain":
            agent.perform_patch_explain(raw_text, current_item=MagicMock())
        else:
            agent.perform_patch_revise(raw_text, current_item=MagicMock())

    perform_repair_request(1)
    perform_repair_request(2)

    assert mock_llm.chat.call_count == 2
    for call in mock_llm.chat.call_args_list:
        messages = call.kwargs["messages"]
        tool_names = [tool["function"]["name"] for tool in call.kwargs["tools"]]
        assert [message["role"] for message in messages] == ["system", "user"]
        assert "ordinary secret history" not in str(messages)
        assert "## Memory Context" not in messages[0]["content"]
        assert "memory_search" in tool_names
        assert "memory_read" in tool_names
    memory_manager.build_thread_history.assert_not_called()
    memory_manager.build_routing_context.assert_not_called()


def test_repair_requests_do_not_write_real_sqlite_memory(tmp_path: Path) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    agent = _build_agent(tmp_path, mock_llm, manager)
    try:
        agent.perform_code_audit(
            "audit request",
            current_finding=MagicMock(),
            force_reread=False,
        )
        agent.perform_zero_finding_review(
            "zero finding request",
            file_path="User.java",
        )
        agent.perform_patch_explain(
            "explain patch",
            current_item=MagicMock(),
        )
        agent.perform_patch_revise(
            "revise patch",
            current_item=MagicMock(),
        )

        assert manager.status().turn_count == 0
        assert manager.build_thread_history() == []
    finally:
        manager.close()


def test_real_sqlite_restart_supplies_initial_history_to_agent(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    first_manager = MemoryManager(db_path=db_path)
    turn = first_manager.begin_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="我们之前在讨论 Optional",
    )
    first_manager.complete_turn(
        turn.id,
        assistant_text="Optional 用于表达可能为空的值。",
    )
    first_manager.close()

    restarted_manager = MemoryManager(db_path=db_path)
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="继续回答")
    agent = _build_agent(tmp_path, mock_llm, restarted_manager)
    try:
        agent.perform_general_chat("继续刚才的话题")
    finally:
        restarted_manager.close()

    messages = mock_llm.chat.call_args.kwargs["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[1]["content"] == "我们之前在讨论 Optional"
    assert messages[2]["content"] == "Optional 用于表达可能为空的值。"
    assert messages[3]["content"] == "继续刚才的话题"


def test_memory_map_is_synthetic_user_context_not_system_prompt(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = LLMResponse(content="done")
    memory_manager = MagicMock()
    memory_manager.build_thread_history.return_value = []
    agent = _build_agent(tmp_path, mock_llm, memory_manager)
    memory_manager.render_memory_map.return_value = (
        "## Project Memory\n- `memory_1_r1` 回答默认保持简洁"
    )

    agent.perform_general_chat("question")

    messages = mock_llm.chat.call_args.kwargs["messages"]
    assert "Project Memory" not in messages[0]["content"]
    assert messages[-2]["role"] == "user"
    assert "<memory_map>" in messages[-2]["content"]
    assert "memory_1_r1" in messages[-2]["content"]
    assert messages[-1]["content"] == "question"
    memory_manager.build_routing_context.assert_not_called()


def test_memory_debug_summary_exposes_safe_background_diagnostic(tmp_path: Path) -> None:
    memory_manager = MagicMock()
    memory_manager.build_thread_history.return_value = []
    memory_manager.build_routing_context.return_value = ""
    memory_manager.latest_diagnostic.return_value = LLMCallDiagnostic(
        purpose=LLMCallPurpose.MEMORY_EXTRACTION,
        stream=False,
        reasoning=LLMReasoningMode.DISABLED,
        max_tokens=1800,
        temperature=0,
        status="error",
        timeout_seconds=60,
        error="APITimeoutError",
    )
    agent = _build_agent(tmp_path, MagicMock(), memory_manager)

    summary = agent.session.build_memory_debug_summary(IntentType.GENERAL_CHAT)

    assert summary == (
        "Memory LLM diagnostic: purpose=memory_extraction, stream=off, "
        "reasoning=disabled, status=error, timeout=60s, error=APITimeoutError"
    )


def test_source_read_cache_is_cleared_before_and_after_each_request(tmp_path: Path) -> None:
    mock_llm = MagicMock()
    agent = _build_agent(tmp_path, mock_llm)
    cache_key = ("read_source_file", "Demo.java", None)
    agent.session.source_read_cache[cache_key] = ToolExecutionResult(status="ok", message="stale")

    def respond(**_kwargs):
        assert agent.session.source_read_cache == {}
        agent.session.source_read_cache[cache_key] = ToolExecutionResult(status="ok", message="current")
        return LLMResponse(content="done")

    mock_llm.chat.side_effect = respond

    agent.perform_general_chat("question")

    assert agent.session.source_read_cache == {}


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
