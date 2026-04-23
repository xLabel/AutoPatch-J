from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from autopatch_j.cli.app import AutoPatchCLI
from autopatch_j.core.continuity_judge_service import ContinuityJudgeService
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.core.models import (
    CodeScope,
    CodeScopeKind,
    ConversationRoute,
    IntentType,
    PatchDraftData,
    PatchReviewItem,
    PatchReviewStatus,
)
from autopatch_j.scanners.base import Finding, ScanResult
from autopatch_j.validators.java_syntax import SyntaxValidationResult


def _make_cli(tmp_path: Path) -> AutoPatchCLI:
    (tmp_path / ".autopatch-j").mkdir(exist_ok=True)
    return AutoPatchCLI(tmp_path)


def _scope() -> CodeScope:
    return CodeScope(
        kind=CodeScopeKind.SINGLE_FILE,
        source_roots=["src/main/java/demo/User.java"],
        focus_files=["src/main/java/demo/User.java"],
        is_locked=True,
    )


def _review_item(item_id: str, file_path: str) -> PatchReviewItem:
    return PatchReviewItem(
        item_id=item_id,
        file_path=file_path,
        finding_ids=["F1"],
        status=PatchReviewStatus.PENDING,
        draft=PatchDraftData(
            file_path=file_path,
            old_string="old",
            new_string="new",
            diff="diff",
            validation_status="ok",
            validation_message="ok",
            validation_errors=[],
            rationale="rationale",
            target_check_id="F1",
            target_snippet="snippet",
        ),
    )


def _patch_draft(file_path: str, finding_id: str) -> PatchDraft:
    return PatchDraft(
        file_path=file_path,
        old_string="old",
        new_string="new",
        diff=f"diff-{finding_id}",
        validation=SyntaxValidationResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        rationale=f"fix {finding_id}",
        error_code=None,
        target_check_id=finding_id,
        target_snippet="snippet",
    )


def test_cli_code_audit_triggers_local_scan_then_agent(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.scope_service is not None
    assert cli.scan_service is not None
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.CODE_AUDIT)
    cli.scope_service.fetch_scope = MagicMock(return_value=_scope())
    cli.scan_service.run_scan_and_persist = MagicMock(
        return_value=(
            "scan-1",
            ScanResult(
                engine="semgrep",
                scope=["src/main/java/demo/User.java"],
                targets=["src/main/java/demo/User.java"],
                status="ok",
                message="ok",
                findings=[],
            ),
        )
    )
    cli.renderer.print_tool_start = MagicMock()
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False, compact_observation=False, **kwargs: captured.update(
        {
            "prompt": prompt,
            "agent_call": agent_call,
            "scope_paths": scope_paths,
            "render_no_issue_panel": render_no_issue_panel,
        }
    )

    cli.handle_chat("@User.java 检查代码")

    assert cli.scope_service.fetch_scope.call_count == 2
    cli.scan_service.run_scan_and_persist.assert_called_once()
    cli.renderer.print_tool_start.assert_called_once_with("scan_project", caller="AGENT")
    assert captured["agent_call"] == cli.agent.perform_code_audit
    assert captured["render_no_issue_panel"] is True


def test_cli_code_audit_targets_single_finding_prompt(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.scope_service is not None
    assert cli.scan_service is not None
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.CODE_AUDIT)
    cli.scope_service.fetch_scope = MagicMock(return_value=_scope())
    cli.scan_service.run_scan_and_persist = MagicMock(
        return_value=(
            "scan-1",
            ScanResult(
                engine="semgrep",
                scope=["src/main/java/demo/User.java"],
                targets=["src/main/java/demo/User.java"],
                status="ok",
                message="ok",
                findings=[
                    Finding(
                        check_id="autopatch-j.java.correctness.unsafe-equals-order",
                        path="src/main/java/demo/User.java",
                        start_line=5,
                        end_line=5,
                        severity="warning",
                        message="unsafe equals order",
                        snippet='return user.getName().equals("admin");',
                    )
                ],
            ),
        )
    )
    cli.renderer.print_tool_start = MagicMock()
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False, compact_observation=False, **kwargs: captured.update(
        {
            "prompt": prompt,
            "agent_call": agent_call,
            "scope_paths": scope_paths,
            "render_no_issue_panel": render_no_issue_panel,
        }
    )

    cli.handle_chat("@User.java 检查代码")

    prompt = str(captured["prompt"])
    assert "当前目标: F1" in prompt
    assert "优先根据 F 编号调用 get_finding_detail" in prompt
    assert captured["render_no_issue_panel"] is False


