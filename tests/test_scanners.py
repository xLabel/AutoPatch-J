from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autopatch_j.scanners import ScanResult
from autopatch_j.scanners import SemgrepScanner, build_default_java_scanner
from autopatch_j.tools.scan_java import scan_java


class ScannerFactoryTests(unittest.TestCase):
    def test_build_default_scanner_uses_semgrep_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            scanner = build_default_java_scanner()

        self.assertIsInstance(scanner, SemgrepScanner)
        self.assertEqual(scanner.label, "semgrep:p/java")

    def test_build_default_scanner_allows_custom_semgrep_config(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTOPATCH_SCANNER": "semgrep", "AUTOPATCH_SEMGREP_CONFIG": "rules/demo.yml"},
            clear=True,
        ):
            scanner = build_default_java_scanner()

        self.assertIsInstance(scanner, SemgrepScanner)
        self.assertEqual(scanner.label, "semgrep:rules/demo.yml")

    def test_build_default_scanner_allows_custom_semgrep_binary(self) -> None:
        with patch.dict(
            os.environ,
            {"AUTOPATCH_SCANNER": "semgrep", "AUTOPATCH_SEMGREP_BIN": "/opt/semgrep/bin/semgrep"},
            clear=True,
        ):
            scanner = build_default_java_scanner()

        self.assertIsInstance(scanner, SemgrepScanner)
        self.assertEqual(scanner.binary_path, "/opt/semgrep/bin/semgrep")

    def test_semgrep_scanner_uses_explicit_binary_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "src").mkdir()
            (repo_root / "src" / "Demo.java").write_text("class Demo {}\n", encoding="utf-8")
            binary = repo_root / "tools" / "semgrep"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)
            scanner = SemgrepScanner(config="rules/demo.yml", binary_path="tools/semgrep")

            with patch("autopatch_j.scanners.semgrep.subprocess.run") as run_mock:
                run_mock.return_value.stdout = '{"results": []}'
                run_mock.return_value.stderr = ""
                run_mock.return_value.returncode = 0
                result = scanner.scan(repo_root, ["src"])

        self.assertEqual(result.status, "ok")
        command = run_mock.call_args.args[0]
        self.assertEqual(command[0], str(binary.resolve()))
        self.assertEqual(command[1:4], ["scan", "--json", "--config"])

    def test_unsupported_scanner_returns_controlled_error(self) -> None:
        with patch.dict(os.environ, {"AUTOPATCH_SCANNER": "spotbugs"}, clear=True):
            scanner = build_default_java_scanner()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = scanner.scan(Path(tmpdir), ["src"])

        self.assertEqual(result.status, "error")
        self.assertEqual(result.engine, "spotbugs")
        self.assertIn("Unsupported Java scanner", result.message)

    def test_scan_java_uses_injected_scanner(self) -> None:
        class FakeScanner:
            label = "fake-scanner"

            def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
                del repo_root
                return ScanResult(
                    engine="fake-scanner",
                    scope=list(scope),
                    targets=list(scope),
                    status="ok",
                    message="fake scanner executed",
                    summary={"total": 0},
                    findings=[],
                )

        result = scan_java(Path("."), ["src"], scanner=FakeScanner())
        self.assertEqual(result.engine, "fake-scanner")
        self.assertEqual(result.targets, ["src"])


if __name__ == "__main__":
    unittest.main()
