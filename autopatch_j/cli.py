from __future__ import annotations

import shlex
from pathlib import Path

from autopatch_j.artifacts import save_scan_result
from autopatch_j.context import build_context_preview
from autopatch_j.decision_engine import AgentDecision, DecisionContext, RuleBasedDecisionEngine
from autopatch_j.indexer import IndexEntry, summarize_index
from autopatch_j.mentions import MentionResolution, ParsedPrompt, parse_prompt
from autopatch_j.project import ProjectSummary, discover_repo_root, initialize_project, load_project
from autopatch_j.session import SessionState, save_session
from autopatch_j.tools.scan_java import ScanResult, scan_java
from autopatch_j.tools.registry import ToolExecutionResult, ToolRegistry

HELP_TEXT = """Commands:
  /init [path]   Initialize the current repository for AutoPatch-J
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
        self.decision_engine = RuleBasedDecisionEngine()
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
        return (
            f"Repo root: {self.repo_root}\n"
            f"Indexed entries: {summary['entries']} "
            f"(files: {summary['files']}, dirs: {summary['directories']}, java: {summary['java_files']})\n"
            f"Active scope: {active_scope}\n"
            f"Recent mentions: {recent_mentions}\n"
            f"Current goal: {self.session.current_goal or '(none)'}\n"
            f"Active findings: {self.session.active_findings_id or '(none)'}"
        )

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

    def apply_scan_result(self, result: ScanResult) -> None:
        if result.status == "ok":
            artifact_id = save_scan_result(self.repo_root, result)
            self.session.active_findings_id = artifact_id
            self.session.current_goal = "review_findings"
            if not self.session.active_scope:
                self.session.active_scope = result.scope
            return

        self.session.active_findings_id = None


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

    header.append("Top findings:")
    for idx, finding in enumerate(result.findings[:5], start=1):
        header.append(
            f"  {idx}. {finding.path}:{finding.start_line} [{finding.severity}] "
            f"{finding.check_id} - {finding.message}"
        )
    return "\n".join(header)


def main() -> int:
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
