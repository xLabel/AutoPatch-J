"""可追溯的扫描证据；finding 句柄包含 scan 身份，避免活动扫描歧义。"""

from collections.abc import Collection
import pathlib
import re
import uuid

import pydantic

from autopatch_j import config
from autopatch_j import error_messages
from autopatch_j.core import errors
from autopatch_j.core.project import java_paths
from autopatch_j.core.project import repo_path
from autopatch_j.core.repair import files
from autopatch_j.core.repair import models
from autopatch_j.scanners import contracts
from autopatch_j.scanners import models as scan_models


class ScanRecord(models.Record):
    """一次完整扫描结果及扫描前后保持一致的各文件指纹。"""

    id: str
    result: scan_models.ScanResult
    source_hashes: dict[str, str]


class EvidenceService:
    """完整扫描成功且源码未变才登记证据；不判断 finding 必须修改。"""

    def __init__(
        self,
        root: pathlib.Path,
        scanner: contracts.StaticScanner | None,
        *,
        ignored_dirs: Collection[str] = config.IGNORED_DIRS,
    ) -> None:
        self.root = root.resolve()
        self.scanner = scanner
        self.ignored_dirs = frozenset(ignored_dirs)
        self.directory = config.get_project_state_dir(self.root) / "findings"
        self.directory.mkdir(exist_ok=True)

    def expand(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        """展开显式扫描入口，过滤忽略目录并复核每个真实目标。"""
        return java_paths.expand(self.root, paths, self.ignored_dirs)

    def scan(self, paths: tuple[str, ...]) -> ScanRecord:
        """返回已登记的完整扫描证据，执行或证据失败时不生成快照。"""
        if self.scanner is None:
            raise errors.OperationError(error_messages.SCANNER_UNAVAILABLE)
        targets = self.expand(paths)
        if not targets:
            raise models.RepairError(error_messages.NO_JAVA_FILES)
        hashes = {
            path: files.digest(
                repo_path.resolve_source_path(self.root, path).read_bytes()
            )
            for path in targets
        }
        result = self.scanner.scan(self.root, list(targets))
        if result.status != "ok":
            raise errors.OperationError(
                error_messages.failed("扫描", result.message)
            ) from RuntimeError(result.message)
        if any(
            files.digest(
                repo_path.resolve_source_path(self.root, path).read_bytes()
            )
            != fingerprint
            for path, fingerprint in hashes.items()
        ):
            raise models.RepairError(error_messages.SCAN_SOURCE_CHANGED)
        if any(item.path not in hashes for item in result.findings):
            raise errors.OperationError(error_messages.INVALID_SCAN_EVIDENCE)
        record = ScanRecord(
            id=uuid.uuid4().hex, result=result, source_hashes=hashes
        )
        files.atomic_write(
            self.directory / f"{record.id}.json",
            record.model_dump_json(indent=2).encode("utf-8"),
            expected=None,
        )
        return record

    def finding(
        self, handle: str
    ) -> tuple[models.FindingTarget, scan_models.Finding]:
        """返回句柄对应的原始绑定与候选，不回退到更新的扫描。"""
        match = re.fullmatch(r"([0-9a-f]{32}):F([1-9]\d*)", handle)
        if match is None:
            raise models.RepairError(error_messages.INVALID_FINDING_HANDLE)
        record = self.load(match[1])
        index = int(match[2]) - 1
        if index >= len(record.result.findings):
            raise models.RepairError(error_messages.missing_finding(handle))
        item = record.result.findings[index]
        target = models.FindingTarget(
            handle=handle,
            identity=item.identity,
            source_sha256=record.source_hashes[item.path],
        )
        return target, item

    def load(self, scan_id: str) -> ScanRecord:
        """读取明确扫描身份，损坏证据继续报错而不转为空结果。"""
        if re.fullmatch(r"[0-9a-f]{32}", scan_id) is None:
            raise models.RepairError(error_messages.missing_scan(scan_id))
        try:
            return ScanRecord.model_validate_json(
                (self.directory / f"{scan_id}.json").read_bytes()
            )
        except pydantic.ValidationError as exc:
            raise errors.OperationError(
                error_messages.corrupted_scan(scan_id)
            ) from exc
