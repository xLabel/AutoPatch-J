from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.cli import AutoPatchCLI
from autopatch_j.project import initialize_project


class ReviewCommandTests(unittest.TestCase):
    def test_preview_and_apply_pending_edit(self) -> None:
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

            cli = AutoPatchCLI(repo_root)
            preview_output = cli.handle_command(
                '/preview-edit Demo.java "call();" "safeCall();"'
            )

            self.assertIn("Pending edit updated.", preview_output)
            self.assertIsNotNone(cli.session.pending_edit)
            assert cli.session.pending_edit is not None
            self.assertIn("--- a/Demo.java", cli.session.pending_edit.diff)

            show_output = cli.handle_command("/show-pending")
            self.assertIn("Pending edit:", show_output)
            self.assertIn("safeCall();", show_output)

            apply_output = cli.handle_command("/apply-pending")
            self.assertIn("Pending edit applied.", apply_output)
            self.assertIsNone(cli.session.pending_edit)
            self.assertIn("safeCall();", (repo_root / "Demo.java").read_text(encoding="utf-8"))

    def test_preview_failure_clears_pending_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "Demo.java").write_text("class Demo { void run() { call(); } }\n", encoding="utf-8")
            initialize_project(repo_root)

            cli = AutoPatchCLI(repo_root)
            cli.handle_command('/preview-edit Demo.java "call();" "safeCall();"')
            self.assertIsNotNone(cli.session.pending_edit)

            output = cli.handle_command('/preview-edit Demo.java "missing();" "safeCall();"')

            self.assertIn("Pending edit cleared because preview failed.", output)
            self.assertIsNone(cli.session.pending_edit)


if __name__ == "__main__":
    unittest.main()
