from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.session import PendingEdit
from autopatch_j.tools.scan_java import Finding, ScanResult
from autopatch_j.validators.rescan import validate_post_apply_rescan


class RescanValidatorTests(unittest.TestCase):
    def test_rescan_skips_without_finding_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pending = PendingEdit(
                file_path="notes.txt",
                old_string="call();",
                new_string="safeCall();",
                diff="",
                validation_status="skipped",
                validation_message="not java",
            )
            result, scan = validate_post_apply_rescan(Path(tmpdir), pending)

            self.assertEqual(result.status, "skipped")
            self.assertIsNone(scan)

    def test_rescan_marks_failure_when_original_check_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pending = PendingEdit(
                file_path="Demo.java",
                old_string="call();",
                new_string="safeCall();",
                diff="",
                validation_status="ok",
                validation_message="validated",
                source_artifact_id="scan-1",
                source_finding_index=1,
                source_check_id="java.lang.correctness.demo",
            )

            def fake_scanner(repo_root: Path, scope: list[str]) -> ScanResult:
                del repo_root
                del scope
                return ScanResult(
                    engine="semgrep",
                    scope=["Demo.java"],
                    targets=["Demo.java"],
                    status="ok",
                    message="done",
                    summary={"total": 1, "error": 1},
                    findings=[
                        Finding(
                            check_id="java.lang.correctness.demo",
                            path="Demo.java",
                            start_line=3,
                            end_line=3,
                            severity="error",
                            message="still there",
                            rule="CWE-476",
                            snippet="call();",
                        )
                    ],
                )

            result, scan = validate_post_apply_rescan(Path(tmpdir), pending, scanner=fake_scanner)

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.remaining_matches, 1)
            self.assertIsNotNone(scan)

    def test_rescan_marks_success_when_original_check_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pending = PendingEdit(
                file_path="Demo.java",
                old_string="call();",
                new_string="safeCall();",
                diff="",
                validation_status="ok",
                validation_message="validated",
                source_artifact_id="scan-1",
                source_finding_index=1,
                source_check_id="java.lang.correctness.demo",
            )

            def fake_scanner(repo_root: Path, scope: list[str]) -> ScanResult:
                del repo_root
                del scope
                return ScanResult(
                    engine="semgrep",
                    scope=["Demo.java"],
                    targets=["Demo.java"],
                    status="ok",
                    message="done",
                    summary={"total": 0},
                    findings=[],
                )

            result, scan = validate_post_apply_rescan(Path(tmpdir), pending, scanner=fake_scanner)

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.remaining_matches, 0)
            self.assertIsNotNone(scan)

    def test_rescan_propagates_scanner_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pending = PendingEdit(
                file_path="Demo.java",
                old_string="call();",
                new_string="safeCall();",
                diff="",
                validation_status="ok",
                validation_message="validated",
                source_artifact_id="scan-1",
                source_finding_index=1,
                source_check_id="java.lang.correctness.demo",
            )

            def fake_scanner(repo_root: Path, scope: list[str]) -> ScanResult:
                del repo_root
                del scope
                return ScanResult(
                    engine="semgrep",
                    scope=["Demo.java"],
                    targets=["Demo.java"],
                    status="error",
                    message="semgrep missing",
                    summary={"total": 0},
                    findings=[],
                )

            result, scan = validate_post_apply_rescan(Path(tmpdir), pending, scanner=fake_scanner)

            self.assertEqual(result.status, "error")
            self.assertIn("semgrep missing", result.message)
            self.assertIsNotNone(scan)
