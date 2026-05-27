from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from autopatch_j.core.review.artifacts import ProjectArtifactStore
from autopatch_j.core.review.workspace import ReviewWorkspaceManager
from autopatch_j.scanners.models import Finding


@dataclass(frozen=True, slots=True)
class FindingLookupResult:
    scan_id: str
    finding_id: str
    finding_index: int
    finding: Finding


class FindingLookupError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def resolve_finding_handle(
    artifact_store: ProjectArtifactStore,
    workspace_manager: ReviewWorkspaceManager,
    finding_id: str,
) -> FindingLookupResult:
    normalized_finding_id, finding_index = parse_finding_handle(finding_id)
    scan_id = resolve_active_scan_id(artifact_store, workspace_manager)
    finding = artifact_store.get_finding_by_index(scan_id, finding_index)
    if finding is None:
        raise FindingLookupError(
            "ASSOCIATED_FINDING_NOT_FOUND",
            f"无法从扫描快照 {scan_id} 中取回句柄为 {normalized_finding_id} 的详情。",
        )
    return FindingLookupResult(
        scan_id=scan_id,
        finding_id=normalized_finding_id,
        finding_index=finding_index,
        finding=finding,
    )


def parse_finding_handle(finding_id: str) -> tuple[str, int]:
    match = re.fullmatch(r"[Ff]([1-9]\d*)", str(finding_id).strip())
    if match is None:
        raise FindingLookupError(
            "INVALID_FINDING_HANDLE",
            f"无效的 finding 句柄格式：{finding_id}。请使用 F1、F2 这种格式。",
        )
    index = int(match.group(1)) - 1
    return f"F{index + 1}", index


def resolve_active_scan_id(
    artifact_store: ProjectArtifactStore,
    workspace_manager: ReviewWorkspaceManager,
) -> str:
    workspace = workspace_manager.load()
    if workspace.latest_scan_id:
        if artifact_store.load_scan_result(workspace.latest_scan_id) is None:
            raise FindingLookupError(
                "SCAN_ARTIFACT_NOT_FOUND",
                f"当前工作台引用的扫描快照不存在或不可读取：{workspace.latest_scan_id}",
            )
        return workspace.latest_scan_id

    latest_scan = _latest_scan_file(artifact_store.findings_dir)
    if latest_scan is None:
        raise FindingLookupError("NO_SCAN_ARTIFACT", "系统中未找到扫描记录，请先发起一次代码检查。")
    return latest_scan.stem


def _latest_scan_file(findings_dir: Path) -> Path | None:
    scan_files = sorted(findings_dir.glob("scan-*.json"), reverse=True)
    return scan_files[0] if scan_files else None
