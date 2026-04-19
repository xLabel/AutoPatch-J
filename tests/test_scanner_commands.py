from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autopatch_j.cli import AutoPatchCLI
from autopatch_j.project import initialize_project


class ToolCommandTests(unittest.TestCase):
    def test_tools_command_shows_readiness_without_exposing_scanner_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)

            with patch.dict(os.environ, {}, clear=True):
                with patch("autopatch_j.scanners.semgrep.shutil.which", return_value=None):
                    with patch("autopatch_j.scanners.semgrep.resolve_repo_runtime_binary", return_value=None):
                        with patch("autopatch_j.scanners.semgrep.resolve_repo_venv_binary", return_value=None):
                            cli = AutoPatchCLI(repo_root)
                            output = cli.handle_command("/tools")

        self.assertIn("Tool readiness:", output)
        self.assertIn("- scanner: error", output)
        self.assertIn("- java_syntax_validator:", output)
        self.assertNotIn("Scanner config:", output)
        self.assertNotIn("project semgrep config", output)

    def test_scanner_command_is_not_public_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)
            cli = AutoPatchCLI(repo_root)

            output = cli.handle_command("/scanner")

        self.assertIn("Unknown command: /scanner", output)
        self.assertNotIn("/scanner", output.split("\n\n", 1)[1])


if __name__ == "__main__":
    unittest.main()
