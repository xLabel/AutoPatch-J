from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import cast

try:
    import readline
except ImportError:  # pragma: no cover - platform dependent
    readline = None

from autopatch_j.artifacts import (
    load_scan_result,
    load_validation_result,
    save_scan_result,
    save_validation_result,
)
from autopatch_j.context import build_context_preview, build_mention_context_text
from autopatch_j.edit_drafter import (
    DraftedEdit,
    RepairingEditDrafter,
    build_default_edit_drafter,
)
from autopatch_j.indexer import IndexEntry, summarize_index
from autopatch_j.mentions import (
    MentionResolution,
    ParsedPrompt,
    build_mention_completions,
    parse_prompt,
)
from autopatch_j.project import (
    ProjectSummary,
    discover_repo_root,
    initialize_project,
    load_project,
    refresh_project_index,
)
from autopatch_j.planner import AgentAction, AgentDecision, DecisionContext, build_default_planner
from autopatch_j.readiness import ReadinessReport, build_readiness_report as build_readiness_snapshot
from autopatch_j.scanners import (
    ALL_SCANNERS,
    DEFAULT_SCANNER_NAME,
    JavaScanner,
    ScanResult,
    get_scanner,
)
from autopatch_j.scanners.semgrep import install_managed_semgrep_runtime
from autopatch_j.session import PendingEdit, SessionState, save_session
from autopatch_j.tools import ToolExecutionResult, ToolName, build_tools, execute_tool
from autopatch_j.tools.edit import EditPreview
from autopatch_j.validators import (
    RescanValidationResult,
    SyntaxValidationResult,
    validate_post_apply_rescan,
)

HELP_TEXT = """Commands:
  /init         Initialize the current repository for AutoPatch-J
  /status       Show project readiness and current work summary
  /scanners     Show Java static scanner choices
  /reindex       Refresh repository index for @mention and scope lookup
  /help          Show this message
  /quit          Exit the CLI

Prompt rules:
  - Use @path or @filename to bind scope, for example:
      @src/main/java/com/foo/UserService.java scan this file
  - Press Tab after typing @query to autocomplete candidate paths.
  - After findings are ready, you can say:
      列出问题
      修复第1个问题
      @src/main/java/com/foo/UserService.java 生成 patch
  - After a patch is drafted, choose exactly one option:
      apply
      discard
  - Ambiguous mentions will show candidate paths for selection.
"""


