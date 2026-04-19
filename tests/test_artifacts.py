from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.artifacts import (
    load_scan_result,
    load_validation_result,
    save_scan_result,
    save_validation_result,
)
from autopatch_j.session import PendingEdit, ensure_project_layout
from autopatch_j.tools.scan_java import Finding, ScanResult
from autopatch_j.validators.rescan import RescanValidationResult


class ArtifactTests(unittest.TestCase):
    def test_scan_result_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_project_layout(repo_root)
            original = ScanResult(
                engine="semgrep",
                scope=["src/main/java/demo/App.java"],
                targets=["src/main/java/demo/App.java"],
                status="ok",
                message="Semgrep completed with 1 finding(s).",
                summary={"total": 1, "error": 1},
                findings=[
                    Finding(
                        check_id="java.lang.correctness.demo",
                        path="src/main/java/demo/App.java",
                        start_line=12,
                        end_line=12,
                        severity="error",
                        message="Avoid direct string equality on nullable values",
                        rule="CWE-476",
                        snippet='if (user.getName().equals("admin")) {',
                    )
                ],
            )

            artifact_id = save_scan_result(repo_root, original)
            loaded = load_scan_result(repo_root, artifact_id)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.engine, original.engine)
            self.assertEqual(loaded.summary, original.summary)
            self.assertEqual(loaded.findings[0].check_id, original.findings[0].check_id)

    def test_validation_result_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            ensure_project_layout(repo_root)
            original = RescanValidationResult(
                status="ok",
                message="Post-apply ReScan no longer reports the original finding.",
                source_artifact_id="scan-1",
                source_finding_index=1,
                source_check_id="java.lang.correctness.demo",
                source_path="Demo.java",
                rescan_artifact_id="scan-2",
                remaining_matches=0,
            )

            artifact_id = save_validation_result(repo_root, original)
            loaded = load_validation_result(repo_root, artifact_id)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.status, original.status)
            self.assertEqual(loaded.rescan_artifact_id, original.rescan_artifact_id)


if __name__ == "__main__":
    unittest.main()
