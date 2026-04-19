from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autopatch_j.scanners import ScanResult
from autopatch_j.scanners import SemgrepScanner, build_default_java_scanner
from autopatch_j.scanners.semgrep import default_semgrep_config, platform_tag, semgrep_binary_name
from autopatch_j.tools.scan_java import scan_java


class ScannerFactoryTests(unittest.TestCase):
    def test_build_default_scanner_uses_semgrep_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            scanner = build_default_java_scanner()

        self.assertIsInstance(scanner, SemgrepScanner)
        self.assertEqual(scanner.label, "semgrep:autopatch-j/java-default")
        self.assertEqual(scanner.config, default_semgrep_config())
        self.assertTrue(Path(scanner.config).exists())

    def test_semgrep_scanner_uses_runtime_binary_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            autopatch_root = Path(tmpdir) / "autopatch"
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir()
            (repo_root / "src").mkdir()
            (repo_root / "src" / "Demo.java").write_text("class Demo {}\n", encoding="utf-8")
            binary = autopatch_root / "runtime" / "semgrep" / "bin" / platform_tag() / semgrep_binary_name()
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)

            with patch("autopatch_j.scanners.semgrep.repo_root_from_module", return_value=autopatch_root):
                scanner = SemgrepScanner()
                with patch("autopatch_j.scanners.semgrep.subprocess.run") as run_mock:
                    run_mock.return_value.stdout = '{"results": []}'
                    run_mock.return_value.stderr = ""
                    run_mock.return_value.returncode = 0
                    result = scanner.scan(repo_root, ["src"])

        self.assertEqual(result.status, "ok")
        command = run_mock.call_args.args[0]
        self.assertEqual(command[0], str(binary.resolve()))
        self.assertEqual(command[1:4], ["scan", "--json", "--config"])
        self.assertEqual(command[4], str(autopatch_root / "runtime" / "semgrep" / "rules" / "java.yml"))
        runtime_env = run_mock.call_args.kwargs["env"]
        self.assertEqual(runtime_env["SEMGREP_SEND_METRICS"], "off")
        self.assertEqual(runtime_env["SEMGREP_ENABLE_VERSION_CHECK"], "0")

    def test_semgrep_scanner_localizes_runtime_state_inside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            autopatch_root = Path(tmpdir) / "autopatch"
            repo_root = Path(tmpdir) / "repo"
            repo_root.mkdir()
            (repo_root / "src").mkdir()
            (repo_root / "src" / "Demo.java").write_text("class Demo {}\n", encoding="utf-8")
            binary = autopatch_root / "runtime" / "semgrep" / "bin" / platform_tag() / semgrep_binary_name()
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)

            with patch("autopatch_j.scanners.semgrep.repo_root_from_module", return_value=autopatch_root):
                scanner = SemgrepScanner()
                with patch("autopatch_j.scanners.semgrep.detect_certifi_bundle", return_value="/tmp/cert.pem"):
                    with patch("autopatch_j.scanners.semgrep.subprocess.run") as run_mock:
                        run_mock.return_value.stdout = '{"results": []}'
                        run_mock.return_value.stderr = ""
                        run_mock.return_value.returncode = 0
                        scanner.scan(repo_root, ["src"])

                        runtime_env = run_mock.call_args.kwargs["env"]
                        self.assertEqual(runtime_env["SSL_CERT_FILE"], "/tmp/cert.pem")
                        self.assertTrue(runtime_env["XDG_CONFIG_HOME"].startswith(str(repo_root)))
                        self.assertTrue(runtime_env["XDG_CACHE_HOME"].startswith(str(repo_root)))
                        self.assertTrue(runtime_env["SEMGREP_LOG_FILE"].startswith(str(repo_root)))
                        self.assertTrue(runtime_env["SEMGREP_SETTINGS_FILE"].startswith(str(repo_root)))
                        self.assertTrue((repo_root / ".autopatch" / "runtime" / "semgrep").exists())

    def test_semgrep_scanner_resolves_only_repo_runtime_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            autopatch_root = Path(tmpdir)
            binary = autopatch_root / "runtime" / "semgrep" / "bin" / platform_tag() / semgrep_binary_name()
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)

            with patch("autopatch_j.scanners.semgrep.repo_root_from_module", return_value=autopatch_root):
                scanner = SemgrepScanner()
                resolved = scanner.resolve_binary_with_source(Path("."))

        self.assertEqual(resolved, (str(binary.resolve()), "local runtime"))

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
