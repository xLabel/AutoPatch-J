from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from autopatch_j.core.finding import FindingIdentity, SourceRegion


class TargetFileNotFoundError(Exception):
    """补丁目标文件不存在。"""


class OldStringNotFoundError(Exception):
    """old_string 没有在目标文件中精确命中。"""


class OldStringNotUniqueError(Exception):
    """old_string 在目标文件中命中多处，无法安全替换。"""

    def __init__(self, occurrences: int):
        self.occurrences = occurrences
        super().__init__(f"old_string matched {occurrences} times.")


class UnsafeSourceEncodingError(Exception):
    """源码无法无损解码，不能建立可信补丁区域。"""


@dataclass(slots=True)
class SyntaxCheckResult:
    """
    Java 语法检查结果。

    status 表示 ok/error/skipped/unavailable，errors 保存 Tree-sitter 定位到的语法问题。
    """

    status: str
    message: str
    errors: list[str] = field(default_factory=list)


class VerificationOutcome(str, Enum):
    """补丁应用后的目标 finding 验证结论。"""

    RESOLVED = "resolved"
    STILL_PRESENT = "still_present"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class PatchDraftBuildResult:
    """search/replace 草案及其在当前源码中的唯一命中区域。"""

    updated_source: str
    diff: str
    match_region: SourceRegion


@dataclass(frozen=True, slots=True)
class PatchApplicationResult:
    """补丁原子落盘结果。"""

    applied: bool
    message: str
    source_region: SourceRegion | None = None
    changed_region: SourceRegion | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.applied:
            if self.source_region is None or self.changed_region is None:
                raise ValueError("成功的补丁结果必须包含 source region 和 changed region。")
            if self.error_code is not None:
                raise ValueError("成功的补丁结果不能包含 error code。")
            return
        if self.source_region is not None or self.changed_region is not None:
            raise ValueError("失败的补丁结果不能包含 source region 或 changed region。")
        if not (self.error_code or "").strip():
            raise ValueError("失败的补丁结果必须包含 error code。")


@dataclass(frozen=True, slots=True)
class PatchDraftRebaseResult:
    """前序补丁落盘后，对待审草案重新建立源码绑定的结果。"""

    rebased: bool
    message: str
    build_result: PatchDraftBuildResult | None = None
    rebased_target_finding: FindingIdentity | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.rebased:
            if self.build_result is None or self.error_code is not None:
                raise ValueError("成功的 rebase result 必须包含 build result 且不能包含 error code。")
            return
        if self.build_result is not None or self.rebased_target_finding is not None:
            raise ValueError("失败的 rebase result 不能包含部分重定位结果。")
        if self.error_code != "STALE_DRAFT":
            raise ValueError("失败的 rebase result 必须使用 STALE_DRAFT。")


@dataclass(slots=True)
class VerificationResult:
    """
    补丁应用后的语义复核结果。

    outcome 区分已解决、仍存在与无法验证；remaining_findings 是文件内全部剩余问题数。
    """

    outcome: VerificationOutcome
    message: str
    remaining_findings: int = 0
    other_same_rule_findings: int = 0


@dataclass(slots=True)
class SearchReplacePatchDraft:
    """
    内存中的 search-replace 补丁草案。

    保存 search-replace 输入、diff、验证结果和关联 finding 信息；进入 workspace 前会转换为快照。
    """

    file_path: str
    old_string: str
    new_string: str
    diff: str
    match_region: SourceRegion
    validation: SyntaxCheckResult
    status: str
    message: str
    rationale: str | None = None
    source_hint: str | None = None
    error_code: str | None = None
    associated_finding_id: str | None = None
    source_scan_id: str | None = None
    target_finding: FindingIdentity | None = None

    def __post_init__(self) -> None:
        if self.associated_finding_id is not None and self.target_finding is None:
            raise ValueError("关联 finding 的补丁必须保存完整 target identity。")
        if self.target_finding is not None and self.associated_finding_id is None:
            raise ValueError("target identity 必须绑定 associated finding handle。")
        if self.target_finding is not None and normalize_patch_path(self.file_path) != self.target_finding.path:
            raise ValueError("补丁文件与 target identity 文件不一致。")
        if self.target_finding is not None and not (self.source_scan_id or "").strip():
            raise ValueError("关联 finding 的补丁必须保存 source scan id。")
        if self.target_finding is not None and not self.match_region.intersects(
            self.target_finding.region
        ):
            raise ValueError("补丁 match region 必须覆盖 target finding region。")


def normalize_patch_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or "."
