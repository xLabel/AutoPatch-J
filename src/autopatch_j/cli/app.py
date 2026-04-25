from __future__ import annotations

import re
import signal
import sys
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit import HTML, PromptSession

from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.agent.llm_client import build_default_llm_client
from autopatch_j.agent.session import AgentSession
from autopatch_j.cli.assistant_stream import AssistantStream
from autopatch_j.cli.command_controller import CliCommandController
from autopatch_j.cli.conversation_controller import CliConversationController
from autopatch_j.cli.input_controller import CliInputController
from autopatch_j.cli.render import (
    DECISION_STYLE,
    MUTED_STYLE,
    SYSTEM_STYLE,
    CliRenderer,
)
from autopatch_j.config import GlobalConfig, discover_repo_root
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.audit_backlog_service import AuditBacklogService
from autopatch_j.core.chat_service import ChatService
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.continuity_judge_service import ContinuityJudgeService
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.intent_service import IntentService
from autopatch_j.core.models import (
    AuditFindingItem,
    CodeScope,
    CodeScopeKind,
    IntentType,
    PatchReviewItem,
)
from autopatch_j.core.patch_engine import PatchDraft, PatchEngine
from autopatch_j.core.scan_service import ScanService
from autopatch_j.core.scope_service import ScopeService
from autopatch_j.core.workflow_service import WorkflowService
from autopatch_j.core.patch_verifier import PatchVerifier
from autopatch_j.scanners import get_scanner, DEFAULT_SCANNER_NAME

DSML_MARKER_PATTERN = re.compile(r"<[^>\n]*DSML[^>\n]*>", re.IGNORECASE)


