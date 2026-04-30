from __future__ import annotations

from pathlib import Path

from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.scanners.base import Finding, ScanResult


def normalize_semgrep_payload(
    payload: dict[str, object],
    repo_root: Path,
    scope: list[str],
    targets: list[str],
) -> ScanResult:
    raw_results = payload.get("results", [])
    findings: list[Finding] = []
    code_fetcher = CodeFetcher(repo_root)

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        extra = item.get("extra", {})
        if not isinstance(extra, dict):
            extra = {}
        severity = str(extra.get("severity", "unknown")).lower()

        finding_path = str(item.get("path", "")).replace("\\", "/")
        start_line = int(_nested_number(item, "start", "line"))
        end_line = int(_nested_number(item, "end", "line"))
        fallback_snippet = str(extra.get("lines", "")).strip()

        findings.append(
            Finding(
                check_id=normalize_check_id(str(item.get("check_id", ""))),
                path=finding_path,
                start_line=start_line,
                end_line=end_line,
                severity=severity,
                message=str(extra.get("message", "")),
                rule=extract_rule(extra),
                snippet=code_fetcher.fetch_resolved_snippet(
                    file_path=finding_path,
                    start_line=start_line,
                    end_line=end_line,
                    fallback_snippet=fallback_snippet,
                ),
            )
        )

    message = f"Semgrep 扫描完成，发现 {len(findings)} 个问题。"
    return ScanResult(
        engine="semgrep",
        scope=scope,
        targets=targets,
        status="ok",
        message=message,
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


def normalize_check_id(raw_check_id: str) -> str:
    for marker in ("autopatch-j.",):
        marker_index = raw_check_id.find(marker)
        if marker_index >= 0:
            return raw_check_id[marker_index:]
    return raw_check_id


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