class AutoPatchCLI:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.repo_root = discover_repo_root(self.cwd)
        self.session = SessionState()
        self.index: list[IndexEntry] = []
        self._completion_matches: list[str] = []
        self.planner = build_default_planner()
        self.edit_drafter = build_default_edit_drafter()
        if self.repo_root is not None:
            self.session, self.index = load_project(self.repo_root)
            if self.session.repo_root is None:
                self.session.repo_root = str(self.repo_root)
        self.rebuild_scanner()
        self.tools = build_tools(scanner=self.scanner)

    def run(self) -> int:
        self.configure_readline()
        print("AutoPatch-J CLI")
        if self.repo_root is not None:
            print(f"Loaded project: {self.repo_root}")
        else:
            print("No project initialized yet. Run /init to start.")

        while True:
            try:
                raw = input("autopatch-j> ").strip()
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print()
                continue

            if not raw:
                continue
            if raw in {"/quit", "/exit"}:
                return 0

            response = self.handle_line(raw)
            if response:
                print(response)

    def configure_readline(self) -> None:
        if readline is None:
            return

        try:
            binding = "tab: complete"
            if "libedit" in (getattr(readline, "__doc__", "") or ""):
                binding = "bind ^I rl_complete"
            readline.parse_and_bind(binding)
            delimiters = readline.get_completer_delims()
            for char in "@/.-":
                delimiters = delimiters.replace(char, "")
            readline.set_completer_delims(delimiters)
            readline.set_completer(self.complete_input)
        except Exception:
            return

    def complete_input(self, text: str, state: int) -> str | None:
        if state == 0:
            recent_paths = self.session.recent_mentions or self.session.active_scope
            self._completion_matches = build_mention_completions(
                index=self.index,
                token=text,
                recent_paths=recent_paths,
            )

        if state >= len(self._completion_matches):
            return None
        return self._completion_matches[state]

    def handle_line(self, raw: str) -> str:
        if raw.startswith("/"):
            return self.handle_command(raw)

        if self.repo_root is None:
            return "No active project. Run /init before entering prompts."

        menu_response = self.handle_pending_menu_choice(raw)
        if menu_response is not None:
            return menu_response

        parsed = parse_prompt(raw, self.index)
        if not self.resolve_mentions_interactively(parsed):
            return "Prompt cancelled."

        self.update_session_from_prompt(parsed)
        mention_context = build_mention_context_text(self.repo_root, parsed)

        preview = build_context_preview(self.repo_root, parsed)
        stream_started = False

        def stream_answer_delta(delta: str) -> None:
            nonlocal stream_started
            if not delta:
                return
            if not stream_started:
                if preview:
                    print(preview)
                    print()
                stream_started = True
            print(delta, end="", flush=True)

        decision = self.planner.decide(
            DecisionContext(
                user_text=parsed.clean_text,
                scoped_paths=self.effective_scope(parsed),
                has_active_findings=self.session.active_findings_id is not None,
                mention_context=mention_context,
                on_answer_delta=stream_answer_delta,
            )
        )

        response = self.handle_agent_decision(parsed, preview, decision)
        if decision.streamed and stream_started:
            print()
        save_session(self.repo_root, self.session)
        return response

    def handle_command(self, raw: str) -> str:
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            return f"Command parse error: {exc}"

        command = parts[0]
        if command == "/help":
            return HELP_TEXT
        if command == "/scanners":
            return self.handle_scanners()
        if command == "/reindex":
            return self.handle_reindex()
        if command == "/status":
            return self.format_status()
        if command == "/init":
            if len(parts) != 1:
                return (
                    "/init only initializes the current directory in v1. "
                    "Please cd into the target repository first, then run /init."
                )
            return self.handle_init()
        return f"Unknown command: {command}\n\n{HELP_TEXT}"

    def handle_init(self) -> str:
        session, index, summary = initialize_project(self.cwd)
        self.repo_root = Path(summary.repo_root)
        self.session = session
        self.index = index
        runtime_status, runtime_message = self.ensure_semgrep_runtime()
        self.rebuild_scanner()
        self.tools = build_tools(scanner=self.scanner)
        return (
            f"{format_init_summary(summary)}\n\n"
            "Scanner runtime:\n"
            f"- semgrep: {runtime_status}\n"
            f"  {runtime_message}"
        )

    def ensure_semgrep_runtime(self) -> tuple[str, str]:
        try:
            return install_managed_semgrep_runtime()
        except Exception as exc:
            return (
                "missing",
                (
                    "AutoPatch-J managed Semgrep was not installed. "
                    f"Check network access, then run /init again. Error: {exc}"
                ),
            )

    def handle_reindex(self) -> str:
        if self.repo_root is None:
            return "No active project. Run /init to create AutoPatch-J state."

        self.index, summary = refresh_project_index(self.repo_root)
        return format_reindex_summary(summary)

    def build_readiness_report(self) -> ReadinessReport:
        return build_readiness_snapshot(
            repo_root=self.repo_root,
            scanner=self.scanner,
            planner_label=self.planner.label,
            edit_drafter_label=self.edit_drafter.label if self.edit_drafter else None,
        )

    def handle_scanners(self) -> str:
        return format_scanners_report(ALL_SCANNERS, self.repo_root)

    def format_status(self) -> str:
        if self.repo_root is None:
            return (
                "AutoPatch-J status:\n"
                "- project: not initialized\n"
                "- next: run /init from the Java repository root\n\n"
                f"{format_readiness_report(self.build_readiness_report())}"
            )

        summary = summarize_index(self.index)
        active_findings = self.format_active_findings_status()
        pending_patch = (
            f"{self.session.pending_edit.file_path} "
            f"({self.session.pending_edit.validation_status})"
            if self.session.pending_edit
            else "(none)"
        )
        last_validation = self.format_last_validation_status()
        return (
            "AutoPatch-J status:\n"
            f"- project: {self.repo_root}\n"
            f"- index: {summary['entries']} entries, {summary['java_files']} Java files\n"
            f"- active findings: {active_findings}\n"
            f"- pending patch: {pending_patch}\n"
            f"- last validation: {last_validation}\n\n"
            f"{format_readiness_report(self.build_readiness_report())}"
        )

    def format_active_findings_status(self) -> str:
        if self.repo_root is None or self.session.active_findings_id is None:
            return "(none)"
        result = load_scan_result(self.repo_root, self.session.active_findings_id)
        if result is None:
            return "saved scan artifact is unavailable"
        return f"{len(result.findings)} finding(s), status {result.status}"

    def format_last_validation_status(self) -> str:
        if self.repo_root is None or self.session.last_validation_id is None:
            return "(none)"
        result = load_validation_result(self.repo_root, self.session.last_validation_id)
        if result is None:
            return "saved validation artifact is unavailable"
        return result.status

    def store_pending_from_draft(
        self,
        drafted: DraftedEdit,
        preview: EditPreview | None = None,
        retry_note: str | None = None,
        source_artifact_id: str | None = None,
        source_finding_index: int | None = None,
        source_check_id: str | None = None,
    ) -> str:
        resolved_preview = preview or self.preview_drafted_edit(drafted)
        header = [
            "Drafted edit:",
            f"- file: {drafted.file_path}",
            f"- rationale: {drafted.rationale or '(none)'}",
        ]
        if retry_note:
            header.append(f"- retry: {retry_note}")
        body = self.store_pending_from_preview(
            file_path=drafted.file_path,
            old_string=drafted.old_string,
            new_string=drafted.new_string,
            preview=resolved_preview,
            prefix="Pending edit updated from draft.",
            rationale=drafted.rationale,
            source_artifact_id=source_artifact_id,
            source_finding_index=source_finding_index,
            source_check_id=source_check_id,
        )
        return "\n".join(header + ["", body])

    def store_pending_from_preview(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        preview: EditPreview,
        prefix: str,
        rationale: str | None = None,
        source_artifact_id: str | None = None,
        source_finding_index: int | None = None,
        source_check_id: str | None = None,
    ) -> str:
        if preview.status == "ok":
            self.session.pending_edit = PendingEdit(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
                diff=preview.diff,
                validation_status=preview.validation.status,
                validation_message=preview.validation.message,
                rationale=rationale,
                source_artifact_id=source_artifact_id,
                source_finding_index=source_finding_index,
                source_check_id=source_check_id,
            )
            self.session.current_goal = "review_pending_edit"
            save_session(self.repo_root, self.session)
            return append_pending_patch_menu(format_edit_preview(preview, prefix=prefix))

        self.session.pending_edit = None
        save_session(self.repo_root, self.session)
        return format_edit_preview(preview, prefix="Pending edit cleared because preview failed.")

    def handle_pending_menu_choice(self, raw: str) -> str | None:
        choice = raw.strip().lower()
        if choice == "apply":
            return self.handle_apply_pending()
        if choice == "discard":
            return self.handle_discard_pending()
        return None

    def handle_apply_pending(self) -> str:
        if self.repo_root is None:
            return "No active project. Run /init to create AutoPatch-J state."
        pending = self.session.pending_edit
        if pending is None:
            return "No pending edit to apply."

        execution = execute_tool(
            repo_root=self.repo_root,
            tool_name=ToolName.APPLY_SEARCH_REPLACE,
            tool_args={
                "file_path": pending.file_path,
                "old_string": pending.old_string,
                "new_string": pending.new_string,
            },
            tools=self.tools,
        )
        preview = self.extract_edit_preview(execution, pending.file_path)
        if preview.status == "ok":
            rescan_output = self.run_post_apply_rescan(pending)
            self.session.pending_edit = None
            self.session.current_goal = "pending_edit_applied"
            save_session(self.repo_root, self.session)
            return (
                f"{format_edit_preview(preview, prefix='Pending edit applied.')}\n\n"
                f"{rescan_output}"
            )

        save_session(self.repo_root, self.session)
        return format_edit_preview(preview, prefix="Pending edit was not applied.")

    def handle_discard_pending(self) -> str:
        if self.repo_root is None:
            return "No active project. Run /init to create AutoPatch-J state."
        if self.session.pending_edit is None:
            return "No pending edit to discard."
        self.session.pending_edit = None
        save_session(self.repo_root, self.session)
        return "Pending patch discarded."

    def handle_agent_decision(
        self,
        parsed: ParsedPrompt,
        preview: str,
        decision: AgentDecision,
    ) -> str:
        if decision.action == AgentAction.SCAN:
            execution = self.run_tool(decision)
            result = self.extract_scan_result(execution)
            self.apply_scan_result(result)
            return f"{preview}\n\n{format_scan_result(result)}"
        if decision.action == AgentAction.PATCH:
            body = self.handle_planned_patch(parsed, decision)
            return self.render_prompt_response(parsed, body)
        if decision.streamed:
            return ""
        return f"{preview}\n\n{decision.message}"

    def handle_planned_patch(self, parsed: ParsedPrompt, decision: AgentDecision) -> str:
        if self.session.pending_edit is not None:
            return self.handle_pending_patch_revision(parsed)
        artifact_id = self.session.active_findings_id
        if not artifact_id:
            return (
                "No active findings are available. Scan the repository first, "
                "then ask AutoPatch-J to draft a patch."
            )
        if self.edit_drafter is None:
            return (
                "Edit drafter is disabled. Set LLM_API_KEY or OPENAI_API_KEY "
                "to enable patch drafting."
            )

        result = load_scan_result(self.repo_root, artifact_id)
        if result is None:
            return f"Findings artifact not found: {artifact_id}"

        finding_index = extract_planned_finding_index(decision.tool_args)
        if finding_index is not None:
            if finding_index < 1 or finding_index > len(result.findings):
                return (
                    f"Requested finding index is out of range: {finding_index}. "
                    f"Available findings: 1..{len(result.findings)}"
                )
            selected: tuple[int, object] | str = (finding_index, result.findings[finding_index - 1])
        else:
            selected = self.select_patch_finding(parsed, result)
        if isinstance(selected, str):
            return selected

        finding_position, _ = selected
        mention_context = build_mention_context_text(self.repo_root, parsed)
        return self.draft_fix_for_finding(
            result,
            artifact_id,
            finding_position,
            user_request=parsed.clean_text,
            mention_context=mention_context,
        )

    def handle_pending_patch_revision(self, parsed: ParsedPrompt) -> str:
        pending = self.session.pending_edit
        if pending is None:
            return "No pending edit to revise."
        if self.edit_drafter is None:
            return (
                "Edit drafter is disabled. Set LLM_API_KEY or OPENAI_API_KEY "
                "to revise pending patches."
            )

        repairing_drafter = self.repairing_edit_drafter()
        if repairing_drafter is None:
            return "The configured edit drafter cannot revise pending patches."

        target = (self.repo_root / pending.file_path).resolve()
        try:
            target.relative_to(self.repo_root.resolve())
        except ValueError:
            return f"Pending patch path is outside the repository: {pending.file_path}"
        if not target.exists() or not target.is_file():
            return f"Pending patch file does not exist: {pending.file_path}"

        file_content = read_text(target)
        previous_edit = DraftedEdit(
            file_path=pending.file_path,
            old_string=pending.old_string,
            new_string=pending.new_string,
            rationale=pending.rationale or "",
        )
        mention_context = build_mention_context_text(self.repo_root, parsed)
        instruction = self.build_revision_instruction(
            pending=pending,
            user_request=parsed.clean_text,
            mention_context=mention_context,
        )
        feedback = self.build_revision_feedback(
            pending=pending,
            user_request=parsed.clean_text,
            mention_context=mention_context,
        )

        try:
            drafted = repairing_drafter.redraft_edit(
                file_path=pending.file_path,
                instruction=instruction,
                file_content=file_content,
                previous_edit=previous_edit,
                feedback=feedback,
            )
        except Exception as exc:
            return f"Patch revision failed. Existing pending patch kept.\n- error: {exc}"

        preview = self.preview_drafted_edit(drafted)
        if self.should_retry_draft(preview, drafted.file_path):
            retry_feedback = self.build_draft_retry_feedback(preview, drafted.file_path)
            try:
                drafted = repairing_drafter.redraft_edit(
                    file_path=pending.file_path,
                    instruction=instruction,
                    file_content=file_content,
                    previous_edit=drafted,
                    feedback=retry_feedback,
                )
                preview = self.preview_drafted_edit(drafted)
            except Exception as exc:
                return (
                    "Patch revision retry failed. Existing pending patch kept.\n"
                    f"- retry feedback: {retry_feedback}\n"
                    f"- error: {exc}"
                )

        if preview.status != "ok":
            return append_pending_patch_menu(
                format_edit_preview(
                    preview,
                    prefix="Revised patch preview failed. Existing pending patch kept.",
                )
            )

        return self.store_pending_from_draft(
            drafted,
            preview=preview,
            retry_note="Revised from user feedback.",
            source_artifact_id=pending.source_artifact_id,
            source_finding_index=pending.source_finding_index,
            source_check_id=pending.source_check_id,
        )

    def build_revision_instruction(
        self,
        pending: PendingEdit,
        user_request: str | None,
        mention_context: str | None,
    ) -> str:
        base = "Revise the current pending search-replace edit according to the user feedback."
        if pending.source_artifact_id and pending.source_finding_index:
            result = load_scan_result(self.repo_root, pending.source_artifact_id)
            if result is not None and 1 <= pending.source_finding_index <= len(result.findings):
                finding = result.findings[pending.source_finding_index - 1]
                base = build_finding_instruction(
                    finding,
                    user_request=user_request,
                    mention_context=mention_context,
                )
        return (
            f"{base}\n"
            "Revision constraints:\n"
            "- Keep a single minimal search-replace edit.\n"
            "- Do not introduce new dependencies unless the user explicitly asks.\n"
            "- Preserve the original finding provenance when possible.\n"
        )

    def build_revision_feedback(
        self,
        pending: PendingEdit,
        user_request: str | None,
        mention_context: str | None,
    ) -> str:
        return (
            f"user_feedback: {user_request or '(none)'}\n"
            f"mention_context:\n{mention_context or '(none)'}\n\n"
            "current_pending_patch:\n"
            f"- file: {pending.file_path}\n"
            f"- validation_status: {pending.validation_status}\n"
            f"- validation_message: {pending.validation_message}\n"
            f"- rationale: {pending.rationale or '(none)'}\n"
            f"- source_artifact: {pending.source_artifact_id or '(none)'}\n"
            f"- source_finding_index: {pending.source_finding_index or '(none)'}\n"
            f"- source_check_id: {pending.source_check_id or '(none)'}\n"
            "diff:\n"
            f"{pending.diff}\n"
        )

    def select_patch_finding(
        self,
        parsed: ParsedPrompt,
        result: ScanResult,
    ) -> tuple[int, object] | str:
        requested_index = extract_requested_finding_index(parsed.clean_text)
        if requested_index is not None:
            if requested_index < 1 or requested_index > len(result.findings):
                return (
                    f"Requested finding index is out of range: {requested_index}. "
                    f"Available findings: 1..{len(result.findings)}"
                )
            return requested_index, result.findings[requested_index - 1]

        scoped_candidates = [
            (index, finding)
            for index, finding in enumerate(result.findings, start=1)
            if self.finding_matches_prompt_scope(parsed, finding.path)
        ]
        if parsed.mentions:
            if not scoped_candidates:
                return "No active findings matched the current @mention scope."
            if len(scoped_candidates) == 1:
                return scoped_candidates[0]
            return format_finding_candidates(
                scoped_candidates,
                prefix=(
                    "Multiple active findings matched the current @mention scope. "
                    "Specify one with a number such as '修复第2个问题'."
                ),
            )

        if len(result.findings) == 1:
            return 1, result.findings[0]

        return format_finding_candidates(
            list(enumerate(result.findings, start=1)),
            prefix=(
                "Multiple active findings are available. Specify one with a number "
                "such as '修复第2个问题', or narrow the scope with @mention."
            ),
        )

    def finding_matches_prompt_scope(self, parsed: ParsedPrompt, finding_path: str) -> bool:
        if not parsed.mentions:
            return True

        for resolution in parsed.mentions:
            entry = resolution.selected
            if entry is None:
                continue
            if entry.kind == "file" and finding_path == entry.path:
                return True
            if entry.kind == "dir":
                prefix = f"{entry.path.rstrip('/')}/"
                if finding_path == entry.path or finding_path.startswith(prefix):
                    return True
        return False

    def draft_fix_for_finding(
        self,
        result: ScanResult,
        artifact_id: str,
        finding_position: int,
        user_request: str | None = None,
        mention_context: str | None = None,
    ) -> str:
        finding = result.findings[finding_position - 1]
        target = (self.repo_root / finding.path).resolve()
        try:
            target.relative_to(self.repo_root.resolve())
        except ValueError:
            return f"Finding path is outside the repository: {finding.path}"
        if not target.exists() or not target.is_file():
            return f"Finding file does not exist: {finding.path}"

        file_content = read_text(target)
        instruction = build_finding_instruction(
            finding,
            user_request=user_request,
            mention_context=mention_context,
        )
        header = [
            "Draft fix context:",
            f"- artifact: {artifact_id}",
            f"- finding index: {finding_position}",
            f"- rule: {finding.check_id}",
            f"- severity: {finding.severity}",
            f"- message: {finding.message}",
        ]
        body = self.draft_pending_edit(
            file_path=finding.path,
            instruction=instruction,
            file_content=file_content,
            source_artifact_id=artifact_id,
            source_finding_index=finding_position,
            source_check_id=finding.check_id,
        )
        return "\n".join(header + ["", body])

    def draft_pending_edit(
        self,
        file_path: str,
        instruction: str,
        file_content: str,
        source_artifact_id: str | None = None,
        source_finding_index: int | None = None,
        source_check_id: str | None = None,
    ) -> str:
        assert self.edit_drafter is not None
        try:
            drafted = self.edit_drafter.draft_edit(file_path, instruction, file_content)
        except Exception as exc:
            return f"Draft edit failed: {exc}"

        preview = self.preview_drafted_edit(drafted)
        retry_note: str | None = None
        repairing_drafter = self.repairing_edit_drafter()
        if repairing_drafter is not None and self.should_retry_draft(preview, drafted.file_path):
            retry_feedback = self.build_draft_retry_feedback(preview, drafted.file_path)
            retry_note = f"Applied one repair retry after: {retry_feedback}"
            try:
                drafted = repairing_drafter.redraft_edit(
                    file_path=file_path,
                    instruction=instruction,
                    file_content=file_content,
                    previous_edit=drafted,
                    feedback=retry_feedback,
                )
                preview = self.preview_drafted_edit(drafted)
            except Exception as exc:
                retry_note = f"{retry_note}\n- retry error: {exc}"

        return self.store_pending_from_draft(
            drafted,
            preview=preview,
            retry_note=retry_note,
            source_artifact_id=source_artifact_id,
            source_finding_index=source_finding_index,
            source_check_id=source_check_id,
        )

    def render_prompt_response(self, parsed: ParsedPrompt, body: str) -> str:
        if not parsed.mentions:
            return body
        preview = build_context_preview(self.repo_root, parsed)
        return f"{preview}\n\n{body}"

    def resolve_mentions_interactively(self, parsed: ParsedPrompt) -> bool:
        for resolution in parsed.mentions:
            if resolution.status == "resolved":
                continue
            if resolution.status == "missing":
                print(f"No path matched {resolution.raw}.")
                return False
            if resolution.status == "ambiguous":
                selected = self.prompt_for_candidate(resolution)
                if selected is None:
                    return False
                resolution.selected = selected
                resolution.status = "resolved"
        return True

    def prompt_for_candidate(self, resolution: MentionResolution) -> IndexEntry | None:
        print(f"{resolution.raw} matched multiple paths:")
        for idx, candidate in enumerate(resolution.candidates, start=1):
            print(f"  {idx}. {candidate.entry.path} ({candidate.entry.kind}, score={candidate.score})")

        while True:
            choice = input("Select a candidate number, or press Enter to cancel: ").strip()
            if not choice:
                return None
            if not choice.isdigit():
                print("Please enter a number.")
                continue
            index = int(choice)
            if 1 <= index <= len(resolution.candidates):
                return resolution.candidates[index - 1].entry
            print("Choice out of range.")

    def update_session_from_prompt(self, parsed: ParsedPrompt) -> None:
        resolved_paths = [
            resolution.selected.path
            for resolution in parsed.mentions
            if resolution.selected is not None
        ]

        if resolved_paths:
            self.session.active_scope = resolved_paths
            merged_mentions = resolved_paths + self.session.recent_mentions
            deduped: list[str] = []
            for path in merged_mentions:
                if path not in deduped:
                    deduped.append(path)
            self.session.recent_mentions = deduped[:10]
        self.session.current_goal = parsed.clean_text or self.session.current_goal

    def effective_scope(self, parsed: ParsedPrompt) -> list[str]:
        return [
            resolution.selected.path
            for resolution in parsed.mentions
            if resolution.selected is not None
        ]

    def run_tool(self, decision: AgentDecision) -> ToolExecutionResult:
        return execute_tool(
            repo_root=self.repo_root,
            tool_name=decision.tool_name or "",
            tool_args=dict(decision.tool_args),
            tools=self.tools,
        )

    def extract_scan_result(self, execution: ToolExecutionResult) -> ScanResult:
        if isinstance(execution.payload, ScanResult):
            return execution.payload

        return ScanResult(
            engine="autopatch-j",
            scope=[],
            targets=[],
            status="error",
            message=execution.message,
            summary={"total": 0},
            findings=[],
        )

    def extract_edit_preview(self, execution: ToolExecutionResult, file_path: str) -> EditPreview:
        if isinstance(execution.payload, EditPreview):
            return execution.payload

        return EditPreview(
            file_path=file_path,
            status="error",
            message=execution.message,
            occurrences=0,
            diff="",
            validation=self.build_default_preview_validation(),
        )

    def preview_drafted_edit(self, drafted: DraftedEdit) -> EditPreview:
        execution = execute_tool(
            repo_root=self.repo_root,
            tool_name=ToolName.PREVIEW_SEARCH_REPLACE,
            tool_args={
                "file_path": drafted.file_path,
                "old_string": drafted.old_string,
                "new_string": drafted.new_string,
            },
            tools=self.tools,
        )
        return self.extract_edit_preview(execution, drafted.file_path)

    def repairing_edit_drafter(self) -> RepairingEditDrafter | None:
        if self.edit_drafter is None or not hasattr(self.edit_drafter, "redraft_edit"):
            return None
        return cast(RepairingEditDrafter, self.edit_drafter)

    def should_retry_draft(self, preview: EditPreview, file_path: str) -> bool:
        if preview.status != "ok":
            return True
        return Path(file_path).suffix.lower() == ".java" and preview.validation.status == "error"

    def build_draft_retry_feedback(self, preview: EditPreview, file_path: str) -> str:
        lines = [
            f"preview_status: {preview.status}",
            f"preview_message: {preview.message}",
        ]
        if preview.occurrences:
            lines.append(f"matched_occurrences: {preview.occurrences}")
        if Path(file_path).suffix.lower() == ".java":
            lines.append(f"validation_status: {preview.validation.status}")
            lines.append(f"validation_message: {preview.validation.message}")
        return "\n".join(lines)

    def apply_scan_result(self, result: ScanResult) -> None:
        if result.status == "ok":
            artifact_id = save_scan_result(self.repo_root, result)
            self.session.active_findings_id = artifact_id
            self.session.current_goal = "review_findings"
            if not self.session.active_scope:
                self.session.active_scope = result.scope
            return

        self.session.active_findings_id = None

    def run_post_apply_rescan(self, pending: PendingEdit) -> str:
        validation, rescan = validate_post_apply_rescan(self.repo_root, pending, scanner=self.scanner.scan)
        if rescan is not None:
            rescan_artifact_id = save_scan_result(self.repo_root, rescan)
            validation.rescan_artifact_id = rescan_artifact_id

        validation_id = save_validation_result(self.repo_root, validation)
        self.session.last_validation_id = validation_id
        return format_rescan_validation(validation)

    def build_default_preview_validation(self) -> object:
        return SyntaxValidationResult(
            status="skipped",
            message="Syntax validation result was unavailable for this tool execution.",
        )

    def rebuild_scanner(self) -> None:
        scanner = get_scanner(DEFAULT_SCANNER_NAME)
        if scanner is None:
            raise RuntimeError(f"Default scanner is unavailable: {DEFAULT_SCANNER_NAME}")
        self.scanner = cast(JavaScanner, scanner)


