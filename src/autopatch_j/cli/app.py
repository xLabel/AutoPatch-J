from __future__ import annotations

import re
import signal
import sys
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit import HTML, PromptSession

from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.agent.llm_client import build_default_llm_client
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
        self._refresh_cli_components()
        if self.repo_root:
            self._init_services(self.repo_root)

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        self.renderer.print(f"\n[bold {DECISION_STYLE}]收到中断信号，正在退出...[/]")
        sys.exit(0)

    def _refresh_cli_components(self) -> None:
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
            prepare_display_answer=self._prepare_display_answer,
            summarize_observation=self._summarize_observation,
            describe_current_scope_paths=self._describe_current_scope_paths,
            build_static_scan_summary=self._build_static_scan_summary,
            build_local_no_issue_summary=self._build_local_no_issue_summary,
        )

    def _get_input_controller(self) -> CliInputController:
        if getattr(self, "input_controller", None) is None:
            self.input_controller = CliInputController(
                index_search=lambda query: getattr(self, "indexer", None).search(query) if getattr(self, "indexer", None) else [],
                repo_root=getattr(self, "repo_root", None),
            )
        self.input_controller.set_repo_root(getattr(self, "repo_root", None))
        return self.input_controller

    def _get_command_controller(self) -> CliCommandController:
        if getattr(self, "command_controller", None) is None:
            self.command_controller = CliCommandController(self)
        return self.command_controller

    def _get_conversation_controller(self) -> CliConversationController:
        if getattr(self, "conversation_controller", None) is None:
            self.conversation_controller = CliConversationController(self)
        return self.conversation_controller

    def _get_assistant_stream(self) -> AssistantStream:
        if getattr(self, "assistant_stream", None) is None:
            self.assistant_stream = AssistantStream(
                renderer=self.renderer,
                workflow_service=self.workflow_service,
                chat_service=self.chat_service,
                agent=self.agent,
                sanitize_output=self._sanitize_assistant_output,
                prepare_display_answer=self._prepare_display_answer,
                summarize_observation=self._summarize_observation,
                describe_current_scope_paths=self._describe_current_scope_paths,
                build_static_scan_summary=self._build_static_scan_summary,
                build_local_no_issue_summary=self._build_local_no_issue_summary,
            )
        else:
            self.assistant_stream.workflow_service = self.workflow_service
            self.assistant_stream.chat_service = self.chat_service
            self.assistant_stream.agent = self.agent
        return self.assistant_stream

    def _create_prompt_session(self) -> PromptSession[str]:
        return self._get_input_controller().create_prompt_session()

    def _ensure_prompt_session(self) -> bool:
        if self.prompt_session is not None:
            return True
        try:
            self.prompt_session = self._create_prompt_session()
            return True
        except Exception as exc:
            self.renderer.print_error(f"CLI 输入环境初始化失败: {exc}")
            return False

    def _pick_active_completion(self, buffer: Any) -> Any:
        return self._get_input_controller().pick_active_completion(buffer)

    def _accept_completion(self, buffer: Any) -> bool:
        return self._get_input_controller().accept_completion(buffer)

    def _select_first_completion(self, buffer: Any) -> bool:
        return self._get_input_controller().select_first_completion(buffer)

    def _should_append_space_after_completion(self, buffer: Any) -> bool:
        return self._get_input_controller().should_append_space_after_completion(buffer)

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
        self.agent = AutoPatchAgent(
            repo_root=repo_root,
            artifacts=self.artifacts,
            indexer=self.indexer,
            patch_engine=self.patch_engine,
            fetcher=self.fetcher,
            llm=shared_llm,
        )
        self._refresh_cli_components()

    def run(self) -> int:
        if not self._ensure_prompt_session():
            return 1

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
                    )
                    prompt_prefix = f"<style fg='{DECISION_STYLE}' font_weight='bold'>PENDING</style> autopatch-j"

                user_input = self.prompt_session.prompt(HTML(f"{prompt_prefix}> ")).strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    self.handle_command(user_input)
                    continue

                if current_item is not None:
                    self._handle_review_input(user_input, current_item)
                else:
                    self.handle_chat(user_input)

            except (EOFError, KeyboardInterrupt):
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

    def _handle_review_input(self, user_input: str, current_item: PatchReviewItem) -> None:
        self._get_conversation_controller().handle_review_input(user_input, current_item)

    def handle_chat(self, text: str) -> None:
        self._get_conversation_controller().handle_chat(text)

    def _handle_code_audit(self, text: str) -> None:
        self._get_conversation_controller().handle_code_audit(text)

    def _handle_code_explain(self, text: str) -> None:
        self._get_conversation_controller().handle_code_explain(text)

    def _handle_general_chat(self, text: str) -> None:
        self._get_conversation_controller().handle_general_chat(text)

    def _handle_patch_explain(self, text: str) -> None:
        self._get_conversation_controller().handle_patch_explain(text)

    def _handle_patch_revise(self, text: str) -> None:
        self._get_conversation_controller().handle_patch_revise(text)

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
    ) -> list[dict[str, Any]]:
        return self._get_assistant_stream().run(
            prompt=prompt,
            agent_call=agent_call,
            scope_paths=scope_paths,
            render_no_issue_panel=render_no_issue_panel,
            compact_observation=compact_observation,
            answer_intent=answer_intent,
            raw_user_text=raw_user_text,
            show_chat_anchors=show_chat_anchors,
            plain_answer=plain_answer,
        )

    def handle_command(self, raw_cmd: str) -> None:
        self._get_command_controller().handle_command(raw_cmd)

    def _sanitize_assistant_output(self, text: str) -> str:
        match = DSML_MARKER_PATTERN.search(text)
        return text[:match.start()].rstrip() if match else text

    def _prepare_display_answer(
        self,
        answer: str,
        answer_intent: IntentType | None,
        raw_user_text: str | None,
    ) -> str:
        if answer_intent is None or raw_user_text is None or self.chat_service is None:
            return answer
        return self.chat_service.build_display_answer(
            user_text=raw_user_text,
            answer=answer,
            intent=answer_intent,
        )

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

    def _build_patch_explain_prompt(self, current_item: PatchReviewItem, user_text: str) -> str:
        draft = current_item.draft
        return (
            f"当前待确认补丁文件: {current_item.file_path}\n"
            f"补丁意图: {draft.rationale or '无说明'}\n"
            f"补丁差异:\n{draft.diff}\n\n"
            f"用户问题:\n{user_text}"
        )

    def _build_patch_revise_prompt(
        self,
        current_item: PatchReviewItem,
        remaining_items: list[PatchReviewItem],
        user_text: str,
    ) -> str:
        draft = current_item.draft
        remaining_files = "\n".join(f"- {item.file_path}" for item in remaining_items)
        return (
            f"当前待重写补丁文件: {current_item.file_path}\n"
            f"当前补丁意图: {draft.rationale or '无说明'}\n"
            f"当前补丁差异:\n{draft.diff}\n\n"
            "以下补丁尾部已失效，需要基于用户反馈整体重建:\n"
            f"{remaining_files}\n\n"
            f"用户反馈:\n{user_text}\n"
            "请基于最新意见重新生成 remaining_patch_items。"
        )

    def _build_local_no_issue_summary(self) -> str:
        return "模型复核未发现需要修复的问题。"

    def _build_static_scan_summary(self) -> str:
        return "当前范围未发现安全或正确性问题。"

    def _build_code_explain_prompt(self, text: str, scope: CodeScope) -> str:
        if scope.kind is CodeScopeKind.SINGLE_FILE:
            return (
                f"当前解释范围仅限文件: {scope.focus_files[0]}\n"
                "请只基于当前文件可见内容解释代码功能，不要主动搜索、读取或推断焦点范围外的类型实现、调用方或配置来源。"
                "如果出现外部类型名，只能基于当前文件里的使用方式做保守说明。"
                "回答默认控制在 2 到 4 句；除非用户明确要求详细展开，否则不要输出分节报告。\n\n"
                f"用户问题:\n{text}"
            )

        joined_paths = "\n".join(f"- {path}" for path in scope.focus_files)
        return (
            "当前任务是代码讲解。你可以在当前焦点范围内使用 search_symbols 和 read_source_code 辅助解释，"
            "但不要越过当前 focus scope。回答默认控制在 1 段或 3 个要点以内；"
            "除非用户明确要求详细展开，否则不要输出长篇报告。\n"
            f"当前焦点范围:\n{joined_paths}\n\n"
            f"用户问题:\n{text}"
        )

    def _build_code_audit_prompt(
        self,
        text: str,
        current_finding: AuditFindingItem,
        force_reread: bool,
    ) -> str:
        lines = [
            "系统已完成本地静态扫描。你当前只允许处理一个 finding，不要切换到其他目标。",
            f"当前目标: {current_finding.finding_id}",
            f"文件位置: {current_finding.file_path}:{current_finding.start_line}",
            f"规则 ID: {current_finding.check_id}",
            f"问题描述: {current_finding.message}",
            f"代码证据:\n```java\n{current_finding.snippet}\n```",
            "",
            "执行要求:",
            f"1. 只处理 {current_finding.finding_id}，不要切换到其他 F 编号。",
            "2. 优先根据 F 编号调用 get_finding_detail 获取详情。",
            f"3. 如需漏洞详情，associated_finding_id 必须使用 {current_finding.finding_id}。",
            f"4. 如需最新源代码，可读取 {current_finding.file_path}。",
            f"5. 如形成补丁，propose_patch 时必须传 associated_finding_id={current_finding.finding_id}。",
            "6. 如果你判断当前目标不值得修复，只输出一句短结论，不要展开长篇分析。",
        ]
        if force_reread:
            lines.extend(
                [
                    "",
                    "上一次 propose_patch 因 old_string 不匹配失败。",
                    f"这一次你必须先 read_source_code({current_finding.file_path})，再重新 propose_patch。",
                ]
            )
        lines.extend(["", f"用户原始请求: {text}"])
        return "\n".join(lines)

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
        if self.agent and self.agent.focus_paths:
            return list(self.agent.focus_paths)
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

    def handle_help(self) -> None:
        self._get_command_controller().handle_help()

    def handle_scanners(self) -> None:
        self._get_command_controller().handle_scanners()

    def handle_init(self) -> None:
        self._get_command_controller().handle_init()

    def handle_status(self) -> None:
        self._get_command_controller().handle_status()

    def handle_reindex(self) -> None:
        self._get_command_controller().handle_reindex()

    def handle_apply(self, pending: PatchDraft) -> None:
        self._get_command_controller().handle_apply(pending)

    def handle_discard(self) -> None:
        self._get_command_controller().handle_discard()


def main() -> int:
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
