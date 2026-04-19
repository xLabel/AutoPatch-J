from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autopatch_j.cli import AutoPatchCLI
from autopatch_j.project import initialize_project
from autopatch_j.readiness import build_readiness_report
from autopatch_j.scanners import SemgrepScanner


class ReadinessReportTests(unittest.TestCase):
    def test_readiness_report_explains_missing_runtime_dependencies(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("autopatch_j.scanners.semgrep.shutil.which", return_value=None):
                with patch("autopatch_j.scanners.semgrep.resolve_repo_runtime_binary", return_value=None):
                    with patch("autopatch_j.scanners.semgrep.resolve_repo_venv_binary", return_value=None):
                        with patch("autopatch_j.readiness.importlib.util.find_spec", return_value=None):
                            report = build_readiness_report(
                                repo_root=None,
                                scanner=SemgrepScanner(),
                                planner_label="llm:unavailable",
                                edit_drafter_label=None,
                            )

        checks = {check.name: check for check in report.checks}
        self.assertEqual(checks["project"].status, "unavailable")
        self.assertEqual(checks["scanner"].status, "error")
        self.assertEqual(checks["java_syntax_validator"].status, "unavailable")
        self.assertEqual(checks["llm_planner"].status, "unavailable")
        self.assertEqual(checks["llm_patch_drafter"].status, "unavailable")

    def test_readiness_report_marks_ready_runtime_dependencies(self) -> None:
        fake_spec = object()

        def fake_find_spec(name: str) -> object | None:
            if name in {"tree_sitter", "tree_sitter_java"}:
                return fake_spec
            return None

        with patch.dict(os.environ, {"AUTOPATCH_LLM_API_KEY": "test-key"}, clear=True):
            with patch("autopatch_j.scanners.semgrep.shutil.which", return_value="/usr/local/bin/semgrep"):
                with patch("autopatch_j.readiness.importlib.util.find_spec", side_effect=fake_find_spec):
                    report = build_readiness_report(
                        repo_root=Path("/tmp/demo"),
                        scanner=SemgrepScanner(config="rules/demo.yml"),
                        planner_label="openai-compatible:deepseek-chat",
                        edit_drafter_label="openai-compatible:deepseek-chat",
                    )

        checks = {check.name: check for check in report.checks}
        self.assertEqual(checks["project"].status, "ok")
        self.assertEqual(checks["scanner"].status, "ok")
        self.assertIn("rules/demo.yml", checks["scanner"].message)
        self.assertEqual(checks["java_syntax_validator"].status, "ok")
        self.assertEqual(checks["llm_planner"].status, "ok")
        self.assertEqual(checks["llm_patch_drafter"].status, "ok")

    def test_readiness_report_accepts_configured_semgrep_binary(self) -> None:
        fake_spec = object()

        def fake_find_spec(name: str) -> object | None:
            if name in {"tree_sitter", "tree_sitter_java"}:
                return fake_spec
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            binary = repo_root / "runtime" / "semgrep" / "bin" / "test-platform" / "semgrep"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)

            with patch.dict(os.environ, {}, clear=True):
                with patch("autopatch_j.readiness.importlib.util.find_spec", side_effect=fake_find_spec):
                    report = build_readiness_report(
                        repo_root=repo_root,
                        scanner=SemgrepScanner(binary_path=str(binary.relative_to(repo_root))),
                        planner_label="llm:unavailable",
                        edit_drafter_label=None,
                    )

        checks = {check.name: check for check in report.checks}
        self.assertEqual(checks["scanner"].status, "ok")
        self.assertIn("configured binary", checks["scanner"].message)
        self.assertIn("runtime/semgrep", checks["scanner"].message)

    def test_status_command_includes_tool_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)
            cli = AutoPatchCLI(repo_root)

            with patch.dict(os.environ, {}, clear=True):
                with patch("autopatch_j.scanners.semgrep.shutil.which", return_value=None):
                    with patch("autopatch_j.scanners.semgrep.resolve_repo_runtime_binary", return_value=None):
                        with patch("autopatch_j.scanners.semgrep.resolve_repo_venv_binary", return_value=None):
                            with patch("autopatch_j.readiness.importlib.util.find_spec", return_value=None):
                                output = cli.handle_command("/status")

        self.assertIn("AutoPatch-J status:", output)
        self.assertIn("Tool readiness:", output)
        self.assertIn(f"- project: {repo_root.resolve()}", output)
        self.assertIn("- scanner: error", output)
        self.assertIn("Set AUTOPATCH_SEMGREP_BIN", output)
        self.assertIn("- llm_planner: unavailable", output)

    def test_removed_diagnostic_commands_are_not_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            initialize_project(repo_root)
            cli = AutoPatchCLI(repo_root)

            doctor_output = cli.handle_command("/doctor")
            env_output = cli.handle_command("/env")

        self.assertIn("Unknown command: /doctor", doctor_output)
        self.assertIn("/status", doctor_output)
        self.assertNotIn("/env", doctor_output)
        self.assertIn("Unknown command: /env", env_output)


if __name__ == "__main__":
    unittest.main()
