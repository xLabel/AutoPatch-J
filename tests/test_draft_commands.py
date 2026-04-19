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


class RepairingFakeEditDrafter:
    label = "repairing-fake-drafter"

    def __init__(self) -> None:
        self.draft_calls = 0
        self.retry_feedback: str | None = None

    def draft_edit(self, file_path: str, instruction: str, file_content: str) -> DraftedEdit:
        del instruction
        del file_content
        self.draft_calls += 1
        return DraftedEdit(
            file_path=file_path,
            old_string="missing();",
            new_string="safeCall();",
            rationale="First attempt misses the real snippet.",
        )

    def redraft_edit(
        self,
        file_path: str,
        instruction: str,
        file_content: str,
        previous_edit: DraftedEdit,
        feedback: str,
    ) -> DraftedEdit:
        del instruction
        del file_content
        self.retry_feedback = feedback
        self.draft_calls += 1
        self.previous_edit = previous_edit
        return DraftedEdit(
            file_path=file_path,
            old_string="call();",
            new_string="safeCall();",
            rationale="Retry selects the unique existing call.",
        )


class DraftEditFlowTests(unittest.TestCase):
    def test_draft_pending_edit_stores_pending_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "notes.txt").write_text("call();\n", encoding="utf-8")
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            cli.edit_drafter = FakeEditDrafter()

            output = cli.draft_pending_edit(
                file_path="notes.txt",
                instruction="replace call",
                file_content=(repo_root / "notes.txt").read_text(encoding="utf-8"),
            )

            self.assertIn("Drafted edit:", output)
            self.assertIn("Minimal replacement.", output)
            self.assertIn("Pending edit updated from draft.", output)
            self.assertIsNotNone(cli.session.pending_edit)
            assert cli.session.pending_edit is not None
            self.assertEqual(cli.session.pending_edit.rationale, "Minimal replacement.")
            self.assertIsNone(cli.session.pending_edit.source_artifact_id)

    def test_draft_pending_edit_retries_once_after_preview_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "notes.txt").write_text("call();\n", encoding="utf-8")
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            drafter = RepairingFakeEditDrafter()
            cli.edit_drafter = drafter

            output = cli.draft_pending_edit(
                file_path="notes.txt",
                instruction="replace call",
                file_content=(repo_root / "notes.txt").read_text(encoding="utf-8"),
            )

            self.assertIn("Pending edit updated from draft.", output)
            self.assertIn("Applied one repair retry after:", output)
            self.assertIsNotNone(drafter.retry_feedback)
            assert drafter.retry_feedback is not None
            self.assertIn("preview_status: missing", drafter.retry_feedback)
            self.assertEqual(drafter.draft_calls, 2)
            self.assertIsNotNone(cli.session.pending_edit)
            assert cli.session.pending_edit is not None
            self.assertEqual(cli.session.pending_edit.old_string, "call();")

    def test_draft_edit_command_is_not_public_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "notes.txt").write_text("call();\n", encoding="utf-8")
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            cli.edit_drafter = None

            output = cli.handle_command('/draft-edit notes.txt "replace call"')

            self.assertIn("Unknown command: /draft-edit", output)
            self.assertNotIn("/draft-edit", output.split("\n\n", 1)[1])


if __name__ == "__main__":
    unittest.main()
