from __future__ import annotations

import shlex
from pathlib import Path

from autopatch_j.artifacts import (
    load_scan_result,
    load_validation_result,
    save_scan_result,
    save_validation_result,
)
from autopatch_j.context import build_context_preview
from autopatch_j.decision_engine import AgentDecision, DecisionContext, build_default_decision_engine
from autopatch_j.edit_drafter import DraftedEdit, build_default_edit_drafter
from autopatch_j.indexer import IndexEntry, summarize_index
from autopatch_j.mentions import MentionResolution, ParsedPrompt, parse_prompt
from autopatch_j.project import ProjectSummary, discover_repo_root, initialize_project, load_project
from autopatch_j.session import PendingEdit, SessionState, save_session
from autopatch_j.tools.edit_tool import EditPreview
from autopatch_j.tools.scan_java import ScanResult, scan_java
from autopatch_j.tools.registry import ToolExecutionResult, ToolRegistry
from autopatch_j.validators.rescan import RescanValidationResult, validate_post_apply_rescan

HELP_TEXT = """Commands:
  /init [path]   Initialize the current repository for AutoPatch-J
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
  /status        Show current session state
  /help          Show this message
  /quit          Exit the CLI

Prompt rules:
  - Use @path or @filename to bind scope, for example:
      @src/main/java/com/foo/UserService.java scan this file
  - Ambiguous mentions will show candidate paths for selection.
"""


class AutoPatchCLI:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.repo_root = discover_repo_root(self.cwd)
        self.session = SessionState()
        self.index: list[IndexEntry] = []
        self.decision_engine = build_default_decision_engine()
        self.edit_drafter = build_default_edit_drafter()
        self.tool_registry = ToolRegistry()
        if self.repo_root is not None:
            self.session, self.index = load_project(self.repo_root)
            if self.session.repo_root is None:
                self.session.repo_root = str(self.repo_root)

    def run(self) -> int:
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

    def handle_line(self, raw: str) -> str:
        if raw.startswith("/"):
            return self.handle_command(raw)

        if self.repo_root is None:
            return "No active project. Run /init [path] before entering prompts."

        parsed = parse_prompt(raw, self.index)
        if not self.resolve_mentions_interactively(parsed):
            return "Prompt cancelled."

        self.update_session_from_prompt(parsed)
        preview = build_context_preview(self.repo_root, parsed)
        decision = self.decision_engine.decide(
            DecisionContext(
                user_text=parsed.clean_text,
                scoped_paths=self.effective_scope(parsed),
                has_active_findings=self.session.active_findings_id is not None,
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
        return format_init_summary(summary)

    def format_status(self) -> str:
        if self.repo_root is None:
            return "No active project. Run /init [path] to create AutoPatch-J state."

        summary = summarize_index(self.index)
        active_scope = ", ".join(self.session.active_scope) if self.session.active_scope else "(none)"
        recent_mentions = ", ".join(self.session.recent_mentions) if self.session.recent_mentions else "(none)"
        pending_edit = self.session.pending_edit.file_path if self.session.pending_edit else "(none)"
        return (
            f"Repo root: {self.repo_root}\n"
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
        try:
            drafted = self.edit_drafter.draft_edit(file_path, instruction, file_content)
        except Exception as exc:
            return f"Draft edit failed: {exc}"

        return self.store_pending_from_draft(drafted)

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

        finding = result.findings[finding_position - 1]
        target = (self.repo_root / finding.path).resolve()
        try:
            target.relative_to(self.repo_root.resolve())
        except ValueError:
            return f"Finding path is outside the repository: {finding.path}"
        if not target.exists() or not target.is_file():
            return f"Finding file does not exist: {finding.path}"

        file_content = read_text(target)
        instruction = build_finding_instruction(finding)
        try:
            drafted = self.edit_drafter.draft_edit(finding.path, instruction, file_content)
        except Exception as exc:
            return f"Draft fix failed: {exc}"

        header = [
            "Draft fix context:",
            f"- artifact: {artifact_id}",
            f"- finding index: {finding_position}",
            f"- rule: {finding.check_id}",
            f"- severity: {finding.severity}",
            f"- message: {finding.message}",
        ]
        body = self.store_pending_from_draft(
            drafted,
            source_artifact_id=artifact_id,
            source_finding_index=finding_position,
            source_check_id=finding.check_id,
        )
        return "\n".join(header + ["", body])

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
        source_artifact_id: str | None = None,
        source_finding_index: int | None = None,
        source_check_id: str | None = None,
    ) -> str:
        execution = self.tool_registry.execute(
            repo_root=self.repo_root,
            tool_name="preview_search_replace",
            tool_args={
                "file_path": drafted.file_path,
                "old_string": drafted.old_string,
                "new_string": drafted.new_string,
            },
        )
        preview = self.extract_edit_preview(execution, drafted.file_path)
        header = [
            "Drafted edit:",
            f"- file: {drafted.file_path}",
            f"- rationale: {drafted.rationale or '(none)'}",
        ]
        body = self.store_pending_from_preview(
            file_path=drafted.file_path,
            old_string=drafted.old_string,
            new_string=drafted.new_string,
            preview=preview,
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
        validation, rescan = validate_post_apply_rescan(self.repo_root, pending, scanner=scan_java)
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


def format_init_summary(summary: ProjectSummary) -> str:
    return (
        "Initialized project:\n"
        f"- repo_root: {summary.repo_root}\n"
        f"- indexed entries: {summary.indexed_entries}\n"
        f"- indexed files: {summary.indexed_files}\n"
        f"- indexed directories: {summary.indexed_directories}\n"
        f"- indexed java files: {summary.indexed_java_files}"
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


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def build_finding_instruction(finding: object) -> str:
    check_id = getattr(finding, "check_id", "")
    severity = getattr(finding, "severity", "")
    message = getattr(finding, "message", "")
    start_line = getattr(finding, "start_line", 0)
    end_line = getattr(finding, "end_line", 0)
    rule = getattr(finding, "rule", "")
    snippet = getattr(finding, "snippet", "")
    return (
        "Draft one minimal search-replace edit for this finding.\n"
        f"check_id: {check_id}\n"
        f"severity: {severity}\n"
        f"message: {message}\n"
        f"rule: {rule}\n"
        f"line_range: {start_line}-{end_line}\n"
        f"snippet:\n{snippet}\n"
    )


def main() -> int:
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
