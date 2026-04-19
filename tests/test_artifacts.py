from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.artifacts import load_scan_result, save_scan_result
from autopatch_j.session import ensure_project_layout
from autopatch_j.tools.scan_java import Finding, ScanResult


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


if __name__ == "__main__":
    unittest.main()