def format_init_summary(summary: ProjectSummary) -> str:
    return (
        "Initialized project:\n"
        f"- repo_root: {summary.repo_root}\n"
        f"- indexed entries: {summary.indexed_entries}\n"
        f"- indexed files: {summary.indexed_files}\n"
        f"- indexed directories: {summary.indexed_directories}\n"
        f"- indexed java files: {summary.indexed_java_files}"
    )


def format_reindex_summary(summary: ProjectSummary) -> str:
    return (
        "Reindexed project:\n"
        f"- repo_root: {summary.repo_root}\n"
        f"- indexed entries: {summary.indexed_entries}\n"
        f"- indexed files: {summary.indexed_files}\n"
        f"- indexed directories: {summary.indexed_directories}\n"
        f"- indexed java files: {summary.indexed_java_files}"
    )


def format_readiness_report(report: ReadinessReport) -> str:
    lines = ["Runtime readiness:"]
    for check in report.checks:
        if check.name == "project":
            continue
        lines.append(f"- {check.name}: {check.status}")
        lines.append(f"  {check.message}")
    return "\n".join(lines)


def format_scanners_report(scanners: list[object], repo_root: Path | None) -> str:
    lines = ["Java scanners:"]
    for scanner in scanners:
        scanner_meta = scanner.get_scanner(repo_root)
        selector = "selected" if scanner_meta.selected else "disabled"
        lines.append(f"- [{selector}] {scanner_meta.name}: {scanner_meta.status}")
        lines.append(f"  {scanner_meta.message}")
    return "\n".join(lines)


