from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.artifacts import save_scan_result
from autopatch_j.cli import AutoPatchCLI
from autopatch_j.edit_drafter import DraftedEdit
from autopatch_j.project import initialize_project
from autopatch_j.tools.scan_java import Finding, ScanResult


class PromptAwareEditDrafter:
    label = "prompt-aware-drafter"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def draft_edit(self, file_path: str, instruction: str, file_content: str) -> DraftedEdit:
        self.calls.append((file_path, instruction, file_content))
        if file_path.endswith("Demo.java"):
            return DraftedEdit(
                file_path=file_path,
                old_string="call();",
                new_string="safeCall();",
                rationale="Replace the risky call with the guarded variant.",
            )
        if file_path.endswith("Other.java"):
            return DraftedEdit(
                file_path=file_path,
                old_string="run();",
                new_string="safeRun();",
                rationale="Replace the risky run call with the guarded variant.",
            )
        raise AssertionError(f"Unexpected file path for draft_edit: {file_path}")


class PromptAgentFlowTests(unittest.TestCase):
    def test_prompt_can_draft_fix_from_requested_finding_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "Demo.java").write_text(
                "class Demo {\n"
                "    void run() {\n"
                "        call();\n"
                "    }\n"
                "}\n",
                encoding="utf-8",
            )
            initialize_project(repo_root)
            artifact_id = save_scan_result(
                repo_root,
                ScanResult(
                    engine="semgrep",
                    scope=["Demo.java"],
                    targets=["Demo.java"],
                    status="ok",
                    message="Semgrep completed with 1 finding(s).",
                    summary={"total": 1, "error": 1},
                    findings=[
                        Finding(
                            check_id="java.lang.correctness.demo",
                            path="Demo.java",
                            start_line=3,
                            end_line=3,
                            severity="error",
                            message="Replace risky call",
                            rule="CWE-000",
                            snippet="call();",
                        )
                    ],
                ),
            )

            cli = AutoPatchCLI(repo_root)
            cli.session.active_findings_id = artifact_id
            cli.edit_drafter = PromptAwareEditDrafter()

            output = cli.handle_line("修复第1个问题")

            self.assertIn("Draft fix context:", output)
            self.assertIn("Pending edit updated from draft.", output)
            self.assertIsNotNone(cli.session.pending_edit)
            assert cli.session.pending_edit is not None
            self.assertEqual(cli.session.pending_edit.source_finding_index, 1)
            self.assertEqual(cli.session.current_goal, "review_pending_edit")

    def test_prompt_can_use_mention_scope_to_select_one_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            (repo_root / "src" / "Demo.java").write_text(
                "class Demo { void run() { call(); } }\n",
                encoding="utf-8",
            )
            (repo_root / "src" / "Other.java").write_text(
                "class Other { void run() { run(); } }\n",
                encoding="utf-8",
            )
            initialize_project(repo_root)
            artifact_id = save_scan_result(
                repo_root,
                ScanResult(
                    engine="semgrep",
                    scope=["src"],
                    targets=["src"],
                    status="ok",
                    message="Semgrep completed with 2 finding(s).",
                    summary={"total": 2, "error": 2},
                    findings=[
                        Finding(
                            check_id="java.lang.correctness.demo",
                            path="src/Demo.java",
                            start_line=1,
                            end_line=1,
                            severity="error",
                            message="Replace risky call",
                            rule="CWE-000",
                            snippet="call();",
                        ),
                        Finding(
                            check_id="java.lang.correctness.other",
                            path="src/Other.java",
                            start_line=1,
                            end_line=1,
                            severity="error",
                            message="Replace risky run",
                            rule="CWE-111",
                            snippet="run();",
                        ),
                    ],
                ),
            )

            cli = AutoPatchCLI(repo_root)
            cli.session.active_findings_id = artifact_id
            drafter = PromptAwareEditDrafter()
            cli.edit_drafter = drafter

            output = cli.handle_line("@src/Demo.java 生成 patch")

            self.assertIn("Mentioned scope:", output)
            self.assertIn("src/Demo.java", output)
            self.assertEqual(len(drafter.calls), 1)
            self.assertEqual(drafter.calls[0][0], "src/Demo.java")
            self.assertIsNotNone(cli.session.pending_edit)
            assert cli.session.pending_edit is not None
            self.assertEqual(cli.session.pending_edit.source_finding_index, 1)

    def test_prompt_reports_ambiguous_patch_target_without_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "Demo.java").write_text("class Demo { void run() { call(); } }\n", encoding="utf-8")
            (repo_root / "Other.java").write_text("class Other { void run() { run(); } }\n", encoding="utf-8")
            initialize_project(repo_root)
            artifact_id = save_scan_result(
                repo_root,
                ScanResult(
                    engine="semgrep",
                    scope=["."],
                    targets=["."],
                    status="ok",
                    message="Semgrep completed with 2 finding(s).",
                    summary={"total": 2, "error": 2},
                    findings=[
                        Finding(
                            check_id="java.lang.correctness.demo",
                            path="Demo.java",
                            start_line=1,
                            end_line=1,
                            severity="error",
                            message="Replace risky call",
                            rule="CWE-000",
                            snippet="call();",
                        ),
                        Finding(
                            check_id="java.lang.correctness.other",
                            path="Other.java",
                            start_line=1,
                            end_line=1,
                            severity="error",
                            message="Replace risky run",
                            rule="CWE-111",
                            snippet="run();",
                        ),
                    ],
                ),
            )

            cli = AutoPatchCLI(repo_root)
            cli.session.active_findings_id = artifact_id
            cli.edit_drafter = PromptAwareEditDrafter()

            output = cli.handle_line("生成 patch")

            self.assertIn("Multiple active findings are available.", output)
            self.assertIn("Candidates:", output)
            self.assertIsNone(cli.session.pending_edit)

    def test_prompt_can_apply_pending_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "notes.txt").write_text("call();\n", encoding="utf-8")
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            cli.handle_command('/preview-edit notes.txt "call();" "safeCall();"')

            output = cli.handle_line("应用这个patch")

            self.assertIn("Pending edit applied.", output)
            self.assertIsNone(cli.session.pending_edit)
            self.assertIn("safeCall();", (repo_root / "notes.txt").read_text(encoding="utf-8"))

    def test_prompt_can_show_active_findings_without_rescanning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "Demo.java").write_text("class Demo { void run() { call(); } }\n", encoding="utf-8")
            initialize_project(repo_root)
            artifact_id = save_scan_result(
                repo_root,
                ScanResult(
                    engine="semgrep",
                    scope=["Demo.java"],
                    targets=["Demo.java"],
                    status="ok",
                    message="Semgrep completed with 1 finding(s).",
                    summary={"total": 1, "error": 1},
                    findings=[
                        Finding(
                            check_id="java.lang.correctness.demo",
                            path="Demo.java",
                            start_line=1,
                            end_line=1,
                            severity="error",
                            message="Replace risky call",
                            rule="CWE-000",
                            snippet="call();",
                        )
                    ],
                ),
            )

            cli = AutoPatchCLI(repo_root)
            cli.session.active_findings_id = artifact_id

            output = cli.handle_line("列出问题")

            self.assertIn("Scan result:", output)
            self.assertIn("java.lang.correctness.demo", output)
            self.assertNotIn("semgrep is not installed", output)

    def test_prompt_can_show_pending_patch_without_redrafting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "notes.txt").write_text("call();\n", encoding="utf-8")
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            cli.handle_command('/preview-edit notes.txt "call();" "safeCall();"')

            output = cli.handle_line("看看 patch")

            self.assertIn("Pending edit:", output)
            self.assertIn("safeCall();", output)
            self.assertIsNotNone(cli.session.pending_edit)


if __name__ == "__main__":
    unittest.main()