class AutoPatchCLI:
    """
    AutoPatch-J CLI 门面。
    职责：保留交互、补全与渲染，并把任务编排委托给核心服务。
    """

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.repo_root = discover_repo_root(self.cwd)
        self.renderer = CliRenderer()

        self.artifacts: ArtifactManager | None = None
        self.indexer: IndexService | None = None
        self.patch_engine: PatchEngine | None = None
        self.fetcher: CodeFetcher | None = None
        self.patch_verifier: PatchVerifier | None = None
        self.intent_service: IntentService | None = None
        self.continuity_judge_service: ContinuityJudgeService | None = None
        self.audit_backlog_service: AuditBacklogService | None = None
        self.chat_service: ChatService | None = None
        self.scope_service: ScopeService | None = None
        self.scan_service: ScanService | None = None
        self.workflow_service: WorkflowService | None = None
        self.agent: AutoPatchAgent | None = None

        self.input_controller: CliInputController | None = None
        self.command_controller: CliCommandController | None = None
        self.conversation_controller: CliConversationController | None = None
        self.assistant_stream: AssistantStream | None = None
        self.prompt_session: PromptSession[str] | None = None

        if self.repo_root:
            self._init_services(self.repo_root)

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        self.request_exit(f"\n[bold {DECISION_STYLE}]收到中断信号，正在退出...[/]")

    def _clear_pending_patch_candidates(self) -> None:
        if self.artifacts is not None:
            self.artifacts.clear_pending_patch()
        if self.agent is not None:
            self.agent.reset_history()

    def _finalize_cli_exit(self, message: str | None = None) -> None:
        self._clear_pending_patch_candidates()
        if message:
            self.renderer.print(message)

    def request_exit(self, message: str | None = None) -> None:
        self._finalize_cli_exit(message)
        sys.exit(0)

    def run(self) -> int:
        if not self._ensure_prompt_session():
            return 1
        self._clear_pending_patch_candidates()

        self.renderer.print_panel(
            "AutoPatch-J: Java 安全与正确性修复智能体\n输入 /help 查看命令，使用 @ 符号绑定上下文。",
            title="AutoPatch-J",
            style=SYSTEM_STYLE,
        )
        if not self.repo_root:
            self.renderer.print_info("未检测到 Java 项目，请进入项目目录后执行 /init")
        else:
            self.renderer.print(
                f"[{MUTED_STYLE}]当前项目:[/] [bold {SYSTEM_STYLE}]{self.repo_root}[/]"
            )

        while True:
            try:
                workspace = self.workflow_service.fetch_workspace() if self.workflow_service else None
                current_item = workspace.fetch_current_patch_item() if workspace else None
                pending_draft = current_item.draft.fetch_patch_draft() if current_item else None
                current_idx, total_count = workspace.fetch_review_progress() if workspace else (0, 0)
                prompt_prefix = "autopatch-j"

                if pending_draft:
                    self.renderer.print_diff(pending_draft.diff, title=f" 预览: {pending_draft.file_path} ")
                    self.renderer.print_action_panel(
                        file_path=pending_draft.file_path,
                        diff=pending_draft.diff,
                        validation=pending_draft.validation.status,
                        rationale=pending_draft.rationale or "无说明",
                        current_idx=current_idx,
                        total_count=total_count,
                        source_hint=pending_draft.source_hint,
                    )
                    prompt_prefix = f"<style fg='{DECISION_STYLE}' font_weight='bold'>PENDING</style> autopatch-j"

                user_input = self.prompt_session.prompt(HTML(f"{prompt_prefix}> ")).strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    self.command_controller.handle_command(user_input)
                    continue

                if current_item is not None:
                    self.conversation_controller.handle_review_input(user_input, current_item)
                else:
                    self.conversation_controller.handle_chat(user_input)

            except (EOFError, KeyboardInterrupt):
                self._finalize_cli_exit()
                break
            except Exception as exc:
                error_message = str(exc)
                if "401" in error_message or "AuthenticationError" in error_message:
                    self.renderer.print_error("LLM 认证失败 (401)，请检查 LLM_API_KEY。")
                elif "403" in error_message or "AccessDenied" in error_message:
                    self.renderer.print_error("LLM 模型无访问权限 (403)，请检查模型权限或账户余额。")
                elif "404" in error_message or "NotFoundError" in error_message:
                    self.renderer.print_error("LLM 接口未找到 (404)，请检查 LLM_BASE_URL 或 LLM_MODEL。")
                else:
                    self.renderer.print_error(f"指令执行异常: {error_message}")

        return 0

    def _run_agent_request(
        self,
        prompt: str,
        agent_call: Callable[..., str],
        scope_paths: list[str] | None = None,
        render_no_issue_panel: bool = False,
        compact_observation: bool = False,
        answer_intent: IntentType | None = None,
        raw_user_text: str | None = None,
        show_chat_anchors: bool = False,
        plain_answer: bool = False,
        suppress_answer_output: bool = False,
    ) -> list[dict[str, Any]]:
        return self.assistant_stream.run(
            prompt=prompt,
            agent_call=agent_call,
            scope_paths=scope_paths,
            render_no_issue_panel=render_no_issue_panel,
            compact_observation=compact_observation,
            answer_intent=answer_intent,
            raw_user_text=raw_user_text,
            show_chat_anchors=show_chat_anchors,
            plain_answer=plain_answer,
            suppress_answer_output=suppress_answer_output,
        )

    def _sanitize_assistant_output(self, text: str) -> str:
        match = DSML_MARKER_PATTERN.search(text)
        return text[:match.start()].rstrip() if match else text

    def _should_show_full_tool_output(self, text: str) -> bool:
        hints = (
            "展示代码",
            "显示代码",
            "贴出代码",
            "展示源码",
            "显示源码",
            "详细过程",
            "完整过程",
            "工具结果",
            "原始输出",
            "完整输出",
            "逐步过程",
        )
        compact = re.sub(r"\s+", "", text)
        return any(hint in compact for hint in hints)

    def _summarize_observation(self, tool_name: str | None, message: str) -> str:
        if tool_name == "read_source_code":
            match = re.search(r"\[[^:\]]+:\s*([^\]]+)\]", message)
            if match:
                return f"已读取: {match.group(1)}"
            return "已读取源代码"

        if tool_name == "search_symbols":
            match = re.search(r"['‘]?([^'’]+)['’]?\s*相关", message)
            if match:
                return f"已定位符号: {match.group(1)}"
            return "已定位相关符号"

        if tool_name == "get_finding_detail":
            match = re.search(r"\((F\d+)\)", message)
            if match:
                return f"已获取 finding 详情: {match.group(1)}"
            return "已获取 finding 详情"

        first_line = message.strip().splitlines()[0] if message.strip() else ""
        if first_line:
            return first_line
        if tool_name:
            return f"已完成工具: {tool_name}"
        return "已更新工具结果"

    def _build_local_no_issue_summary(self) -> str:
        return "模型复核未发现需要修复的问题。"

    def _build_static_scan_summary(self) -> str:
        return "当前范围未发现安全或正确性问题。"

    def _fetch_review_scope_paths(self, current_item: PatchReviewItem) -> list[str]:
        assert self.workflow_service is not None
        workspace = self.workflow_service.fetch_workspace()
        if workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        return [current_item.file_path]

    def _describe_scope_paths(self, scope: CodeScope) -> list[str]:
        if scope.kind.value == "project":
            return list(scope.focus_files)
        return list(scope.focus_files)

    def _describe_current_scope_paths(self) -> list[str]:
        workflow_service = getattr(self, "workflow_service", None)
        workspace = workflow_service.fetch_workspace() if workflow_service else None
        if workspace is not None and workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        scan_paths = self._collect_latest_scan_paths()
        if scan_paths:
            return scan_paths
        if self.agent and self.agent.session.focus_paths:
            return list(self.agent.session.focus_paths)
        return ["褰撳墠鑼冨洿"]

    def _collect_latest_scan_paths(self) -> list[str]:
        if not self.artifacts:
            return []
        scan_files = sorted(self.artifacts.findings_dir.glob("scan-*.json"), reverse=True)
        if not scan_files:
            return []
        latest = self.artifacts.fetch_scan_result(scan_files[0].stem)
        if latest is None:
            return []

        resolved: list[str] = []
        for target in latest.targets:
            normalized = str(target).replace("\\", "/")
            candidate = (self.repo_root / normalized).resolve()
            if candidate.is_dir():
                for java_file in sorted(candidate.rglob("*.java")):
                    rel_path = java_file.relative_to(self.repo_root).as_posix()
                    if rel_path not in resolved:
                        resolved.append(rel_path)
            else:
                if normalized not in resolved:
                    resolved.append(normalized)
        return resolved

    def _ensure_prompt_session(self) -> bool:
        if self.prompt_session is not None:
            return True
        try:
            self.prompt_session = self.input_controller.create_prompt_session()
            return True
        except Exception as exc:
            self.renderer.print_error(f"CLI 输入环境初始化失败: {exc}")
            return False

    def _init_services(self, repo_root: Path) -> None:
        self.artifacts = ArtifactManager(repo_root)
        self.indexer = IndexService(repo_root, ignored_dirs=GlobalConfig.ignored_dirs)
        self.patch_engine = PatchEngine(repo_root)
        self.fetcher = CodeFetcher(repo_root)
        self.intent_service = IntentService()
        self.audit_backlog_service = AuditBacklogService()
        self.chat_service = ChatService()
        shared_llm = build_default_llm_client()
        self.continuity_judge_service = ContinuityJudgeService(llm=shared_llm)
        self.scope_service = ScopeService(repo_root, self.indexer, ignored_dirs=GlobalConfig.ignored_dirs)
        self.scan_service = ScanService(repo_root, self.artifacts)
        self.workflow_service = WorkflowService(self.artifacts)

        scanner = get_scanner(DEFAULT_SCANNER_NAME)
        self.patch_verifier = PatchVerifier(repo_root, scanner) if scanner else None

        agent_session = AgentSession(
            repo_root=repo_root,
            artifacts=self.artifacts,
            indexer=self.indexer,
            patch_engine=self.patch_engine,
            fetcher=self.fetcher,
            patch_verifier=self.patch_verifier,
        )

        self.agent = AutoPatchAgent(
            session=agent_session,
            llm=shared_llm,
        )

        self.input_controller = CliInputController(
            index_search=lambda query: self.indexer.search(query) if self.indexer else [],
            repo_root=self.repo_root,
        )
        self.command_controller = CliCommandController(self)
        self.conversation_controller = CliConversationController(self)
        self.assistant_stream = AssistantStream(
            renderer=self.renderer,
            workflow_service=self.workflow_service,
            chat_service=self.chat_service,
            agent=self.agent,
            sanitize_output=self._sanitize_assistant_output,
            summarize_observation=self._summarize_observation,
            describe_current_scope_paths=self._describe_current_scope_paths,
            build_static_scan_summary=self._build_static_scan_summary,
            build_local_no_issue_summary=self._build_local_no_issue_summary,
        )


def main() -> int:
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
