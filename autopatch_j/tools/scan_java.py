from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


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


def scan_java(repo_root: Path, scope: list[str], config: str = "p/java") -> ScanResult:
    targets = select_targets(repo_root, scope)
    if not targets:
        return ScanResult(
            engine="semgrep",
            scope=list(scope),
            targets=[],
            status="skipped",
            message="No Java files or directories were selected for scanning.",
            summary={"total": 0},
            findings=[],
        )

    if shutil.which("semgrep") is None:
        return ScanResult(
            engine="semgrep",
            scope=list(scope),
            targets=targets,
            status="error",
            message="semgrep is not installed or not available on PATH.",
            summary={"total": 0},
            findings=[],
        )

    command = ["semgrep", "scan", "--json", "--config", config, *targets]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode not in {0, 1}:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "semgrep failed"
        return ScanResult(
            engine="semgrep",
            scope=list(scope),
            targets=targets,
            status="error",
            message=stderr,
            summary={"total": 0},
            findings=[],
        )

    payload = json.loads(completed.stdout or "{}")
    return normalize_semgrep_payload(payload, scope=list(scope), targets=targets)


def select_targets(repo_root: Path, scope: list[str]) -> list[str]:
    if not scope:
        return ["."]

    targets: list[str] = []
    for entry in scope:
        candidate = (repo_root / entry).resolve()
        if not candidate.exists():
            continue
        if candidate.is_dir():
            targets.append(Path(entry).as_posix())
            continue
        if candidate.suffix.lower() == ".java":
            targets.append(Path(entry).as_posix())
    return targets


def normalize_semgrep_payload(
    payload: dict[str, object],
    scope: list[str],
    targets: list[str],
) -> ScanResult:
    raw_results = payload.get("results", [])
    findings: list[Finding] = []
    severity_counts: dict[str, int] = {}

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        extra = item.get("extra", {})
        if not isinstance(extra, dict):
            extra = {}
        severity = str(extra.get("severity", "unknown")).lower()
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        findings.append(
            Finding(
                check_id=str(item.get("check_id", "")),
                path=str(item.get("path", "")),
                start_line=int(_nested_number(item, "start", "line")),
                end_line=int(_nested_number(item, "end", "line")),
                severity=severity,
                message=str(extra.get("message", "")),
                rule=extract_rule(extra),
                snippet=str(extra.get("lines", "")).strip(),
            )
        )

    summary = {"total": len(findings), **severity_counts}
    message = f"Semgrep completed with {len(findings)} finding(s)."
    return ScanResult(
        engine="semgrep",
        scope=scope,
        targets=targets,
        status="ok",
        message=message,
        summary=summary,
        findings=findings,
    )


def extract_rule(extra: dict[str, object]) -> str:
    metadata = extra.get("metadata", {})
    if not isinstance(metadata, dict):
        return ""
    cwe = metadata.get("cwe")
    if cwe:
        return str(cwe)
    owasp = metadata.get("owasp")
    if owasp:
        return str(owasp)
    return ""


def _nested_number(payload: dict[str, object], *keys: str) -> int:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return 0
        current = current.get(key, 0)
    try:
        return int(current)
    except (TypeError, ValueError):
        return 0
