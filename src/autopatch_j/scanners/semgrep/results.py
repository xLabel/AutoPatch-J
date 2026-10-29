"""Semgrep 原始结果的完整性校验及稳定 finding 身份构造。"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import hashlib
import json
import pathlib

from autopatch_j.core import finding as core_finding
from autopatch_j.core.project import repo_path
from autopatch_j.core.project import source_reader
from autopatch_j.scanners import models

_FINGERPRINT_VERSION = b"autopatch-j-finding-v1"
_MAX_ERROR_SUMMARY_ITEMS = 3


@dataclasses.dataclass(slots=True)
class _PreparedFinding:
    check_id: str
    path: str
    region: core_finding.SourceRegion
    severity: str
    message: str
    rule: str
    snippet: str
    base_hash: str
    fingerprint: str = ""


def build_semgrep_scan_result(
    payload: dict[str, object],
    repo_root: pathlib.Path,
    scope: list[str],
    targets: list[str],
) -> models.ScanResult:
    """校验整次 Semgrep JSON；任一证据无效时拒绝部分成功。"""
    missing_fields = [
        key for key in ("results", "errors") if key not in payload
    ]
    if missing_fields:
        return _error_result(
            scope,
            targets,
            f"Semgrep payload 缺少必需字段：{", ".join(missing_fields)}。",
        )

    raw_errors = payload["errors"]
    if not isinstance(raw_errors, list):
        return _error_result(
            scope, targets, "Semgrep errors 字段结构不符合预期。"
        )
    if raw_errors:
        return _error_result(
            scope, targets, _summarize_semgrep_errors(raw_errors)
        )

    raw_results = payload["results"]
    if not isinstance(raw_results, list):
        return _error_result(
            scope, targets, "Semgrep results 字段结构不符合预期。"
        )

    code_fetcher = source_reader.SourceReader(repo_root)
    prepared: list[_PreparedFinding] = []
    try:
        for index, item in enumerate(raw_results, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index} 个 result 不是 JSON object。")
            prepared.append(_prepare_finding(item, repo_root, code_fetcher))
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        return _error_result(
            scope, targets, f"Semgrep finding 证据不完整：{exc}"
        )

    _assign_fingerprints(prepared)
    findings = [
        models.Finding(
            fingerprint=item.fingerprint,
            check_id=item.check_id,
            path=item.path,
            region=item.region,
            severity=item.severity,
            message=item.message,
            rule=item.rule,
            snippet=item.snippet,
        )
        for item in prepared
    ]
    return models.ScanResult(
        engine="semgrep",
        scope=scope,
        targets=targets,
        status="ok",
        message=f"Semgrep 扫描完成，发现 {len(findings)} 个问题。",
        findings=findings,
    )


def _prepare_finding(
    item: Mapping[str, object],
    repo_root: pathlib.Path,
    code_fetcher: source_reader.SourceReader,
) -> _PreparedFinding:
    """校验单个 Semgrep finding 的路径、区域与当前源码字节证据。"""
    check_id = normalize_check_id(_required_text(item, "check_id"))
    if not check_id:
        raise ValueError("check_id 不能为空。")

    finding_path, source_path = normalize_result_path(
        repo_root, _required_text(item, "path")
    )
    if not source_path.is_file():
        raise ValueError(f"finding path 不是仓库内文件：{finding_path}")

    region = core_finding.SourceRegion(
        start_line=_required_nested_int(item, "start", "line"),
        start_column=_required_nested_int(item, "start", "col"),
        end_line=_required_nested_int(item, "end", "line"),
        end_column=_required_nested_int(item, "end", "col"),
        start_offset=_required_nested_int(item, "start", "offset"),
        end_offset=_required_nested_int(item, "end", "offset"),
    )
    source_bytes = source_path.read_bytes()
    _validate_region(region, source_bytes)
    matched_bytes = _normalize_line_endings(
        source_bytes[region.start_offset : region.end_offset]
    )

    extra = item.get("extra", {})
    if not isinstance(extra, dict):
        raise ValueError("extra 字段不是 JSON object。")
    fallback_snippet = str(extra.get("lines", "")).strip()
    snippet = code_fetcher.fetch_resolved_snippet(
        file_path=finding_path,
        start_line=region.start_line,
        end_line=region.inclusive_end_line,
        fallback_snippet=fallback_snippet,
    )
    base_hash = _build_base_hash(
        engine="semgrep",
        check_id=check_id,
        path=finding_path,
        matched_bytes=matched_bytes,
    )
    return _PreparedFinding(
        check_id=check_id,
        path=finding_path,
        region=region,
        severity=str(extra.get("severity", "unknown")).lower(),
        message=str(extra.get("message", "")),
        rule=extract_rule(extra),
        snippet=snippet,
        base_hash=base_hash,
    )


def _assign_fingerprints(findings: list[_PreparedFinding]) -> None:
    """按证据和源码位置分配稳定指纹，不依赖结果数组顺序。"""
    groups: dict[str, list[_PreparedFinding]] = {}
    for finding in findings:
        groups.setdefault(finding.base_hash, []).append(finding)
    for base_hash, group in groups.items():
        ordered = sorted(
            group,
            key=lambda finding: (
                finding.region.start_offset,
                finding.region.end_offset,
            ),
        )
        for ordinal, finding in enumerate(ordered, start=1):
            finding.fingerprint = f"apj-v1:{base_hash}:{ordinal}"


def _build_base_hash(
    *,
    engine: str,
    check_id: str,
    path: str,
    matched_bytes: bytes,
) -> str:
    """对版本、引擎、规则、路径及匹配 bytes 构造证据摘要。"""
    digest = hashlib.sha256()
    for part in (
        _FINGERPRINT_VERSION,
        engine.encode("utf-8"),
        check_id.encode("utf-8"),
        path.encode("utf-8"),
        matched_bytes,
    ):
        digest.update(len(part).to_bytes(8, byteorder="big"))
        digest.update(part)
    return digest.hexdigest()


def _validate_region(
    region: core_finding.SourceRegion, source_bytes: bytes
) -> None:
    """校验扫描坐标与实际源码 bytes 一致，证据缺失即拒绝。"""
    if region.end_offset <= region.start_offset:
        raise ValueError("finding region 必须包含非空源码证据。")
    if region.end_offset > len(source_bytes):
        raise ValueError("finding region 的字节偏移超出源码范围。")

    actual_start_line = source_bytes.count(b"\n", 0, region.start_offset) + 1
    actual_end_line = source_bytes.count(b"\n", 0, region.end_offset) + 1
    if (
        region.start_line != actual_start_line
        or region.end_line != actual_end_line
    ):
        raise ValueError("finding region 的行号与字节偏移不一致。")

    start_line_offset = source_bytes.rfind(b"\n", 0, region.start_offset) + 1
    end_line_offset = source_bytes.rfind(b"\n", 0, region.end_offset) + 1
    if region.start_column not in _column_candidates(
        source_bytes[start_line_offset : region.start_offset]
    ) or region.end_column not in _column_candidates(
        source_bytes[end_line_offset : region.end_offset]
    ):
        raise ValueError("finding region 的列号与字节偏移不一致。")


def _column_candidates(line_prefix: bytes) -> set[int]:
    candidates = {len(line_prefix) + 1}
    for encoding in ("utf-8", "gbk"):
        try:
            candidates.add(len(line_prefix.decode(encoding)) + 1)
        except UnicodeDecodeError:
            continue
    return candidates


def _normalize_line_endings(content: bytes) -> bytes:
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _error_result(
    scope: list[str], targets: list[str], message: str
) -> models.ScanResult:
    return models.ScanResult(
        engine="semgrep",
        scope=scope,
        targets=targets,
        status="error",
        message=message,
        findings=[],
    )


def _summarize_semgrep_errors(errors: list[object]) -> str:
    rendered = [
        _render_error(error) for error in errors[:_MAX_ERROR_SUMMARY_ITEMS]
    ]
    suffix = (
        f"；另有 {len(errors) - len(rendered)} 条错误"
        if len(errors) > len(rendered)
        else ""
    )
    return f"Semgrep 返回不完整扫描结果：{"；".join(rendered)}{suffix}"


def _render_error(error: object) -> str:
    if isinstance(error, dict):
        for key in ("message", "type", "code"):
            value = error.get(key)
            if value:
                return str(value)[:300]
        return json.dumps(error, ensure_ascii=False, sort_keys=True)[:300]
    return str(error)[:300]


def extract_rule(extra: dict[str, object]) -> str:
    """从可用元数据中提取 CWE 或 OWASP 规则说明。"""
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
    """去除扫描器附加路径前缀，保留实际规则 ID。"""
    normalized = raw_check_id.strip()
    for marker in ("autopatch-j.",):
        marker_index = normalized.find(marker)
        if marker_index >= 0:
            return normalized[marker_index:]
    return normalized


def normalize_result_path(
    repo_root: pathlib.Path, raw_path: str
) -> tuple[str, pathlib.Path]:
    """解析扫描结果真实路径并拒绝越界或项目状态目录。"""
    path_text = raw_path.replace("\\", "/").strip()
    if not path_text or "\x00" in path_text:
        raise ValueError("finding path 为空或包含非法字符。")
    candidate = pathlib.Path(path_text)
    try:
        if candidate.is_absolute():
            normalized = repo_path.to_repo_relative_path(repo_root, candidate)
            resolved = candidate.resolve()
        else:
            resolved = repo_path.resolve_repo_path(repo_root, path_text)
            normalized = repo_path.to_repo_relative_path(repo_root, resolved)
    except repo_path.UnsafeRepoPathError as exc:
        raise ValueError(f"finding path 超出仓库范围：{raw_path}") from exc
    if repo_path.is_project_state_path(normalized):
        raise ValueError(f"finding path 不属于源码范围：{raw_path}")
    return normalized, resolved


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 缺失或不是非空字符串。")
    return value.strip()


def _required_nested_int(
    payload: Mapping[str, object], parent: str, key: str
) -> int:
    nested = payload.get(parent)
    if not isinstance(nested, dict):
        raise ValueError(f"{parent} 缺失或不是 JSON object。")
    value = nested.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{parent}.{key} 缺失或不是整数。")
    return value
