from __future__ import annotations

from pathlib import Path

from autopatch_j.project import ProjectSummary
from autopatch_j.readiness import ReadinessReport
from autopatch_j.scanners import ScanResult
from autopatch_j.tools.patch import PatchPreview
from autopatch_j.validators import RescanValidationResult


def format_init_summary(summary: ProjectSummary) -> str:
    return (
        "项目已初始化：\n"
        f"- repo_root: {summary.repo_root}\n"
        f"- indexed entries: {summary.indexed_entries}\n"
        f"- indexed files: {summary.indexed_files}\n"
        f"- indexed directories: {summary.indexed_directories}\n"
        f"- indexed Java files: {summary.indexed_java_files}"
    )


def format_reindex_summary(summary: ProjectSummary) -> str:
    return (
        "索引已刷新：\n"
        f"- repo_root: {summary.repo_root}\n"
        f"- indexed entries: {summary.indexed_entries}\n"
        f"- indexed files: {summary.indexed_files}\n"
        f"- indexed directories: {summary.indexed_directories}\n"
        f"- indexed Java files: {summary.indexed_java_files}"
    )


def format_readiness_report(report: ReadinessReport) -> str:
    lines = ["运行环境："]
    for check in report.checks:
        if check.name == "project":
            continue
        lines.append(f"- {check.name}: {check.status}")
        lines.append(f"  {check.message}")
    return "\n".join(lines)


def format_scanners_report(scanners: list[object], repo_root: Path | None) -> str:
    lines = ["Java 静态扫描器："]
    for scanner in scanners:
        scanner_meta = scanner.get_scanner(repo_root)
        selector = "selected" if scanner_meta.selected else "disabled"
        lines.append(f"- [{selector}] {scanner_meta.name}: {scanner_meta.status}")
        lines.append(f"  {scanner_meta.message}")
    return "\n".join(lines)


def format_scan_result(result: ScanResult) -> str:
    header = [
        "扫描结果：",
        f"- engine: {result.engine}",
        f"- scope: {', '.join(result.scope) if result.scope else '(none)'}",
        f"- targets: {', '.join(result.targets) if result.targets else '(none)'}",
        f"- status: {result.status}",
        f"- message: {result.message}",
    ]

    if not result.findings:
        header.append("- findings: 0")
        return "\n".join(header)

    header.append(f"- findings: {result.summary.get('total', len(result.findings))}")
    for severity, count in sorted(result.summary.items()):
        if severity == "total":
            continue
        header.append(f"  - {severity}: {count}")

    header.append("问题列表：")
    for idx, finding in enumerate(result.findings, start=1):
        header.append(
            f"  {idx}. {finding.path}:{finding.start_line} [{finding.severity}] "
            f"{finding.check_id} - {finding.message}"
        )
    header.append("下一步：")
    header.append("  - 输入 '修复第1个问题'，为某个问题生成 patch。")
    header.append("  - 输入 '@path 生成 patch'，把修复范围收敛到单个文件。")
    return "\n".join(header)


def format_edit_preview(preview: PatchPreview, prefix: str) -> str:
    lines = [
        prefix,
        f"- file: {preview.file_path}",
        f"- status: {preview.status}",
        f"- message: {preview.message}",
        f"- occurrences: {preview.occurrences}",
        f"- validation status: {preview.validation.status}",
        f"- validation message: {preview.validation.message}",
    ]
    if preview.diff:
        lines.append(preview.diff)
    return "\n".join(lines)


def append_pending_patch_menu(body: str) -> str:
    return f"{body}\n\n{format_pending_patch_menu()}"


def format_pending_patch_menu() -> str:
    return "Patch 选项：\n- apply\n- discard"


def format_rescan_validation(result: RescanValidationResult) -> str:
    lines = [
        "应用后 ReScan：",
        f"- status: {result.status}",
        f"- message: {result.message}",
        f"- source artifact: {result.source_artifact_id or '(none)'}",
        f"- source finding index: {result.source_finding_index or '(none)'}",
        f"- source check_id: {result.source_check_id or '(none)'}",
        f"- source path: {result.source_path or '(none)'}",
        f"- remaining matches: {result.remaining_matches}",
        f"- rescan artifact: {result.rescan_artifact_id or '(none)'}",
    ]
    return "\n".join(lines)


def format_finding_candidates(
    candidates: list[tuple[int, object]],
    prefix: str,
    max_items: int = 10,
) -> str:
    lines = [prefix, "候选问题："]
    for index, finding in candidates[:max_items]:
        lines.append(
            f"  {index}. {getattr(finding, 'path', '')}:{getattr(finding, 'start_line', 0)} "
            f"[{getattr(finding, 'severity', '')}] {getattr(finding, 'check_id', '')} "
            f"- {getattr(finding, 'message', '')}"
        )
    if len(candidates) > max_items:
        lines.append(f"  ... 还有 {len(candidates) - max_items} 个")
    return "\n".join(lines)
