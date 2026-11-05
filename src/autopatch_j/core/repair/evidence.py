"""可追溯的扫描证据；finding 句柄包含 scan 身份，避免活动扫描歧义。"""

import os
import pathlib
import re
import uuid

from autopatch_j import config
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
        self, root: pathlib.Path, scanner: contracts.StaticScanner | None
    ) -> None:
        self.root = root.resolve()
        self.scanner = scanner
        self.directory = config.get_project_state_dir(self.root) / "findings"
        self.directory.mkdir(exist_ok=True)

    def expand(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        """展开显式扫描入口，过滤忽略目录并复核每个真实目标。"""
        result = set()
        for name in paths:
            target = repo_path.resolve_source_path(self.root, name)
            if target.is_file():
                candidates = [target]
            elif target.is_dir():
                candidates = []
                for directory, dirs, names in os.walk(target):
                    dirs[:] = [
                        item
                        for item in dirs
                        if item not in config.GLOBAL_CONFIG.ignored_dirs
                        and item != ".autopatch-j"
                    ]
                    candidates.extend(
                        pathlib.Path(directory) / item
                        for item in names
                        if item.endswith(".java")
                    )
            else:
                raise models.RepairError(f"扫描入口不存在：{name}")
            for path in candidates:
                resolved = repo_path.resolve_source_path(
                    self.root, path.relative_to(self.root).as_posix()
                )
                if resolved.suffix.lower() == ".java":
                    result.add(resolved.relative_to(self.root).as_posix())
        return tuple(sorted(result))

    def scan(self, paths: tuple[str, ...]) -> ScanRecord:
        """返回已登记的完整扫描证据，执行或证据失败时不生成快照。"""
        if self.scanner is None:
            raise models.RepairError("扫描器未配置。")
        targets = self.expand(paths)
        if not targets:
            raise models.RepairError("范围内没有 Java 文件；未执行扫描。")
        hashes = {
            path: files.digest(
                repo_path.resolve_source_path(self.root, path).read_bytes()
            )
            for path in targets
        }
        result = self.scanner.scan(self.root, list(targets))
        if result.status != "ok":
            raise models.RepairError(
                f"扫描失败，不能当作零 finding：{result.message}"
            )
        if any(
            files.digest(
                repo_path.resolve_source_path(self.root, path).read_bytes()
            )
            != fingerprint
            for path, fingerprint in hashes.items()
        ):
            raise models.RepairError("扫描期间源码变化，未登记不可靠证据。")
        if any(item.path not in hashes for item in result.findings):
            raise models.RepairError("扫描器返回范围外 finding。")
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
            raise models.RepairError(
                "finding 句柄必须使用扫描返回的 <scan_id>:F<n>。"
            )
        record = self.load(match[1])
        index = int(match[2]) - 1
        if index >= len(record.result.findings):
            raise models.RepairError("扫描中不存在此 finding。")
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
            raise models.RepairError("无效的 scan ID。")
        return ScanRecord.model_validate_json(
            (self.directory / f"{scan_id}.json").read_bytes()
        )
