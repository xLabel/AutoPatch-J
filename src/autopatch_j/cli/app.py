from __future__ import annotations

import signal
import sys
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit import HTML, PromptSession

from autopatch_j.agent.agent import Agent
from autopatch_j.agent.llm_client import build_default_llm_client
from autopatch_j.cli.command_controller import CliCommandController
from autopatch_j.cli.input_controller import CliInputController
from autopatch_j.cli.render import (
    DECISION_STYLE,
    SYSTEM_STYLE,
    CliRenderer,
)
from autopatch_j.cli.services import CliContextSummary, CliServices, build_cli_services
from autopatch_j.cli.stream_adapter import StreamAdapter
from autopatch_j.cli.workflow_controller import CliWorkflowController
from autopatch_j.config import GlobalConfig, discover_repo_root
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.backlog_manager import BacklogManager
from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.input_classifier import ConversationRouter, IntentDetector
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.models import (
    CodeScope,
    IntentType,
    PatchReviewItem,
)
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.scanner_runner import ScannerRunner
from autopatch_j.core.scope_service import ScopeService
from autopatch_j.core.workspace_manager import WorkspaceManager
from autopatch_j.core.patch_verifier import PatchVerifier


class CLI:
    """
    AutoPatch-J 的命令行应用入口。

    职责边界：
    1. 持有主事件循环、欢迎界面、退出信号和 prompt 会话。
    2. 调用服务 builder 完成依赖组装，并把控制权交给 command/workflow controller。
    3. 不承载具体业务规则；扫描、意图路由、补丁队列和 ReAct 执行都由下层组件负责。
    """

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.repo_root = discover_repo_root(self.cwd)
        self.renderer = CliRenderer()

        self.artifact_manager: ArtifactManager | None = None
        self.symbol_indexer: SymbolIndexer | None = None
        self.patch_engine: PatchEngine | None = None
        self.code_fetcher: CodeFetcher | None = None
        self.patch_verifier: PatchVerifier | None = None
        self.intent_detector: IntentDetector | None = None
        self.conversation_router: ConversationRouter | None = None
        self.backlog_manager: BacklogManager | None = None
        self.chat_filter: ChatFilter | None = None
        self.scope_service: ScopeService | None = None
        self.scanner_runner: ScannerRunner | None = None
        self.workspace_manager: WorkspaceManager | None = None
        self.agent: Agent | None = None
        self.services: CliServices | None = None
        self.context_summary: CliContextSummary | None = None

        self.input_controller: CliInputController | None = None
        self.command_controller: CliCommandController | None = None
        self.workflow_controller: CliWorkflowController | None = None
        self.stream_adapter: StreamAdapter | None = None
        self.prompt_session: PromptSession[str] | None = None
        self.is_first_run: bool = False

        if self.repo_root:
            index_db_path = self.repo_root / ".autopatch-j" / "index.db"
            self.is_first_run = not index_db_path.exists()
            self._init_services(self.repo_root)

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        self.request_exit(f"\n[bold {DECISION_STYLE}]收到中断信号，正在退出...[/]")

    def _reset_agent_session(self) -> None:
        if self.agent is not None:
            self.agent.reset_history()

    def _finalize_cli_exit(self, message: str | None = None) -> None:
        self._reset_agent_session()
        if message:
            self.renderer.print(message)

    def request_exit(self, message: str | None = None) -> None:
        self._finalize_cli_exit(message)
        sys.exit(0)

    def run(self) -> int:
        if not self._ensure_prompt_session():
            return 1
        self._reset_agent_session()

        if not self.repo_root:
            self.renderer.print_panel(
                "AutoPatch-J: Java 安全与正确性修复智能体\n"
                f"{self._describe_debug_output_mode()}"
                "输入 /help 查看命令，使用 @ 符号绑定上下文。",
                title="AutoPatch-J",
                style=SYSTEM_STYLE,
            )
            self.renderer.print_info("未检测到有效目录，请进入项目目录后执行 /init")
        else:
            if self.is_first_run:
                self.renderer.print_panel(
                    f"当前项目: {self.repo_root}\n"
                    f"{self._describe_debug_output_mode()}"
                    "[bold yellow]检测到首次在本项目运行。[/]\n"
                    "👉 请在下方输入 [bold green]/init[/] 执行初始化，系统将下载扫描器规则并构建本地代码索引。",
                    title="欢迎使用 AutoPatch-J",
                    style=SYSTEM_STYLE,
                )
            else:
                stats = self.symbol_indexer.get_stats() if self.symbol_indexer else {}
                file_count = stats.get("file", 0)
                self.renderer.print_panel(
                    f"当前项目: {self.repo_root}\n"
                    f"[bold green][就绪] 已静默加载现有工作台与本地索引 (共包含 {file_count} 个项目文件)。[/]\n"
                    f"{self._describe_debug_output_mode()}"
                    f"💡 提示：若代码发生大规模变更，请使用 [bold]/reindex[/] 手动刷新 AST 缓存。\n"
                    f"输入 /help 查看命令，使用 @ 符号绑定上下文。",
                    title="AutoPatch-J",
                    style=SYSTEM_STYLE,
                )

        while True:
            try:
                workspace = self.workspace_manager.load_workspace() if self.workspace_manager else None
                current_item = workspace.get_current_patch() if workspace else None
                pending_draft = current_item.draft.fetch_patch_draft() if current_item else None
                current_idx, total_count = workspace.get_review_progress() if workspace else (0, 0)
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
                    self.workflow_controller.handle_review_input(user_input, current_item)
                else:
                    self.workflow_controller.handle_chat(user_input)

            except (EOFError, KeyboardInterrupt):
                self._finalize_cli_exit()
                break
            except Exception as exc:
                error_message = str(exc)
                if "401" in error_message or "AuthenticationError" in error_message:
                    self.renderer.print_error("LLM 认证失败 (401)，请检查 AUTOPATCH_LLM_API_KEY。")
                elif "403" in error_message or "AccessDenied" in error_message:
                    self.renderer.print_error("LLM 模型无访问权限 (403)，请检查模型权限或账户余额。")
                elif "404" in error_message or "NotFoundError" in error_message:
                    self.renderer.print_error("LLM 接口未找到 (404)，请检查 AUTOPATCH_LLM_BASE_URL 或 AUTOPATCH_LLM_MODEL。")
                else:
                    self.renderer.print_error(f"指令执行异常: {error_message}")

        return 0

    def _describe_debug_output_mode(self) -> str:
        if GlobalConfig.debug_mode:
            return "[bold green][调试模式] 显示完整思考链与工具输出详情。[/]\n"
        return ""

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
        return self.stream_adapter.run(
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

    def _build_local_no_issue_summary(self) -> str:
        assert self.context_summary is not None
        return self.context_summary.build_local_no_issue_summary()

    def _build_static_scan_summary(self) -> str:
        assert self.context_summary is not None
        return self.context_summary.build_static_scan_summary()

    def _fetch_review_scope_paths(self, current_item: PatchReviewItem) -> list[str]:
        assert self.context_summary is not None
        return self.context_summary.fetch_review_scope_paths(current_item)

    def _describe_scope_paths(self, scope: CodeScope) -> list[str]:
        assert self.context_summary is not None
        return self.context_summary.describe_scope_paths(scope)

    def _describe_current_scope_paths(self) -> list[str]:
        assert self.context_summary is not None
        return self.context_summary.describe_current_scope_paths()

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
        services = build_cli_services(repo_root, llm_factory=build_default_llm_client)
        self.services = services
        self.context_summary = services.summary
        self.artifact_manager = services.artifact_manager
        self.symbol_indexer = services.symbol_indexer
        self.patch_engine = services.patch_engine
        self.code_fetcher = services.code_fetcher
        self.patch_verifier = services.patch_verifier
        self.intent_detector = services.intent_detector
        self.backlog_manager = services.backlog_manager
        self.chat_filter = services.chat_filter
        self.conversation_router = services.conversation_router
        self.scope_service = services.scope_service
        self.scanner_runner = services.scanner_runner
        self.workspace_manager = services.workspace_manager
        self.agent = services.agent

        self.input_controller = CliInputController(
            index_search=lambda query: self.symbol_indexer.search(query) if self.symbol_indexer else [],
            repo_root=self.repo_root,
        )
        self.command_controller = CliCommandController(self)
        self.workflow_controller = CliWorkflowController(self)
        self.stream_adapter = StreamAdapter(
            renderer=self.renderer,
            workspace_manager=self.workspace_manager,
            chat_filter=self.chat_filter,
            agent=self.agent,
            describe_current_scope_paths=self._describe_current_scope_paths,
            build_static_scan_summary=self._build_static_scan_summary,
            build_local_no_issue_summary=self._build_local_no_issue_summary,
            debug_mode=lambda: GlobalConfig.debug_mode,
        )


def main() -> int:
    cli = CLI(Path.cwd())
    return cli.run()