def format_scan_result(result: ScanResult) -> str:
    header = [
        "Scan result:",
        f"- engine: {result.engine}",
        f"- scope: {', '.join(result.scope) if result.scope else '(none)'}",
        f"- targets: {', '.join(result.targets) if result.targets else '(none)'}",
        f"- status: {result.status}",
        f"- message: {result.message}",
    ]

    if not result.findings:
        header.append("- findings: 0")
        return "\n".join(header)

    header.append(f"- findings: {result.summary.get('total', len(result.findings))}")
    for severity, count in sorted(result.summary.items()):
        if severity == "total":
            continue
        header.append(f"  - {severity}: {count}")

    header.append("Findings:")
    for idx, finding in enumerate(result.findings, start=1):
        header.append(
            f"  {idx}. {finding.path}:{finding.start_line} [{finding.severity}] "
            f"{finding.check_id} - {finding.message}"
        )
    header.append("Next:")
    header.append("  - Say '修复第1个问题' to draft a patch for one finding.")
    header.append("  - Say '@path 生成 patch' to narrow the draft to one file.")
    return "\n".join(header)


def format_edit_preview(preview: EditPreview, prefix: str) -> str:
    lines = [
        prefix,
        f"- file: {preview.file_path}",
        f"- status: {preview.status}",
        f"- message: {preview.message}",
        f"- occurrences: {preview.occurrences}",
        f"- validation status: {preview.validation.status}",
        f"- validation message: {preview.validation.message}",
    ]
    if preview.diff:
        lines.append(preview.diff)
    return "\n".join(lines)


