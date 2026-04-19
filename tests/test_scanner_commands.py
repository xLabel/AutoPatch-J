from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autopatch_j.cli import AutoPatchCLI
from autopatch_j.project import initialize_project


class ScannerCommandTests(unittest.TestCase):
    def test_scanner_command_shows_current_scanner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)

            with patch.dict(os.environ, {}, clear=True):
                cli = AutoPatchCLI(repo_root)
                output = cli.handle_command("/scanner")

        self.assertIn("Scanner config:", output)
        self.assertIn("- active: semgrep:p/java", output)

    def test_scanner_command_persists_project_semgrep_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)

            with patch.dict(os.environ, {}, clear=True):
                cli = AutoPatchCLI(repo_root)
                output = cli.handle_command("/scanner semgrep rules/demo.yml")
                reloaded_cli = AutoPatchCLI(repo_root)
                reloaded_output = reloaded_cli.handle_command("/scanner")

        self.assertIn("Scanner config updated.", output)
        self.assertIn("- active: semgrep:rules/demo.yml", output)
        self.assertIn("- active: semgrep:rules/demo.yml", reloaded_output)
        self.assertIn("- project semgrep config: rules/demo.yml", reloaded_output)

    def test_scanner_reset_clears_project_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)

            with patch.dict(os.environ, {}, clear=True):
                cli = AutoPatchCLI(repo_root)
                cli.handle_command("/scanner semgrep rules/demo.yml")
                output = cli.handle_command("/scanner reset")

        self.assertIn("Scanner config reset to env/default.", output)
        self.assertIn("- active: semgrep:p/java", output)
        self.assertIn("- project scanner: (none)", output)
        self.assertIn("- project semgrep config: (none)", output)


if __name__ == "__main__":
    unittest.main()
