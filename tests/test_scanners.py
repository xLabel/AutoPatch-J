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
