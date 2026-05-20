from __future__ import annotations

from dataclasses import dataclass, field


class TargetFileNotFoundError(Exception):
    """补丁目标文件不存在。"""


class OldStringNotFoundError(Exception):
    """old_string 没有在目标文件中精确命中。"""


class OldStringNotUniqueError(Exception):
    """old_string 在目标文件中命中多处，无法安全替换。"""

    def __init__(self, occurrences: int):
        self.occurrences = occurrences
        super().__init__(f"old_string matched {occurrences} times.")


@dataclass(slots=True)
class SyntaxCheckResult:
    """
    Java 语法检查结果。

    status 表示 ok/error/skipped/unavailable，errors 保存 Tree-sitter 定位到的语法问题。
    """

    status: str
    message: str
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VerificationResult:
    """
    补丁应用后的语义复核结果。

    is_resolved 表示目标 finding 是否消失，remaining_findings 用于提示重扫后剩余问题数量。
    """

    is_resolved: bool
    message: str
    remaining_findings: int = 0


@dataclass(slots=True)
class ProjectValidationResult:
    """
    项目级验证状态。

    当前只表达“是否识别到可运行的项目验证入口”和“本轮是否执行”，不默认触发 Maven/Gradle。
    """

    status: str
    message: str


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
    validation: SyntaxCheckResult
    status: str
    message: str
    rationale: str | None = None
    source_hint: str | None = None
    error_code: str | None = None
    target_check_id: str | None = None
    target_snippet: str | None = None
    project_validation: ProjectValidationResult = field(
        default_factory=lambda: ProjectValidationResult(status="not_run", message="项目级验证未执行。")
    )