def test_cli_code_explain_skips_scan_and_uses_explain_entry(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.scope_service is not None
    assert cli.scan_service is not None
    assert cli.agent is not None

    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.CODE_EXPLAIN)
    cli.scope_service.fetch_scope = MagicMock(return_value=_scope())
    cli.scan_service.run_scan_and_persist = MagicMock()
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False, compact_observation=False, **kwargs: captured.update(
        {"agent_call": agent_call, "compact_observation": compact_observation}
    )

    cli.handle_chat("@User.java 解释一下代码")

    cli.scan_service.run_scan_and_persist.assert_not_called()
    assert captured["agent_call"] == cli.agent.perform_code_explain
    assert captured["compact_observation"] is True


def test_cli_general_chat_rejects_non_programming_topics(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.agent is not None

    cli.renderer.print_user_anchor = MagicMock()
    cli.renderer.print_assistant_anchor = MagicMock()
    cli.renderer.print_plain = MagicMock()
    cli.agent.perform_general_chat = MagicMock(return_value="unused")

    cli._handle_general_chat("番茄炒蛋怎么做")

    cli.renderer.print_user_anchor.assert_called_once_with("番茄炒蛋怎么做")
    cli.renderer.print_assistant_anchor.assert_called_once_with()
    cli.renderer.print_plain.assert_called_once()
    cli.agent.perform_general_chat.assert_not_called()


@patch.object(ContinuityJudgeService, "fetch_route", return_value=ConversationRoute.REVIEW_CONTINUE)
def test_cli_patch_revise_clears_remaining_tail_before_agent_call(
    _mock_route: MagicMock,
    tmp_path: Path,
) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.continuity_judge_service is not None
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.PATCH_REVISE)
    cli.workflow_service.persist_review_workspace(
        scope=_scope(),
        latest_scan_id="scan-1",
        patch_items=[
            _review_item("item-1", "src/main/java/demo/User.java"),
            _review_item("item-2", "src/main/java/demo/UserService.java"),
        ],
    )
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False, compact_observation=False, **kwargs: captured.update(
        {"agent_call": agent_call}
    )

    cli.handle_chat("加一句注释")

    workspace = cli.workflow_service.fetch_workspace()
    assert workspace.fetch_current_patch_item() is None
    assert workspace.patch_items == []
    assert captured["agent_call"] == cli.agent.perform_patch_revise


@patch.object(ContinuityJudgeService, "fetch_route", return_value=ConversationRoute.REVIEW_CONTINUE)
def test_cli_review_mixed_feedback_routes_to_patch_revise(
    _mock_route: MagicMock,
    tmp_path: Path,
) -> None:
    cli = _make_cli(tmp_path)
    assert cli.continuity_judge_service is not None
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.workflow_service.persist_review_workspace(
        scope=_scope(),
        latest_scan_id="scan-1",
        patch_items=[_review_item("item-1", "src/main/java/demo/User.java")],
    )
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False, compact_observation=False, **kwargs: captured.update(
        {"agent_call": agent_call, "prompt": prompt}
    )

    cli.handle_chat("加一行注释说明原因")

    assert captured["agent_call"] == cli.agent.perform_patch_revise
    assert "加一行注释说明原因" in str(captured["prompt"])


