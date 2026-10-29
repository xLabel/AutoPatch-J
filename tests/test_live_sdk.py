"""显式授权后的真实 SDK 集成：使用临时项目，不修改示例或用户源码。"""

import pathlib

import pytest

from autopatch_j.agent import classification
from autopatch_j.agent import result
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.core.domain import intent
from autopatch_j.core.domain import scope as domain_scope
from autopatch_j.core.patching import types
from autopatch_j.llm import factory
from autopatch_j.llm import runtime as model_runtime

pytestmark = pytest.mark.live_llm


def test_live_short_classifiers(live_llm_client: model_runtime.ModelRuntime):
    detected, error = classification.build_intent_classifier(live_llm_client)(
        "你好，聊聊你能做什么", False
    )
    assert error == "" and detected is intent.IntentType.GENERAL_CHAT
    route, error = classification.build_route_classifier(live_llm_client)(
        "请解释当前补丁为什么这样改", "Demo.java", None
    )
    assert error == "" and route is intent.ConversationRoute.REVIEW_CONTINUE


def test_live_reasoning_and_source_tools(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
):
    (tmp_path / "Demo.java").write_text(
        "class Demo {\n    int twice(int n) { return n * 2; }\n}\n",
        encoding="utf-8",
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, lambda: live_llm_client)
    reasoning = []
    runtime.agent.session.set_focus_paths(["Demo.java"])
    try:
        response = runtime.agent.perform_code_explain(
            "请先调用 read_source_file 读取 Demo.java，再独立调用 "
            "read_source_context 读取第 2 行，完成两次工具调用后解释 twice。",
            scope=None,
            allow_symbol_search=False,
            on_reasoning=reasoning.append,
        )
        assert response.status is result.RunStatus.COMPLETED
        assert response.final_answer and reasoning
        names = {
            item.name for item in response.observations if item.status == "ok"
        }
        assert {"read_source_file", "read_source_context"} <= names
        assert runtime.model_runtime.run(runtime.conversations.load()).messages
    finally:
        runtime.close()


def test_live_notebook_write_and_restart(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
):
    runtime = cli_runtime.build_cli_runtime(tmp_path, lambda: live_llm_client)
    try:
        response = runtime.agent.perform_general_chat(
            "我明确确认本项目的长期决定：每次补丁都必须经人工审核，"
            "决定标识 APJ-LIVE-DECISION-42。请用 write_memory 将这项决定"
            "写入 MEMORY.md，随后用 read_memory 读回确认。"
        )
        assert response.status is result.RunStatus.COMPLETED
        note = runtime.model_runtime.run(runtime.memory.show())
        assert note is not None and "APJ-LIVE-DECISION-42" in note.content
        runtime.model_runtime.run(runtime.conversations.new())
    finally:
        runtime.close()
    restarted = cli_runtime.build_cli_runtime(
        tmp_path, factory.build_model_runtime
    )
    try:
        assert not restarted.model_runtime.run(
            restarted.conversations.load()
        ).messages
        response = restarted.agent.perform_general_chat(
            "本项目的长期审核决定是什么？请包含决定标识。"
        )
        assert response.status is result.RunStatus.COMPLETED
        assert "APJ-LIVE-DECISION-42" in response.final_answer
        note = restarted.model_runtime.run(restarted.memory.show())
        assert note is not None and "人工审核" in note.content
    finally:
        restarted.close()


def test_live_scan_draft_review_apply_and_rescan(
    tmp_path: pathlib.Path, live_llm_client: model_runtime.ModelRuntime
):
    source = tmp_path / "Demo.java"
    original = (
        "import java.security.MessageDigest;\n"
        "class Demo {\n"
        "  MessageDigest digest() throws Exception {\n"
        '    return MessageDigest.getInstance("MD5");\n'
        "  }\n}\n"
    )
    source.write_text(original, encoding="utf-8")
    runtime = cli_runtime.build_cli_runtime(tmp_path, lambda: live_llm_client)
    scope = domain_scope.CodeScope(
        domain_scope.CodeScopeKind.SINGLE_FILE,
        ["Demo.java"],
        ["Demo.java"],
        True,
    )
    try:
        scan_id, scan = runtime.scanner_runner.run_scan_and_save(scope)
        backlog = runtime.backlog_manager.build_from_scan_result(scan)
        target = next(
            item
            for item in backlog
            if item.check_id.endswith("weak-crypto-md5")
        )
        runtime.workspace_manager.initialize_review(scope, scan_id, [])
        runtime.agent.session.set_focus_paths(["Demo.java"])
        response = runtime.agent.perform_code_audit(
            "只修复当前 MD5 finding，将算法改为 SHA-256，提交草案供我审核。",
            target,
            force_reread=False,
        )
        assert response.status is result.RunStatus.COMPLETED
        draft = runtime.agent.session.pop_proposed_patch_draft()
        assert draft is not None and draft.source_scan_id == scan_id
        assert draft.associated_finding_id == target.finding_id
        assert source.read_text(encoding="utf-8") == original
        runtime.workspace_manager.add_patch(draft)
        assert runtime.workspace_manager.load_current_patch_draft() == draft
        # 测试在临时项目明确代表人工批准；产品没有自动 apply。
        outcome = runtime.review_service.apply(draft)
        assert outcome.application.applied
        assert outcome.verification is not None
        assert (
            outcome.verification.outcome is types.VerificationOutcome.RESOLVED
        )
        assert '"SHA-256"' in source.read_text(encoding="utf-8")
        assert not runtime.workspace_manager.load().has_pending_patch()
    finally:
        runtime.close()
