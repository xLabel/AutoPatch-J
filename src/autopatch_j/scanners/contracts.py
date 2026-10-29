"""扫描器的标准元信息与扫描结果接口。"""

from __future__ import annotations

import abc
import pathlib

from autopatch_j.scanners import models


class StaticScanner(abc.ABC):
    """审计与应用后验证共用的 Java 扫描器契约。"""

    name: models.ScannerName

    @abc.abstractmethod
    def get_meta(
        self, repo_root: pathlib.Path | None = None
    ) -> models.ScannerMeta:
        """返回扫描器实际能力和就绪状态。"""

    @abc.abstractmethod
    def scan(
        self, repo_root: pathlib.Path, scope: list[str]
    ) -> models.ScanResult:
        """扫描指定仓库范围，返回完整标准结果或明确失败。"""
