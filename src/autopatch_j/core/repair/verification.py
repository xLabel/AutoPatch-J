"""以真实写入后的目标足迹复扫，不把静态证据称为业务正确性证明。"""

import pathlib

from autopatch_j.core.project import repo_path
from autopatch_j.core.repair import files
from autopatch_j.core.repair import models
from autopatch_j.scanners import contracts


def target_span(proposal: models.Proposal) -> tuple[int, int] | None:
    """依次映射每次编辑；辅助文件改动不要求与扫描区域相交。"""
    if proposal.target is None:
        return None
    region = proposal.target.identity.region
    start, end = region.start_offset, region.end_offset
    for prepared in proposal.files:
        if prepared.path != proposal.target.identity.path:
            continue
        for span in prepared.spans:
            delta = span.replacement_length - (span.end - span.start)
            if span.end <= start:
                start, end = start + delta, end + delta
            elif span.start < end or (start == end == span.start):
                start = min(start, span.start)
                end = max(span.start + span.replacement_length, end + delta)
    return start, end


def verify(
    root: pathlib.Path,
    proposal: models.Proposal,
    scanner: contracts.StaticScanner | None,
) -> tuple[models.Check, ...]:
    """核对当前 bytes 再复扫同规则目标区域；编译和测试单独标为未验证。"""
    checks = [
        models.Check(
            kind="build_and_tests",
            status="unverified",
            message="未执行目标 Java 项目的编译、测试或业务验收。",
        )
    ]
    target = proposal.target
    if scanner is None or target is None:
        return (
            *checks,
            models.Check(
                kind="target_rescan",
                status="unverified",
                message="缺少扫描目标绑定或扫描器，不能确认问题消失。",
            ),
        )
    expected = {item.path: item.candidate_sha256 for item in proposal.files}
    expected.setdefault(target.identity.path, target.source_sha256)
    try:
        if any(
            files.digest(repo_path.resolve_source_path(root, path).read_bytes())
            != fingerprint
            for path, fingerprint in expected.items()
        ):
            raise models.RepairError("验证前源码再次变化。")
        result = scanner.scan(root, [target.identity.path])
        if result.status != "ok":
            raise models.RepairError(result.message)
        if any(
            files.digest(repo_path.resolve_source_path(root, path).read_bytes())
            != fingerprint
            for path, fingerprint in expected.items()
        ):
            raise models.RepairError("扫描期间源码再次变化。")
    except (OSError, RuntimeError, ValueError) as exc:
        return (
            *checks,
            models.Check(
                kind="target_rescan",
                status="unverified",
                message=f"目标复扫未完成：{exc}",
            ),
        )
    span = target_span(proposal)
    assert span is not None
    start, end = span
    candidates = [
        item
        for item in result.findings
        if item.path == target.identity.path
        and item.check_id == target.identity.check_id
    ]
    remaining = any(
        (
            item.region.start_offset <= start < item.region.end_offset
            if start == end
            else (
                start <= item.region.start_offset < end
                if item.region.start_offset == item.region.end_offset
                else item.region.start_offset < end
                and start < item.region.end_offset
            )
        )
        for item in candidates
    )
    checks.append(
        models.Check(
            kind="target_rescan",
            status="failed" if remaining else "passed",
            message=(
                "目标映射足迹内仍触发同规则。"
                if remaining
                else "目标映射足迹内未再触发同规则；不证明业务语义正确，其他位置仍可能有问题。"
            ),
        )
    )
    return tuple(checks)