def test_cli_new_task_in_review_state_resets_review_context(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.continuity_judge_service is not None
    assert cli.scope_service is not None
    assert cli.scan_service is not None
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.workflow_service.persist_review_workspace(
        scope=_scope(),
        latest_scan_id="scan-1",
        patch_items=[_review_item("item-1", "src/main/java/demo/User.java")],
    )
    cli.agent.messages = [{"role": "user", "content": "old review"}]
    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.CODE_AUDIT)
    cli.scope_service.fetch_scope = MagicMock(return_value=_scope())
    cli.scan_service.run_scan_and_persist = MagicMock(
        return_value=(
            "scan-2",
            ScanResult(
                engine="semgrep",
                scope=["src/main/java/demo/User.java"],
                targets=["src/main/java/demo/User.java"],
                status="ok",
                message="ok",
                findings=[],
            ),
        )
    )
    cli.renderer.print_info = MagicMock()
    captured: dict[str, object] = {}
    cli._run_agent_request = lambda prompt, agent_call, scope_paths=None, render_no_issue_panel=False, compact_observation=False, **kwargs: captured.update(
        {
            "prompt": prompt,
            "agent_call": agent_call,
            "scope_paths": scope_paths,
            "render_no_issue_panel": render_no_issue_panel,
        }
    )

    with patch.object(
        ContinuityJudgeService,
        "fetch_route",
        return_value=ConversationRoute.NEW_TASK,
    ):
        cli.handle_chat("@demo 检查代码")

    assert cli.agent.messages == []
    cli.renderer.print_info.assert_called_once()
    assert captured["agent_call"] == cli.agent.perform_code_audit


