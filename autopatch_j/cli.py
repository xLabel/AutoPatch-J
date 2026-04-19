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
from autopatch_j.decision_engine import AgentDecision, DecisionContext, build_default_decision_engine
from autopatch_j.doctor import DoctorReport, build_doctor_report
from autopatch_j.edit_drafter import (
    DraftedEdit,
    RepairingEditDrafter,
    build_default_edit_drafter,
)
from autopatch_j.indexer import IndexEntry, summarize_index
from autopatch_j.intent import (
    has_apply_intent,
    has_findings_review_intent,
    has_patch_intent,
    has_pending_review_intent,
    has_scan_intent,
)
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
    load_project_config,
    load_project,
    refresh_project_index,
    save_project_config,
)
from autopatch_j.scanners import ScanResult, build_java_scanner
from autopatch_j.session import PendingEdit, ProjectConfig, SessionState, save_session
from autopatch_j.tools.edit_tool import EditPreview
from autopatch_j.tools.registry import ToolExecutionResult, ToolRegistry
from autopatch_j.validators.rescan import RescanValidationResult, validate_post_apply_rescan

HELP_TEXT = """Commands:
  /init [path]   Initialize the current repository for AutoPatch-J
  /env          Inspect runtime prerequisites and feature availability
  /scanner      Show the current scanner configuration
  /scanner semgrep [config] [--bin <path>]
                Persist project scanner selection, optional Semgrep config, and optional binary path
  /scanner reset
                Clear project scanner overrides and fall back to env/defaults
  /draft-edit <file_path> <instruction>
                 Ask the model to draft one search-replace edit for a file
  /draft-fix <finding_index> [artifact_id]
                 Ask the model to draft one search-replace edit for a finding
  /preview-edit <file_path> <old_string> <new_string>
                 Preview a search-replace edit and store it as pending
  /show-pending  Show the current pending edit and diff
  /apply-pending Apply the current pending edit to the working tree
  /clear-pending Drop the current pending edit without writing files
  /show-findings [artifact_id]
                 Show a saved scan artifact, or the current active findings if omitted
  /show-validation [artifact_id]
                 Show a saved post-apply validation artifact, or the latest one if omitted
  /reindex       Refresh repository index for @mention and scope lookup
  /status        Show current session state
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
      看看 patch
      应用这个patch
  - Ambiguous mentions will show candidate paths for selection.
"""


