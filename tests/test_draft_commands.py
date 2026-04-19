from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.cli import AutoPatchCLI
from autopatch_j.edit_drafter import DraftedEdit
from autopatch_j.project import initialize_project


class FakeEditDrafter:
    label = "fake-drafter"

    def draft_edit(self, file_path: str, instruction: str, file_content: str) -> DraftedEdit:
        del instruction
        del file_content
        return DraftedEdit(
            file_path=file_path,
            old_string="call();",
            new_string="safeCall();",
            rationale="Minimal replacement.",
        )


class DraftCommandTests(unittest.TestCase):
    def test_draft_edit_command_stores_pending_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "notes.txt").write_text("call();\n", encoding="utf-8")
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            cli.edit_drafter = FakeEditDrafter()

            output = cli.handle_command('/draft-edit notes.txt "replace call"')

            self.assertIn("Drafted edit:", output)
            self.assertIn("Minimal replacement.", output)
            self.assertIn("Pending edit updated from draft.", output)
            self.assertIsNotNone(cli.session.pending_edit)
            assert cli.session.pending_edit is not None
            self.assertEqual(cli.session.pending_edit.rationale, "Minimal replacement.")
            self.assertIsNone(cli.session.pending_edit.source_artifact_id)

    def test_draft_edit_requires_configured_drafter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "notes.txt").write_text("call();\n", encoding="utf-8")
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            cli.edit_drafter = None

            output = cli.handle_command('/draft-edit notes.txt "replace call"')

            self.assertEqual(
                output,
                "Edit drafter is disabled. Set OPENAI_API_KEY to enable /draft-edit.",
            )


if __name__ == "__main__":
    unittest.main()