def test_cli_code_audit_retries_current_finding_then_continues_remaining(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.intent_service is not None
    assert cli.scope_service is not None
    assert cli.scan_service is not None
    assert cli.workflow_service is not None
    assert cli.agent is not None

    cli.intent_service.fetch_intent = MagicMock(return_value=IntentType.CODE_AUDIT)
    cli.scope_service.fetch_scope = MagicMock(return_value=_scope())
    cli.scan_service.run_scan_and_persist = MagicMock(
        return_value=(
            "scan-1",
            ScanResult(
                engine="semgrep",
                scope=["src/main/java/demo"],
                targets=["src/main/java/demo"],
                status="ok",
                message="ok",
                findings=[
                    Finding(
                        check_id="rule-a",
                        path="src/main/java/demo/User.java",
                        start_line=6,
                        end_line=6,
                        severity="warning",
                        message="missing constructor null check",
                        snippet="this.name = name;",
                    ),
                    Finding(
                        check_id="rule-b",
                        path="src/main/java/demo/UserService.java",
                        start_line=5,
                        end_line=5,
                        severity="warning",
                        message="unsafe equals order",
                        snippet='return user.getName().equals("admin");',
                    ),
                ],
            ),
        )
    )

    run_count = {"value": 0}

    def fake_run_agent_request(
        prompt: str,
        agent_call,
        scope_paths=None,
        render_no_issue_panel: bool = False,
    ) -> list[dict[str, object]]:
        run_count["value"] += 1
        if run_count["value"] == 1:
            return [
                {
                    "role": "tool",
                    "name": "propose_patch",
                    "tool_status": "error",
                    "tool_payload": {
                        "file_path": "src/main/java/demo/User.java",
                        "associated_finding_id": "F1",
                        "error_code": "OLD_STRING_NOT_FOUND",
                        "error_message": "old string not found",
                    },
                    "content": "error",
                }
            ]
        if run_count["value"] == 2:
            cli.artifacts.persist_pending_patch(_patch_draft("src/main/java/demo/User.java", "F1"))
            return [
                {
                    "role": "tool",
                    "name": "propose_patch",
                    "tool_status": "ok",
                    "tool_payload": {
                        "file_path": "src/main/java/demo/User.java",
                        "associated_finding_id": "F1",
                    },
                    "content": "ok",
                }
            ]
        cli.artifacts.persist_pending_patch(_patch_draft("src/main/java/demo/UserService.java", "F2"))
        return [
            {
                "role": "tool",
                "name": "propose_patch",
                "tool_status": "ok",
                "tool_payload": {
                    "file_path": "src/main/java/demo/UserService.java",
                    "associated_finding_id": "F2",
                },
                "content": "ok",
            }
        ]

    cli._run_agent_request = fake_run_agent_request

    cli.handle_chat("@demo 检查代码")

    pending = cli.artifacts.fetch_pending_patches()
    assert run_count["value"] == 3
    assert len(pending) == 2
    assert [draft.file_path for draft in pending] == [
        "src/main/java/demo/UserService.java",
        "src/main/java/demo/User.java",
    ]


def test_cli_can_initialize_without_prompt_session(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.artifacts is not None
    cli.artifacts.persist_pending_patch(_patch_draft("src/main/java/demo/User.java", "F1"))

    cli.handle_init()

    assert cli.artifacts is not None
    assert cli.artifacts.fetch_pending_patch() is None


def test_run_agent_request_labels_llm_tool_calls(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.workflow_service is not None

    cli.renderer.print_tool_start = MagicMock()
    cli.renderer.print = MagicMock()

    def fake_agent_call(
        prompt: str,
        on_token=None,
        on_reasoning=None,
        on_observation=None,
        on_tool_start=None,
    ) -> str:
        assert on_tool_start is not None
        on_tool_start("read_source_code")
        return ""

    cli._run_agent_request(prompt="check", agent_call=fake_agent_call)

    cli.renderer.print_tool_start.assert_called_once_with("read_source_code", caller="LLM")


def test_run_agent_request_uses_distinct_observation_and_reasoning_rendering(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.workflow_service is not None

    cli.renderer.print_reasoning = MagicMock()
    cli.renderer.print_observation = MagicMock()
    cli.renderer.print = MagicMock()

    def fake_agent_call(
        prompt: str,
        on_token=None,
        on_reasoning=None,
        on_observation=None,
        on_tool_start=None,
    ) -> str:
        assert on_reasoning is not None
        assert on_observation is not None
        on_reasoning("思考中")
        on_observation("工具观察")
        return ""

    cli._run_agent_request(prompt="check", agent_call=fake_agent_call)

    cli.renderer.print_reasoning.assert_called_once_with("思考中", end="")
    cli.renderer.print_observation.assert_called_once_with("工具观察")


def test_run_agent_request_compacts_observation_for_code_explain(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.workflow_service is not None

    cli.renderer.print_tool_start = MagicMock()
    cli.renderer.print_info = MagicMock()
    cli.renderer.print_observation = MagicMock()
    cli.renderer.print = MagicMock()

    def fake_agent_call(
        prompt: str,
        on_token=None,
        on_reasoning=None,
        on_observation=None,
        on_tool_start=None,
    ) -> str:
        assert on_tool_start is not None
        assert on_observation is not None
        on_tool_start("read_source_code")
        on_observation("已成功加载源代码 [路径: src/main/java/demo/LegacyConfig.java]：\n\n```java\nclass A {}\n```")
        return ""

    cli._run_agent_request(prompt="check", agent_call=fake_agent_call, compact_observation=True)

    cli.renderer.print_info.assert_called_once_with("已读取: src/main/java/demo/LegacyConfig.java")
    cli.renderer.print_observation.assert_not_called()


def test_run_agent_request_uses_plain_chat_output_with_anchor(tmp_path: Path) -> None:
    cli = _make_cli(tmp_path)
    assert cli.workflow_service is not None

    cli.renderer.print_assistant_anchor = MagicMock()
    cli.renderer.print_plain = MagicMock()
    cli.renderer.print = MagicMock()

    long_markdown = (
        "## 常见解法\n"
        "1. 暴力枚举\n"
        "2. 哈希表\n"
        "```python\n"
        "def two_sum(nums, target):\n"
        "    return []\n"
        "```\n"
        "哈希表一次遍历通常是最优解，时间复杂度 O(n)。\n"
        "如果需要，我还可以继续展开完整代码。\n"
    )

    def fake_agent_call(
        prompt: str,
        on_token=None,
        on_reasoning=None,
        on_observation=None,
        on_tool_start=None,
    ) -> str:
        return long_markdown

    cli._run_agent_request(
        prompt="leetcode 第1题的解法？",
        agent_call=fake_agent_call,
        answer_intent=IntentType.GENERAL_CHAT,
        raw_user_text="leetcode 第1题的解法？",
        show_chat_anchors=True,
        plain_answer=True,
    )

    cli.renderer.print_assistant_anchor.assert_called_once_with()
    rendered_text = cli.renderer.print_plain.call_args.args[0]
    assert "##" not in rendered_text
    assert "```" not in rendered_text
    assert "如需展开，我可以继续给代码示例或逐步说明。" in rendered_text
