from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.artifacts import save_scan_result
from autopatch_j.cli import AutoPatchCLI
from autopatch_j.edit_drafter import DraftedEdit
from autopatch_j.project import initialize_project
from autopatch_j.tools.scan_java import Finding, ScanResult


class CapturingEditDrafter:
    label = "capturing-drafter"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def draft_edit(self, file_path: str, instruction: str, file_content: str) -> DraftedEdit:
        self.calls.append((file_path, instruction, file_content))
        return DraftedEdit(
            file_path=file_path,
            old_string='user.getName().equals("admin")',
            new_string='"admin".equals(user.getName())',
            rationale="Guard nullable string comparison.",
        )


class DraftFixCommandTests(unittest.TestCase):
    def test_draft_fix_uses_active_findings_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            source = (
                "class Demo {\n"
                "    void run(User user) {\n"
                "        if (user.getName().equals(\"admin\")) {\n"
                "        }\n"
                "    }\n"
                "}\n"
            )
            (repo_root / "Demo.java").write_text(source, encoding="utf-8")
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
                            message="Avoid direct string equality on nullable values",
                            rule="CWE-476",
                            snippet='if (user.getName().equals("admin")) {',
                        )
                    ],
                ),
            )

            cli = AutoPatchCLI(repo_root)
            cli.session.active_findings_id = artifact_id
            drafter = CapturingEditDrafter()
            cli.edit_drafter = drafter

            output = cli.handle_command("/draft-fix 1")

            self.assertIn("Draft fix context:", output)
            self.assertIn("Pending edit updated from draft.", output)
            self.assertIsNotNone(cli.session.pending_edit)
            self.assertEqual(len(drafter.calls), 1)
            _, instruction, file_content = drafter.calls[0]
            self.assertIn("Avoid direct string equality on nullable values", instruction)
            self.assertIn("user.getName().equals", file_content)

    def test_draft_fix_validates_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            cli.edit_drafter = CapturingEditDrafter()

            output = cli.handle_command("/draft-fix 1")
            self.assertEqual(output, "No findings artifact is active.")

    def test_draft_fix_requires_configured_drafter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            cli.edit_drafter = None

            output = cli.handle_command("/draft-fix 1")
            self.assertEqual(
                output,
                "Edit drafter is disabled. Set OPENAI_API_KEY to enable /draft-fix.",
            )


if __name__ == "__main__":
    unittest.main()
