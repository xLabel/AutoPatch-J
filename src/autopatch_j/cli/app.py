from __future__ import annotations

import re
import signal
import sys
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit import HTML, PromptSession

from autopatch_j.agent.agent import Agent
from autopatch_j.agent.llm_client import build_default_llm_client
from autopatch_j.agent.session import AgentSession
from autopatch_j.cli.stream_adapter import StreamAdapter
from autopatch_j.cli.command_controller import CliCommandController
from autopatch_j.cli.workflow_controller import CliWorkflowController
from autopatch_j.cli.input_controller import CliInputController
from autopatch_j.cli.render import (
    DECISION_STYLE,
    MUTED_STYLE,
    SYSTEM_STYLE,
    CliRenderer,
)
from autopatch_j.config import GlobalConfig, discover_repo_root
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.backlog_manager import BacklogManager
from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.conversation_router import ConversationRouter
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.intent_detector import IntentDetector
from autopatch_j.core.models import (
    AuditFindingItem,
    CodeScope,
    CodeScopeKind,
    IntentType,
    PatchReviewItem,
)
from autopatch_j.core.patch_engine import PatchDraft, PatchEngine
from autopatch_j.core.scanner_runner import ScannerRunner
from autopatch_j.core.scope_service import ScopeService
from autopatch_j.core.workspace_manager import WorkspaceManager
from autopatch_j.core.patch_verifier import PatchVerifier
from autopatch_j.scanners import get_scanner, DEFAULT_SCANNER_NAME

DSML_MARKER_PATTERN = re.compile(r"<[^>\n]*DSML[^>\n]*>", re.IGNORECASE)


class CLI:
    """
    AutoPatch-J CLI 门面与依赖注入容器 (DI Container)。
    核心职责：
    1. 启动容器与主事件循环 (Event Loop)，拦截退出信号。
    2. 负责所有核心服务（Service、Manager、Agent 等）的实例化与组装。
    3. 驱动 prompt_toolkit 终端渲染和智能补全。
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

    def _clear_pending_patch_candidates(self) -> None:
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

        if not self.repo_root:
            self.renderer.print_panel(
                "AutoPatch-J: Java 安全与正确性修复智能体\n输入 /help 查看命令，使用 @ 符号绑定上下文。",
                title="AutoPatch-J",
                style=SYSTEM_STYLE,
            )
            self.renderer.print_info("未检测到有效目录，请进入项目目录后执行 /init")
        else:
            if self.is_first_run:
                self.renderer.print_panel(
                    f"当前项目: {self.repo_root}\n"
                    "[bold yellow]检测到首次在本项目运行。[/]\n"
                    "👉 请在下方输入 [bold green]/init[/] 执行初始化，系统将下载扫描器规则并构建本地代码索引。",
                    title="欢迎使用 AutoPatch-J",
                    style=SYSTEM_STYLE,
                )
            else:
                # _init_services is already called in __init__, but it's safe to call it or just use the services
                # Wait, we already called _init_services in __init__. So we don't need to call it again.
                stats = self.symbol_indexer.get_stats() if self.symbol_indexer else {}
                file_count = stats.get("file", 0)
                self.renderer.print_panel(
                    f"当前项目: {self.repo_root}\n"
                    f"[bold green][就绪] 已静默加载现有工作台与本地索引 (共包含 {file_count} 个项目文件)。[/]\n"
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
        assert self.workspace_manager is not None
        workspace = self.workspace_manager.load_workspace()
        if workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        return [current_item.file_path]

    def _describe_current_scope_paths(self) -> list[str]:
        workspace_manager = getattr(self, "workspace_manager", None)
        workspace = workspace_manager.load_workspace() if workspace_manager else None
        if workspace is not None and workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        scan_paths = self._collect_latest_scan_paths()
        if scan_paths:
            return scan_paths
        if self.agent and self.agent.session.focus_paths:
            return list(self.agent.session.focus_paths)
        return ["褰撳墠鑼冨洿"]

    def _collect_latest_scan_paths(self) -> list[str]:
        if not self.artifact_manager:
            return []
        scan_files = sorted(self.artifact_manager.findings_dir.glob("scan-*.json"), reverse=True)
        if not scan_files:
            return []
        latest = self.artifact_manager.load_scan_result(scan_files[0].stem)
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
        self.artifact_manager = ArtifactManager(repo_root)
        self.symbol_indexer = SymbolIndexer(repo_root, ignored_dirs=GlobalConfig.ignored_dirs)
        self.patch_engine = PatchEngine(repo_root)
        self.code_fetcher = CodeFetcher(repo_root)
        self.intent_detector = IntentDetector()
        self.backlog_manager = BacklogManager()
        self.chat_filter = ChatFilter()
        shared_llm = build_default_llm_client()
        self.conversation_router = ConversationRouter(llm=shared_llm)
        self.scope_service = ScopeService(repo_root, self.symbol_indexer, ignored_dirs=GlobalConfig.ignored_dirs)
        self.scanner_runner = ScannerRunner(repo_root, self.artifact_manager)
        self.workspace_manager = WorkspaceManager(self.artifact_manager)

        scanner = get_scanner(DEFAULT_SCANNER_NAME)
        self.patch_verifier = PatchVerifier(repo_root, scanner) if scanner else None

        agent_session = AgentSession(
            repo_root=repo_root,
            artifact_manager=self.artifact_manager,
            workspace_manager=self.workspace_manager,
            symbol_indexer=self.symbol_indexer,
            patch_engine=self.patch_engine,
            code_fetcher=self.code_fetcher,
            patch_verifier=self.patch_verifier,
        )

        self.agent = Agent(
            session=agent_session,
            llm=shared_llm,
        )

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
            sanitize_output=self._sanitize_assistant_output,
            summarize_observation=self._summarize_observation,
            describe_current_scope_paths=self._describe_current_scope_paths,
            build_static_scan_summary=self._build_static_scan_summary,
            build_local_no_issue_summary=self._build_local_no_issue_summary,
        )


def main() -> int:
    cli = CLI(Path.cwd())
    return cli.run()
