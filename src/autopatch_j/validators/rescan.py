from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from autopatch_j.scanners import ScanResult
from autopatch_j.session import PendingEdit
from autopatch_j.tools.scan_java import scan_java

Scanner = Callable[[Path, list[str]], ScanResult]


@dataclass(slots=True)
class RescanValidationResult:
    status: str
    message: str
    source_artifact_id: str | None = None
    source_finding_index: int | None = None
    source_check_id: str | None = None
    source_path: str | None = None
    rescan_artifact_id: str | None = None
    remaining_matches: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "message": self.message,
            "source_artifact_id": self.source_artifact_id,
            "source_finding_index": self.source_finding_index,
            "source_check_id": self.source_check_id,
            "source_path": self.source_path,
            "rescan_artifact_id": self.rescan_artifact_id,
            "remaining_matches": self.remaining_matches,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "RescanValidationResult":
        return cls(
            status=str(data.get("status", "")),
            message=str(data.get("message", "")),
            source_artifact_id=(
                str(data.get("source_artifact_id")) if data.get("source_artifact_id") else None
            ),
            source_finding_index=(
                int(data.get("source_finding_index"))
                if data.get("source_finding_index") is not None
                else None
            ),
            source_check_id=(
                str(data.get("source_check_id")) if data.get("source_check_id") else None
            ),
            source_path=str(data.get("source_path")) if data.get("source_path") else None,
            rescan_artifact_id=(
                str(data.get("rescan_artifact_id")) if data.get("rescan_artifact_id") else None
            ),
            remaining_matches=int(data.get("remaining_matches", 0)),
        )


def validate_post_apply_rescan(
    repo_root: Path,
    pending: PendingEdit,
    scanner: Scanner = scan_java,
) -> tuple[RescanValidationResult, ScanResult | None]:
    if not pending.source_artifact_id or not pending.source_check_id:
        return (
            RescanValidationResult(
                status="skipped",
                message="Post-apply ReScan was skipped because this edit has no finding provenance.",
                source_path=pending.file_path,
            ),
            None,
        )

    if Path(pending.file_path).suffix.lower() != ".java":
        return (
            RescanValidationResult(
                status="skipped",
                message="Post-apply ReScan is only enforced for Java files.",
                source_artifact_id=pending.source_artifact_id,
                source_finding_index=pending.source_finding_index,
                source_check_id=pending.source_check_id,
                source_path=pending.file_path,
            ),
            None,
        )

    rescan = scanner(repo_root, [pending.file_path])
    if rescan.status != "ok":
        return (
            RescanValidationResult(
                status="error",
                message=f"Post-apply ReScan failed: {rescan.message}",
                source_artifact_id=pending.source_artifact_id,
                source_finding_index=pending.source_finding_index,
                source_check_id=pending.source_check_id,
                source_path=pending.file_path,
            ),
            rescan,
        )

    remaining_matches = [
        finding
        for finding in rescan.findings
        if finding.path == pending.file_path and finding.check_id == pending.source_check_id
    ]
    if remaining_matches:
        return (
            RescanValidationResult(
                status="failed",
                message=(
                    "Post-apply ReScan still reports the original finding on the edited file."
                ),
                source_artifact_id=pending.source_artifact_id,
                source_finding_index=pending.source_finding_index,
                source_check_id=pending.source_check_id,
                source_path=pending.file_path,
                remaining_matches=len(remaining_matches),
            ),
            rescan,
        )

    return (
        RescanValidationResult(
            status="ok",
            message="Post-apply ReScan no longer reports the original finding on the edited file.",
            source_artifact_id=pending.source_artifact_id,
            source_finding_index=pending.source_finding_index,
            source_check_id=pending.source_check_id,
            source_path=pending.file_path,
            remaining_matches=0,
        ),
        rescan,
    )
