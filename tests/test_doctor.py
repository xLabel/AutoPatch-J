from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autopatch_j.cli import AutoPatchCLI
from autopatch_j.doctor import build_doctor_report
from autopatch_j.project import initialize_project
from autopatch_j.scanners import SemgrepScanner


class DoctorReportTests(unittest.TestCase):
    def test_doctor_report_explains_missing_runtime_dependencies(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("autopatch_j.doctor.shutil.which", return_value=None):
                with patch("autopatch_j.doctor.importlib.util.find_spec", return_value=None):
                    report = build_doctor_report(
                        repo_root=None,
                        scanner=SemgrepScanner(),
                        decision_engine_label="rule-based",
                        edit_drafter_label=None,
                    )

        checks = {check.name: check for check in report.checks}
        self.assertEqual(checks["project"].status, "unavailable")
        self.assertEqual(checks["scanner"].status, "error")
        self.assertEqual(checks["java_syntax_validator"].status, "unavailable")
        self.assertEqual(checks["openai_decision_engine"].status, "unavailable")
        self.assertEqual(checks["openai_edit_drafter"].status, "unavailable")

    def test_doctor_report_marks_ready_runtime_dependencies(self) -> None:
        fake_spec = object()

        def fake_find_spec(name: str) -> object | None:
            if name in {"tree_sitter", "tree_sitter_java"}:
                return fake_spec
            return None

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with patch("autopatch_j.doctor.shutil.which", return_value="/usr/local/bin/semgrep"):
                with patch("autopatch_j.doctor.importlib.util.find_spec", side_effect=fake_find_spec):
                    report = build_doctor_report(
                        repo_root=Path("/tmp/demo"),
                        scanner=SemgrepScanner(config="rules/demo.yml"),
                        decision_engine_label="openai:gpt-5.4-mini",
                        edit_drafter_label="openai:gpt-5.4-mini",
                    )

        checks = {check.name: check for check in report.checks}
        self.assertEqual(checks["project"].status, "ok")
        self.assertEqual(checks["scanner"].status, "ok")
        self.assertIn("rules/demo.yml", checks["scanner"].message)
        self.assertEqual(checks["java_syntax_validator"].status, "ok")
        self.assertEqual(checks["openai_decision_engine"].status, "ok")
        self.assertEqual(checks["openai_edit_drafter"].status, "ok")

    def test_doctor_command_formats_report_for_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)
            cli = AutoPatchCLI(repo_root)

            with patch.dict(os.environ, {}, clear=True):
                with patch("autopatch_j.doctor.shutil.which", return_value=None):
                    with patch("autopatch_j.doctor.importlib.util.find_spec", return_value=None):
                        output = cli.handle_command("/doctor")

        self.assertIn("Doctor report:", output)
        self.assertIn("- project: ok", output)
        self.assertIn("- scanner: error", output)
        self.assertIn("- openai_decision_engine: unavailable", output)


if __name__ == "__main__":
    unittest.main()