class AutoPatchCLI:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.repo_root = discover_repo_root(self.cwd)
        self.session = SessionState()
        self.project_config = ProjectConfig(repo_root=str(self.repo_root)) if self.repo_root else None
        self.index: list[IndexEntry] = []
        self._completion_matches: list[str] = []
        self.decision_engine = build_default_decision_engine()
        self.edit_drafter = build_default_edit_drafter()
        if self.repo_root is not None:
            self.session, self.index = load_project(self.repo_root)
            self.project_config = load_project_config(self.repo_root)
            if self.session.repo_root is None:
                self.session.repo_root = str(self.repo_root)
        self.rebuild_scanner()
        self.tool_registry = ToolRegistry(scanner=self.scanner)

    def run(self) -> int:
        self.configure_readline()
        print("AutoPatch-J CLI")
        if self.repo_root is not None:
            print(f"Loaded project: {self.repo_root}")
        else:
            print("No project initialized yet. Run /init . to start.")

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
            return "No active project. Run /init [path] before entering prompts."

        parsed = parse_prompt(raw, self.index)
        if not self.resolve_mentions_interactively(parsed):
            return "Prompt cancelled."

        self.update_session_from_prompt(parsed)
        mention_context = build_mention_context_text(self.repo_root, parsed)

        if has_apply_intent(parsed.clean_text):
            response = self.handle_prompt_apply()
            save_session(self.repo_root, self.session)
            return response

        prompt_review_response = self.handle_prompt_review(parsed)
        if prompt_review_response is not None:
            save_session(self.repo_root, self.session)
            return self.render_prompt_response(parsed, prompt_review_response)

        prompt_patch_response = self.handle_prompt_patch(parsed)
        if prompt_patch_response is not None:
            save_session(self.repo_root, self.session)
            return self.render_prompt_response(parsed, prompt_patch_response)

        if has_scan_intent(parsed.clean_text):
            preview = build_context_preview(self.repo_root, parsed)
            decision = self.decision_engine.decide(
                DecisionContext(
                    user_text=parsed.clean_text,
                    scoped_paths=self.effective_scope(parsed),
                    has_active_findings=self.session.active_findings_id is not None,
                    mention_context=mention_context,
                )
            )

            if decision.action == "tool_call":
                execution = self.run_tool(decision)
                result = self.extract_scan_result(execution)
                self.apply_scan_result(result)
                save_session(self.repo_root, self.session)
                return f"{preview}\n\n{format_scan_result(result)}"

            save_session(self.repo_root, self.session)
            return f"{preview}\n\n{decision.message}"

        preview = build_context_preview(self.repo_root, parsed)
        decision = self.decision_engine.decide(
            DecisionContext(
                user_text=parsed.clean_text,
                scoped_paths=self.effective_scope(parsed),
                has_active_findings=self.session.active_findings_id is not None,
                mention_context=mention_context,
            )
        )

        if decision.action == "tool_call":
            execution = self.run_tool(decision)
            result = self.extract_scan_result(execution)
            self.apply_scan_result(result)
            save_session(self.repo_root, self.session)
            return f"{preview}\n\n{format_scan_result(result)}"

        save_session(self.repo_root, self.session)
        return f"{preview}\n\n{decision.message}"

    def handle_command(self, raw: str) -> str:
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            return f"Command parse error: {exc}"

        command = parts[0]
        if command == "/help":
            return HELP_TEXT
        if command == "/draft-edit":
            return self.handle_draft_edit(parts)
        if command == "/draft-fix":
            return self.handle_draft_fix(parts)
        if command == "/preview-edit":
            return self.handle_preview_edit(parts)
        if command == "/show-pending":
            return self.handle_show_pending()
        if command == "/apply-pending":
            return self.handle_apply_pending()
        if command == "/clear-pending":
            return self.handle_clear_pending()
        if command == "/show-findings":
            artifact_id = parts[1] if len(parts) > 1 else None
            return self.handle_show_findings(artifact_id)
        if command == "/show-validation":
            artifact_id = parts[1] if len(parts) > 1 else None
            return self.handle_show_validation(artifact_id)
        if command == "/env":
            return self.handle_env()
        if command == "/scanner":
            return self.handle_scanner(parts)
        if command == "/reindex":
            return self.handle_reindex()
        if command == "/status":
            return self.format_status()
        if command == "/init":
            target = Path(parts[1]) if len(parts) > 1 else self.cwd
            return self.handle_init(target)
        return f"Unknown command: {command}\n\n{HELP_TEXT}"

    def handle_init(self, target: Path) -> str:
        session, index, summary = initialize_project((self.cwd / target).resolve() if not target.is_absolute() else target)
        self.repo_root = Path(summary.repo_root)
        self.session = session
        self.index = index
        self.project_config = load_project_config(self.repo_root)
        self.rebuild_scanner()
        self.tool_registry = ToolRegistry(scanner=self.scanner)
        return format_init_summary(summary)

    def handle_reindex(self) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."

        self.index, summary = refresh_project_index(self.repo_root)
        return format_reindex_summary(summary)

    def handle_env(self) -> str:
        report = build_doctor_report(
            repo_root=self.repo_root,
            scanner=self.scanner,
            decision_engine_label=self.decision_engine.label,
            edit_drafter_label=self.edit_drafter.label if self.edit_drafter else None,
        )
        return format_doctor_report(report)

    def handle_scanner(self, parts: list[str]) -> str:
        if len(parts) == 1:
            return format_scanner_summary(self.scanner, self.project_config)
        if self.repo_root is None or self.project_config is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."
        if len(parts) == 2 and parts[1] == "reset":
            self.project_config.scanner_name = None
            self.project_config.semgrep_config = None
            self.project_config.semgrep_bin = None
            save_project_config(self.repo_root, self.project_config)
            self.rebuild_scanner()
            self.tool_registry = ToolRegistry(scanner=self.scanner)
            return (
                "Scanner config reset to env/default.\n"
                f"{format_scanner_summary(self.scanner, self.project_config)}"
            )
        if parts[1] != "semgrep":
            return (
                "Usage:\n"
                "  /scanner\n"
                "  /scanner semgrep [config] [--bin <path>]\n"
                "  /scanner reset"
            )
        args = parts[2:]
        semgrep_config = "p/java"
        semgrep_bin = self.project_config.semgrep_bin
        if args and args[0] != "--bin":
            semgrep_config = args[0]
            args = args[1:]
        if args:
            if len(args) != 2 or args[0] != "--bin":
                return (
                    "Usage:\n"
                    "  /scanner\n"
                    "  /scanner semgrep [config] [--bin <path>]\n"
                    "  /scanner reset"
                )
            semgrep_bin = args[1]
        self.project_config.scanner_name = "semgrep"
        self.project_config.semgrep_config = semgrep_config
        self.project_config.semgrep_bin = semgrep_bin
        save_project_config(self.repo_root, self.project_config)
        self.rebuild_scanner()
        self.tool_registry = ToolRegistry(scanner=self.scanner)
        return (
            "Scanner config updated.\n"
            f"{format_scanner_summary(self.scanner, self.project_config)}"
        )

    def format_status(self) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."

        summary = summarize_index(self.index)
        active_scope = ", ".join(self.session.active_scope) if self.session.active_scope else "(none)"
        recent_mentions = ", ".join(self.session.recent_mentions) if self.session.recent_mentions else "(none)"
        pending_edit = self.session.pending_edit.file_path if self.session.pending_edit else "(none)"
        return (
            f"Repo root: {self.repo_root}\n"
            f"Scanner: {self.scanner.label}\n"
            f"Decision engine: {self.decision_engine.label}\n"
            f"Edit drafter: {self.edit_drafter.label if self.edit_drafter else '(disabled)'}\n"
            f"Indexed entries: {summary['entries']} "
            f"(files: {summary['files']}, dirs: {summary['directories']}, java: {summary['java_files']})\n"
            f"Active scope: {active_scope}\n"
            f"Recent mentions: {recent_mentions}\n"
            f"Current goal: {self.session.current_goal or '(none)'}\n"
            f"Active findings: {self.session.active_findings_id or '(none)'}\n"
            f"Last validation: {self.session.last_validation_id or '(none)'}\n"
            f"Pending edit: {pending_edit}"
        )

    def handle_draft_edit(self, parts: list[str]) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."
        if self.edit_drafter is None:
            return "Edit drafter is disabled. Set OPENAI_API_KEY to enable /draft-edit."
        if len(parts) != 3:
            return (
                "Usage: /draft-edit <file_path> <instruction>\n"
                "Wrap the instruction in quotes if it contains spaces."
            )

        file_path = parts[1]
        instruction = parts[2]
        target = (self.repo_root / file_path).resolve()
        try:
            target.relative_to(self.repo_root.resolve())
        except ValueError:
            return f"Target file is outside the repository: {file_path}"
        if not target.exists() or not target.is_file():
            return f"Target file does not exist: {file_path}"

        file_content = read_text(target)
        return self.draft_pending_edit(
            file_path=file_path,
            instruction=instruction,
            file_content=file_content,
        )

    def handle_draft_fix(self, parts: list[str]) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."
        if self.edit_drafter is None:
            return "Edit drafter is disabled. Set OPENAI_API_KEY to enable /draft-fix."
        if len(parts) not in {2, 3}:
            return (
                "Usage: /draft-fix <finding_index> [artifact_id]\n"
                "Use /show-findings to inspect the available finding list."
            )

        finding_index = parts[1]
        if not finding_index.isdigit():
            return f"finding_index must be a positive integer: {finding_index}"

        artifact_id = parts[2] if len(parts) == 3 else self.session.active_findings_id
        if not artifact_id:
            return "No findings artifact is active."

        result = load_scan_result(self.repo_root, artifact_id)
        if result is None:
            return f"Findings artifact not found: {artifact_id}"

        finding_position = int(finding_index)
        if finding_position < 1 or finding_position > len(result.findings):
            return (
                f"finding_index out of range: {finding_position}. "
                f"Available findings: 1..{len(result.findings)}"
            )

        return self.draft_fix_for_finding(result, artifact_id, finding_position)

    def handle_preview_edit(self, parts: list[str]) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."
        if len(parts) != 4:
            return (
                "Usage: /preview-edit <file_path> <old_string> <new_string>\n"
                "Wrap values with spaces in quotes."
            )

        execution = self.tool_registry.execute(
            repo_root=self.repo_root,
            tool_name="preview_search_replace",
            tool_args={
                "file_path": parts[1],
                "old_string": parts[2],
                "new_string": parts[3],
            },
        )
        return self.store_pending_from_preview(
            file_path=parts[1],
            old_string=parts[2],
            new_string=parts[3],
            preview=self.extract_edit_preview(execution, parts[1]),
            prefix="Pending edit updated.",
        )

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
            return format_edit_preview(preview, prefix=prefix)

        self.session.pending_edit = None
        save_session(self.repo_root, self.session)
        return format_edit_preview(preview, prefix="Pending edit cleared because preview failed.")

    def handle_show_pending(self) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."
        pending = self.session.pending_edit
        if pending is None:
            return "No pending edit."
        return (
            "Pending edit:\n"
            f"- file: {pending.file_path}\n"
            f"- old_string length: {len(pending.old_string)}\n"
            f"- new_string length: {len(pending.new_string)}\n"
            f"- validation status: {pending.validation_status}\n"
            f"- validation message: {pending.validation_message}\n"
            f"- rationale: {pending.rationale or '(none)'}\n"
            f"- source artifact: {pending.source_artifact_id or '(none)'}\n"
            f"- source finding index: {pending.source_finding_index or '(none)'}\n"
            f"- source check_id: {pending.source_check_id or '(none)'}\n"
            f"{pending.diff}"
        )

    def handle_apply_pending(self) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."
        pending = self.session.pending_edit
        if pending is None:
            return "No pending edit to apply."

        execution = self.tool_registry.execute(
            repo_root=self.repo_root,
            tool_name="apply_search_replace",
            tool_args={
                "file_path": pending.file_path,
                "old_string": pending.old_string,
                "new_string": pending.new_string,
            },
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

    def handle_clear_pending(self) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."
        if self.session.pending_edit is None:
            return "No pending edit to clear."
        self.session.pending_edit = None
        save_session(self.repo_root, self.session)
        return "Pending edit cleared."

    def handle_show_findings(self, artifact_id: str | None) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."

        target_artifact = artifact_id or self.session.active_findings_id
        if not target_artifact:
            return "No findings artifact is active."

        result = load_scan_result(self.repo_root, target_artifact)
        if result is None:
            return f"Findings artifact not found: {target_artifact}"

        return format_scan_result(result)

    def handle_show_validation(self, artifact_id: str | None) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."

        target_artifact = artifact_id or self.session.last_validation_id
        if not target_artifact:
            return "No validation artifact is active."

        result = load_validation_result(self.repo_root, target_artifact)
        if result is None:
            return f"Validation artifact not found: {target_artifact}"

        return format_rescan_validation(result)

    def handle_prompt_apply(self) -> str:
        if self.session.pending_edit is None:
            return "No pending edit to apply."
        return self.handle_apply_pending()

    def handle_prompt_review(self, parsed: ParsedPrompt) -> str | None:
        if has_pending_review_intent(parsed.clean_text):
            return self.handle_show_pending()
        if not has_findings_review_intent(parsed.clean_text):
            return None
        if self.session.active_findings_id is None and has_scan_intent(parsed.clean_text):
            return None
        return self.handle_show_findings(None)

    def handle_prompt_patch(self, parsed: ParsedPrompt) -> str | None:
        if not has_patch_intent(parsed.clean_text):
            return None
        if self.session.pending_edit is not None:
            return (
                "A pending edit already exists. Review it with /show-pending, "
                "apply it with /apply-pending, or drop it with /clear-pending "
                "before drafting another patch."
            )

        artifact_id = self.session.active_findings_id
        if not artifact_id:
            if has_scan_intent(parsed.clean_text):
                return None
            return (
                "No active findings are available. Scan the repository first, "
                "then ask AutoPatch-J to draft a patch."
            )
        if self.edit_drafter is None:
            return "Edit drafter is disabled. Set OPENAI_API_KEY to enable patch drafting."

        result = load_scan_result(self.repo_root, artifact_id)
        if result is None:
            return f"Findings artifact not found: {artifact_id}"

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
        return self.tool_registry.execute(
            repo_root=self.repo_root,
            tool_name=decision.tool_name or "",
            tool_args=dict(decision.tool_args),
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
        execution = self.tool_registry.execute(
            repo_root=self.repo_root,
            tool_name="preview_search_replace",
            tool_args={
                "file_path": drafted.file_path,
                "old_string": drafted.old_string,
                "new_string": drafted.new_string,
            },
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
        from autopatch_j.validators.java_syntax import SyntaxValidationResult

        return SyntaxValidationResult(
            status="skipped",
            message="Syntax validation result was unavailable for this tool execution.",
        )

    def rebuild_scanner(self) -> None:
        scanner_name = self.project_config.scanner_name if self.project_config else None
        semgrep_config = self.project_config.semgrep_config if self.project_config else None
        semgrep_bin = self.project_config.semgrep_bin if self.project_config else None
        self.scanner = build_java_scanner(
            scanner_name=scanner_name,
            semgrep_config=semgrep_config,
            semgrep_bin=semgrep_bin,
        )


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


def format_doctor_report(report: DoctorReport) -> str:
    lines = ["Environment report:"]
    for check in report.checks:
        lines.append(f"- {check.name}: {check.status}")
        lines.append(f"  {check.message}")
    return "\n".join(lines)


def format_scanner_summary(scanner: object, project_config: ProjectConfig | None) -> str:
    return (
        "Scanner config:\n"
        f"- active: {getattr(scanner, 'label', '(unknown)')}\n"
        f"- active semgrep bin: {getattr(scanner, 'binary_path', None) or '(PATH)'}\n"
        f"- project scanner: {project_config.scanner_name if project_config and project_config.scanner_name else '(none)'}\n"
        f"- project semgrep config: {project_config.semgrep_config if project_config and project_config.semgrep_config else '(none)'}\n"
        f"- project semgrep bin: {project_config.semgrep_bin if project_config and project_config.semgrep_bin else '(none)'}"
    )


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
