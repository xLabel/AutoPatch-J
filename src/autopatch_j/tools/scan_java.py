from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autopatch_j.scanners import Finding, JavaScanner, ScanResult, build_java_scanner
from autopatch_j.scanners.semgrep import normalize_semgrep_payload, select_targets
from autopatch_j.tools.base import Tool, ToolExecutionResult


@dataclass(slots=True)
class ScanJavaTool(Tool):
    scanner: JavaScanner | None = None

    name = "scan_java"
    description = "Run the Java static scanner for the selected repository scope."
    parameters = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Repository-relative files or directories. Use ['.'] for the whole repo.",
            }
        },
        "required": ["scope"],
    }

    def execute(self, repo_root: Path, scope: list[str] | None = None) -> ToolExecutionResult:
        result = scan_java(repo_root, scope or ["."], scanner=self.scanner)
        return ToolExecutionResult(
            tool_name=self.name,
            status=result.status,
            message=result.message,
            payload=result,
        )


def scan_java(
    repo_root: Path,
    scope: list[str],
    scanner: JavaScanner | None = None,
) -> ScanResult:
    active_scanner = scanner or build_java_scanner()
    return active_scanner.scan(repo_root, scope)


__all__ = [
    "Finding",
    "ScanResult",
    "ScanJavaTool",
    "normalize_semgrep_payload",
    "scan_java",
    "select_targets",
]