def append_pending_patch_menu(body: str) -> str:
    return f"{body}\n\n{format_pending_patch_menu()}"


def format_pending_patch_menu() -> str:
    return "Patch options:\n- apply\n- discard"


def format_rescan_validation(result: RescanValidationResult) -> str:
    lines = [
        "Post-apply ReScan:",
        f"- status: {result.status}",
        f"- message: {result.message}",
        f"- source artifact: {result.source_artifact_id or '(none)'}",
        f"- source finding index: {result.source_finding_index or '(none)'}",
        f"- source check_id: {result.source_check_id or '(none)'}",
        f"- source path: {result.source_path or '(none)'}",
        f"- remaining matches: {result.remaining_matches}",
        f"- rescan artifact: {result.rescan_artifact_id or '(none)'}",
    ]
    return "\n".join(lines)


def format_finding_candidates(
    candidates: list[tuple[int, object]],
    prefix: str,
    max_items: int = 10,
) -> str:
    lines = [prefix, "Candidates:"]
    for index, finding in candidates[:max_items]:
        lines.append(
            f"  {index}. {getattr(finding, 'path', '')}:{getattr(finding, 'start_line', 0)} "
            f"[{getattr(finding, 'severity', '')}] {getattr(finding, 'check_id', '')} "
            f"- {getattr(finding, 'message', '')}"
        )
    if len(candidates) > max_items:
        lines.append(f"  ... and {len(candidates) - max_items} more")
    return "\n".join(lines)


