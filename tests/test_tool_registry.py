from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autopatch_j.scanners import ScanResult
from autopatch_j.tools.registry import ToolRegistry


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


class ToolRegistryTests(unittest.TestCase):
    def test_registry_returns_error_for_unknown_tool(self) -> None:
        registry = ToolRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = registry.execute(Path(tmpdir), "unknown_tool", {})

        self.assertEqual(result.status, "error")
        self.assertEqual(result.message, "Unsupported tool: unknown_tool")

    def test_registry_runs_preview_search_replace(self) -> None:
        registry = ToolRegistry()
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / "Demo.java"
            target.write_text("class Demo { void run() { call(); } }\n", encoding="utf-8")

            result = registry.execute(
                repo_root,
                "preview_search_replace",
                {
                    "file_path": "Demo.java",
                    "old_string": "call();",
                    "new_string": "safeCall();",
                },
            )

        self.assertEqual(result.status, "ok")
        self.assertIn("safeCall();", result.payload.diff)

    def test_registry_uses_injected_scanner_for_scan_java(self) -> None:
        registry = ToolRegistry(scanner=FakeScanner())
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            result = registry.execute(
                repo_root,
                "scan_java",
                {"scope": ["src/Demo.java"]},
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.payload.engine, "fake-scanner")
        self.assertEqual(result.payload.targets, ["src/Demo.java"])


if __name__ == "__main__":
    unittest.main()
