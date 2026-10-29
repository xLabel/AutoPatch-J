"""Java 语法检查及应用后目标 finding 足迹复扫。"""

from __future__ import annotations

import pathlib

tree_sitter_import_error: ImportError | None = None
try:
    import tree_sitter
    import tree_sitter_java
except ImportError as exc:
    tree_sitter_import_error = exc


from autopatch_j.core import finding as core_finding
from autopatch_j.core.patching import types
from autopatch_j.core.project import repo_path
from autopatch_j.scanners import contracts


def _map_target_footprint(
    target_region: core_finding.SourceRegion,
    source_region: core_finding.SourceRegion,
    changed_region: core_finding.SourceRegion,
) -> tuple[int, int] | None:
    """将 apply 前目标映射为 apply 后的保守 byte span。"""

    byte_delta = (
        changed_region.end_offset
        - changed_region.start_offset
        - (source_region.end_offset - source_region.start_offset)
    )
    start_offset = (
        target_region.start_offset
        if target_region.start_offset < source_region.start_offset
        else changed_region.start_offset
    )
    end_offset = (
        target_region.end_offset + byte_delta
        if target_region.end_offset > source_region.end_offset
        else changed_region.end_offset
    )
    if end_offset < start_offset:
        return None
    return start_offset, end_offset


def _region_intersects_span(
    region: core_finding.SourceRegion,
    start_offset: int,
    end_offset: int,
) -> bool:
    """按 SourceRegion 的半开区间与零长度点语义判断相交。"""

    if region.start_offset == region.end_offset:
        return start_offset <= region.start_offset < end_offset
    if start_offset == end_offset:
        return region.start_offset <= start_offset < region.end_offset
    return region.start_offset < end_offset and start_offset < region.end_offset


class PatchQualityVerifier:
    """补丁质量验证服务。

    职责边界：
    1. 草案阶段用 Tree-sitter 做 Java 语法检查。
    2. apply 后通过扫描器重扫确认目标规则是否消失。
    3. 不生成补丁、不写文件；补丁生成和落盘分别由 SearchReplacePatchEngine 负责。
    """

    def __init__(
        self, repo_root: pathlib.Path, scanner: contracts.StaticScanner | None
    ) -> None:
        self.repo_root = repo_root
        self.scanner = scanner

    def verify_syntax(
        self, file_path: str, new_source_code: str
    ) -> types.SyntaxCheckResult:
        """检查 Java 语法，不代表编译或业务验收；依赖不可用单独返回状态。"""
        if not file_path.lower().endswith(".java"):
            return types.SyntaxCheckResult(
                status="skipped", message="非 Java 文件，跳过语法校验。"
            )

        if tree_sitter_import_error is not None:
            return types.SyntaxCheckResult(
                status="unavailable",
                message="系统缺少 tree-sitter 或 tree-sitter-java 依赖，无法执行语法校验。",
            )

        try:
            language = tree_sitter.Language(tree_sitter_java.language())
            parser = tree_sitter.Parser(language)
            tree = parser.parse(new_source_code.encode("utf-8"))

            errors = self._collect_errors(tree.root_node)
            if errors:
                return types.SyntaxCheckResult(
                    status="error",
                    message=f"检测到 {len(errors)} 处 Java 语法错误。",
                    errors=errors,
                )

            return types.SyntaxCheckResult(
                status="ok", message="Java 语法校验通过。"
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return types.SyntaxCheckResult(
                status="error", message=f"语法分析过程出现异常：{str(exc)}"
            )

    def _collect_errors(self, node: tree_sitter.Node) -> list[str]:
        """收集 Tree-sitter 的错误和缺失节点坐标，不宣称执行编译。"""
        errors = []

        def walk(current_node: tree_sitter.Node) -> None:
            if current_node.is_error or current_node.is_missing:
                line = current_node.start_point[0] + 1
                col = current_node.start_point[1] + 1
                kind = "缺失符号" if current_node.is_missing else "语法错误"
                errors.append(f"[{line}:{col}] {kind} ({current_node.type})")

            for child in current_node.children:
                walk(child)

        walk(node)
        return errors

    def verify_finding_resolved(
        self,
        draft: types.SearchReplacePatchDraft,
        application_result: types.PatchApplicationResult,
    ) -> types.VerificationResult:
        """按编辑后的目标足迹复扫，写入成功不等于目标已经消失。"""
        if not self.scanner:
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                "无法确认修复结果：未配置可用的扫描器。",
            )
        if draft.target_finding is None:
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                "无法确认修复结果：补丁缺少目标 finding identity。",
            )
        if not application_result.applied:
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                "无法确认修复结果：补丁缺少成功 apply 证据。",
            )

        source_region = application_result.source_region
        changed_region = application_result.changed_region
        if source_region is None or changed_region is None:
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                "无法确认修复结果：补丁缺少实际 source/changed region。",
            )

        target = draft.target_finding
        if source_region != draft.match_region:
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                "无法确认修复结果：apply source region 与待审草案绑定不一致。",
            )
        if (
            changed_region.start_offset != source_region.start_offset
            or changed_region.start_line != source_region.start_line
            or changed_region.start_column != source_region.start_column
        ):
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                "无法确认修复结果：apply source/changed region 起点不一致。",
            )
        if not source_region.intersects(target.region):
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                "无法确认修复结果：apply source region 未与目标 finding 区域相交。",
            )

        target_footprint = _map_target_footprint(
            target.region,
            source_region,
            changed_region,
        )
        if target_footprint is None:
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                "无法确认修复结果：目标 finding 区域映射无效。",
            )

        try:
            rescan_result = self.scanner.scan(self.repo_root, [draft.file_path])
        except (OSError, RuntimeError, ValueError) as exc:
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                f"无法确认修复结果：语义重扫异常：{exc}",
            )

        if rescan_result.status != "ok":
            return types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                f"无法确认修复结果：语义重扫失败：{rescan_result.message}",
            )

        target_path = repo_path.normalize_repo_path(target.path)
        candidates = [
            finding
            for finding in rescan_result.findings
            if repo_path.normalize_repo_path(finding.path) == target_path
            and finding.check_id == target.check_id
        ]
        in_target_footprint = [
            finding
            for finding in candidates
            if _region_intersects_span(finding.region, *target_footprint)
        ]
        outside_target_footprint = len(candidates) - len(in_target_footprint)
        remaining_findings = len(rescan_result.findings)

        if in_target_footprint:
            return types.VerificationResult(
                types.VerificationOutcome.STILL_PRESENT,
                (
                    f"验证未通过：规则 '{target.check_id}' 在目标"
                    f" finding 区域仍被触发；"
                    f"同规则其他位置剩余 {outside_target_footprint}"
                    f" 处。"
                ),
                remaining_findings=remaining_findings,
                other_same_rule_findings=outside_target_footprint,
            )

        return types.VerificationResult(
            types.VerificationOutcome.RESOLVED,
            (
                f"精准验证通过：目标规则 '{target.check_id}' "
                f"已从目标 finding 区域消失；"
                f"同规则其他位置剩余 {outside_target_footprint}"
                f" 处。"
            ),
            remaining_findings=remaining_findings,
            other_same_rule_findings=outside_target_footprint,
        )