FINDING_INDEX_PATTERNS = (
    re.compile(r"第\s*(\d+)\s*个"),
    re.compile(r"\b(\d+)\b"),
)

CHINESE_FINDING_INDEX = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

CHINESE_FINDING_INDEX_PATTERN = re.compile(r"第\s*([一二两三四五六七八九十])\s*个")


def extract_requested_finding_index(text: str) -> int | None:
    for pattern in FINDING_INDEX_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return int(match.group(1))

    match = CHINESE_FINDING_INDEX_PATTERN.search(text)
    if match is None:
        return None
    return CHINESE_FINDING_INDEX.get(match.group(1))


def extract_planned_finding_index(tool_args: dict[str, object]) -> int | None:
    raw_index = tool_args.get("finding_index")
    if raw_index is None:
        return None
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return None
    return index if index > 0 else None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def build_finding_instruction(
    finding: object,
    user_request: str | None = None,
    mention_context: str | None = None,
) -> str:
    check_id = getattr(finding, "check_id", "")
    severity = getattr(finding, "severity", "")
    message = getattr(finding, "message", "")
    start_line = getattr(finding, "start_line", 0)
    end_line = getattr(finding, "end_line", 0)
    rule = getattr(finding, "rule", "")
    snippet = getattr(finding, "snippet", "")
    return (
        "Draft one minimal search-replace edit for this finding.\n"
        f"user_request: {user_request or '(none)'}\n"
        f"check_id: {check_id}\n"
        f"severity: {severity}\n"
        f"message: {message}\n"
        f"rule: {rule}\n"
        f"line_range: {start_line}-{end_line}\n"
        f"snippet:\n{snippet}\n"
        f"mention_context:\n{mention_context or '(none)'}\n"
    )


def main() -> int:
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
