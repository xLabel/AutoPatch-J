from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from autopatch_j.scanners import DEFAULT_SCANNER_NAME, JavaScanner, ScanResult, get_scanner
from autopatch_j.tools.base import Tool, ToolExecutionResult, ToolName


@dataclass(slots=True)
class ScanTool(Tool):
    scanner: JavaScanner | None = None

    name = ToolName.SCAN
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
        result = scan(repo_root, scope or ["."], scanner=self.scanner)
        return ToolExecutionResult(
            tool_name=self.name,
            status=result.status,
            message=result.message,
            payload=result,
        )


def scan(
    repo_root: Path,
    scope: list[str],
    scanner: JavaScanner | None = None,
) -> ScanResult:
    active_scanner = scanner
    if active_scanner is None:
        active_scanner = cast(JavaScanner | None, get_scanner(DEFAULT_SCANNER_NAME))

    if active_scanner is None:
        return ScanResult(
            engine="autopatch-j",
            scope=list(scope),
            targets=[],
            status="error",
            message=f"Default scanner is unavailable: {DEFAULT_SCANNER_NAME}",
            summary={"total": 0},
            findings=[],
        )

    return active_scanner.scan(repo_root, scope)
