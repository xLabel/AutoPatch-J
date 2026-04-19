from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autopatch_j.cli import AutoPatchCLI
from autopatch_j.project import initialize_project


class ScannerCommandTests(unittest.TestCase):
    def test_scanners_command_shows_scanner_choices(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)

            with patch.dict(os.environ, {}, clear=True):
                with patch("autopatch_j.scanners.semgrep.resolve_repo_runtime_binary", return_value=None):
                    cli = AutoPatchCLI(repo_root)
                    output = cli.handle_command("/scanners")

        self.assertIn("Java scanners:", output)
        self.assertIn("[selected] Semgrep", output)
        self.assertIn("runtime missing", output)
        self.assertIn("PMD", output)
        self.assertIn("SpotBugs", output)
        self.assertIn("Checkstyle", output)
        self.assertNotIn("java_syntax_validator", output)

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
