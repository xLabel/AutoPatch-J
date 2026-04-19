from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class Finding:
    check_id: str
    path: str
    start_line: int
    end_line: int
    severity: str
    message: str
    rule: str
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "severity": self.severity,
            "message": self.message,
            "rule": self.rule,
            "snippet": self.snippet,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Finding":
        return cls(
            check_id=str(data.get("check_id", "")),
            path=str(data.get("path", "")),
            start_line=int(data.get("start_line", 0)),
            end_line=int(data.get("end_line", 0)),
            severity=str(data.get("severity", "")),
            message=str(data.get("message", "")),
            rule=str(data.get("rule", "")),
            snippet=str(data.get("snippet", "")),
        )


@dataclass(slots=True)
class ScanResult:
    engine: str
    scope: list[str]
    targets: list[str]
    status: str
    message: str
    summary: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "engine": self.engine,
            "scope": list(self.scope),
            "targets": list(self.targets),
            "status": self.status,
            "message": self.message,
            "summary": dict(self.summary),
            "findings": [finding.to_dict() for finding in self.findings],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ScanResult":
        findings_raw = data.get("findings", [])
        findings = [
            Finding.from_dict(item)
            for item in findings_raw
            if isinstance(item, dict)
        ]
        summary = data.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}
        return cls(
            engine=str(data.get("engine", "")),
            scope=[str(item) for item in data.get("scope", [])],
            targets=[str(item) for item in data.get("targets", [])],
            status=str(data.get("status", "")),
            message=str(data.get("message", "")),
            summary={str(key): int(value) for key, value in summary.items()},
            findings=findings,
        )


class JavaScanner(Protocol):
    @property
    def label(self) -> str:
        """Return a short label for status output."""

    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        """Run a Java scanner for the selected repository scope."""
