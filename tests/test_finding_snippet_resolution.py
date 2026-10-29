"""以临时源码和扫描快照核对 finding 回源、scan 选择和重定位绑定。"""

from __future__ import annotations

import pathlib

from autopatch_j.agent import session as agent_session
from autopatch_j.core import finding
from autopatch_j.core.domain import workspace
from autopatch_j.core.patching import search_replace
from autopatch_j.core.patching import types
from autopatch_j.core.patching import verification
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index
from autopatch_j.core.review import artifacts
from autopatch_j.core.review import workspace as review_workspace
from autopatch_j.scanners import models
from autopatch_j.scanners.semgrep import results
from autopatch_j.tools.function_calls import get_finding_detail
from autopatch_j.tools.function_calls import propose_patch


def _build_session(repo_root: pathlib.Path) -> agent_session.AgentSession:
    artifact_manager = artifacts.ProjectArtifactStore(repo_root)
    workspace_manager = review_workspace.ReviewWorkspaceManager(
        artifact_manager
    )
    symbol_indexer = symbol_index.SymbolIndex(repo_root)
    patch_engine = search_replace.SearchReplacePatchEngine(repo_root)
    code_fetcher = source_reader.SourceReader(repo_root)
    symbol_indexer.rebuild_index()
    patch_verifier = verification.PatchQualityVerifier(repo_root, None)
    session = agent_session.AgentSession(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        symbol_indexer=symbol_indexer,
        patch_engine=patch_engine,
        code_fetcher=code_fetcher,
        patch_verifier=patch_verifier,
    )
    return session


def _source_region(source: str, evidence: str) -> finding.SourceRegion:
    start_index = source.index(evidence)
    end_index = start_index + len(evidence)
    start_prefix = source[:start_index]
    end_prefix = source[:end_index]
    return finding.SourceRegion(
        start_line=start_prefix.count("\n") + 1,
        start_column=start_index - start_prefix.rfind("\n"),
        end_line=end_prefix.count("\n") + 1,
        end_column=end_index - end_prefix.rfind("\n"),
        start_offset=len(start_prefix.encode("utf-8")),
        end_offset=len(end_prefix.encode("utf-8")),
    )


def _finding(
    *,
    source: str,
    evidence: str,
    path: str,
    check_id: str,
    message: str,
    snippet: str | None = None,
) -> models.Finding:
    return models.Finding(
        fingerprint=f"apj-v1:{"a" * 64}:1",
        check_id=check_id,
        path=path,
        region=_source_region(source, evidence),
        severity="warning",
        message=message,
        snippet=snippet if snippet is not None else evidence,
    )


def test_build_semgrep_scan_result_prefers_source_lines_over_dirty_extra_lines(
    tmp_path: pathlib.Path,
) -> None:
    java_file = tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    java_file.parent.mkdir(parents=True)
    source = (
        "package demo;\n\n"
        "public class UserService {\n"
        "    public boolean isAdmin(User "
        "user) {\n"
        "        return user.getName().eq"
        'uals("admin");\n'
        "    }\n"
        "}\n"
    )
    with java_file.open("w", encoding="utf-8", newline="") as stream:
        stream.write(source)
    evidence = 'return user.getName().equals("admin");'
    region = _source_region(source, evidence)

    payload: dict[str, object] = {
        "results": [
            {
                "check_id": "autopatch-j.java.correctness.unsafe-equals-order",
                "path": "src/main/java/demo/UserService.java",
                "start": {
                    "line": region.start_line,
                    "col": region.start_column,
                    "offset": region.start_offset,
                },
                "end": {
                    "line": region.end_line,
                    "col": region.end_column,
                    "offset": region.end_offset,
                },
                "extra": {
                    "severity": "WARNING",
                    "message": "unsafe equals order",
                    "lines": "requires login",
                },
            }
        ],
        "errors": [],
    }

    result = results.build_semgrep_scan_result(
        payload=payload,
        repo_root=tmp_path,
        scope=["src/main/java/demo/UserService.java"],
        targets=["src/main/java/demo/UserService.java"],
    )

    assert len(result.findings) == 1
    assert (
        result.findings[0].snippet == 'return user.getName().equals("admin");'
    )


def test_get_finding_detail_refreshes_stale_display_snippet(
    tmp_path: pathlib.Path,
) -> None:
    java_file = tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    java_file.parent.mkdir(parents=True)
    source = (
        "package demo;\n\n"
        "public class UserService {\n"
        "    public boolean isAdmin(User "
        "user) {\n"
        "        return user.getName().eq"
        'uals("admin");\n'
        "    }\n"
        "}\n"
    )
    java_file.write_text(source, encoding="utf-8")

    context = _build_session(tmp_path)
    context.set_focus_paths(["src/main/java/demo/UserService.java"])
    context.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                _finding(
                    source=source,
                    evidence='return user.getName().equals("admin");',
                    check_id="autopatch-j.java.correctness.unsafe-equals-order",
                    path="src/main/java/demo/UserService.java",
                    message="unsafe equals order",
                    snippet="requires login",
                )
            ],
        )
    )

    result = get_finding_detail.GetFindingDetailTool(context).execute("F1")

    assert result.status == "ok"
    assert 'return user.getName().equals("admin");' in result.message
    assert "requires login" not in result.message
    assert result.payload["finding_id"] == "F1"
    assert str(result.payload["scan_id"]).startswith("scan-")


def test_get_finding_detail_uses_rebased_pending_region(
    tmp_path: pathlib.Path,
) -> None:
    path = "Demo.java"
    original = "first();\nsecond();\n"
    current = "first();\ninserted();\nsecond();\n"
    (tmp_path / path).write_text(current, encoding="utf-8")
    context = _build_session(tmp_path)
    context.set_focus_paths([path])
    first_finding = _finding(
        source=original,
        evidence="first();",
        path=path,
        check_id="demo.first",
        message="first finding",
    )
    second_finding = _finding(
        source=original,
        evidence="second();",
        path=path,
        check_id="demo.second",
        message="second finding",
    )
    second_finding.fingerprint = f"apj-v1:{"b" * 64}:1"
    scan_id = context.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=[path],
            targets=[path],
            status="ok",
            message="ok",
            findings=[first_finding, second_finding],
        )
    )
    build_result = context.patch_engine.create_draft(
        path,
        "second();",
        "fixedSecond();",
    )
    target = finding.FindingIdentity(
        fingerprint=second_finding.fingerprint,
        check_id=second_finding.check_id,
        path=path,
        region=build_result.match_region,
    )
    draft = types.SearchReplacePatchDraft(
        file_path=path,
        old_string="second();",
        new_string="fixedSecond();",
        diff=build_result.diff,
        match_region=build_result.match_region,
        validation=types.SyntaxCheckResult(status="ok", message="ok"),
        status="ok",
        message="ok",
        associated_finding_id="F2",
        source_scan_id=scan_id,
        target_finding=target,
    )
    context.workspace_manager.save(
        workspace.ReviewWorkspace(
            mode=workspace.WorkspaceStatus.REVIEWING,
            scope=None,
            latest_scan_id=scan_id,
            patch_items=[
                workspace.ReviewPatchItem(
                    item_id="item-2",
                    file_path=path,
                    finding_ids=["F2"],
                    status=workspace.PatchReviewStatus.PENDING,
                    draft=workspace.PatchDraftSnapshot.from_patch_draft(draft),
                )
            ],
            current_patch_index=0,
        )
    )

    result = get_finding_detail.GetFindingDetailTool(context).execute("F2")

    assert result.status == "ok"
    assert result.payload["region"] == build_result.match_region.to_dict()
    assert "second();" in result.message
    assert "inserted();" not in result.message


def test_get_finding_detail_uses_workspace_scan_before_newer_scan(
    tmp_path: pathlib.Path,
) -> None:
    first_file = tmp_path / "First.java"
    second_file = tmp_path / "Second.java"
    first_file.write_text("class First { void run() {} }", encoding="utf-8")
    second_file.write_text("class Second { void run() {} }", encoding="utf-8")

    context = _build_session(tmp_path)
    first_scan_id = context.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=["First.java"],
            targets=["First.java"],
            status="ok",
            message="ok",
            findings=[
                _finding(
                    source="class First { void run() {} }",
                    evidence="class First { void run() {} }",
                    check_id="first.rule",
                    path="First.java",
                    message="first finding",
                )
            ],
        )
    )
    context.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=["Second.java"],
            targets=["Second.java"],
            status="ok",
            message="ok",
            findings=[
                _finding(
                    source="class Second { void run() {} }",
                    evidence="class Second { void run() {} }",
                    check_id="second.rule",
                    path="Second.java",
                    message="second finding",
                )
            ],
        )
    )
    context.workspace_manager.save(
        workspace.ReviewWorkspace(
            mode=workspace.WorkspaceStatus.REVIEWING,
            scope=None,
            latest_scan_id=first_scan_id,
            patch_items=[],
        )
    )

    result = get_finding_detail.GetFindingDetailTool(context).execute("F1")

    assert result.status == "ok"
    assert result.payload["scan_id"] == first_scan_id
    assert result.payload["check_id"] == "first.rule"


def test_get_finding_detail_does_not_fallback_when_workspace_scan_is_missing(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "Demo.java").write_text("class Demo {}", encoding="utf-8")
    context = _build_session(tmp_path)
    context.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=["Demo.java"],
            targets=["Demo.java"],
            status="ok",
            message="ok",
            findings=[
                _finding(
                    source="class Demo {}",
                    evidence="class Demo {}",
                    check_id="demo.rule",
                    path="Demo.java",
                    message="demo finding",
                )
            ],
        )
    )
    context.workspace_manager.save(
        workspace.ReviewWorkspace(
            mode=workspace.WorkspaceStatus.REVIEWING,
            scope=None,
            latest_scan_id="scan-missing",
            patch_items=[],
        )
    )

    result = get_finding_detail.GetFindingDetailTool(context).execute("F1")

    assert result.status == "error"
    assert result.payload["error_code"] == "SCAN_ARTIFACT_NOT_FOUND"


def test_patch_proposal_persists_full_target_identity(
    tmp_path: pathlib.Path,
) -> None:
    java_file = tmp_path / "src" / "main" / "java" / "demo" / "UserService.java"
    java_file.parent.mkdir(parents=True)
    source = (
        "package demo;\n\n"
        "public class UserService {\n"
        "    public boolean isAdmin(User "
        "user) {\n"
        "        return user.getName().eq"
        'uals("admin");\n'
        "    }\n"
        "}\n"
    )
    java_file.write_text(source, encoding="utf-8")

    context = _build_session(tmp_path)
    context.set_focus_paths(["src/main/java/demo/UserService.java"])
    context.artifact_manager.save_scan_result(
        models.ScanResult(
            engine="semgrep",
            scope=["src/main/java/demo/UserService.java"],
            targets=["src/main/java/demo/UserService.java"],
            status="ok",
            message="ok",
            findings=[
                _finding(
                    source=source,
                    evidence='return user.getName().equals("admin");',
                    check_id="autopatch-j.java.correctness.unsafe-equals-order",
                    path="src/main/java/demo/UserService.java",
                    message="unsafe equals order",
                    snippet="requires login",
                )
            ],
        )
    )

    result = propose_patch.ProposePatchTool(context).execute(
        file_path="src/main/java/demo/UserService.java",
        old_string='return user.getName().equals("admin");',
        new_string='return "admin".equals(user.getName());',
        rationale="fix unsafe equals order",
        associated_finding_id="F1",
    )

    pending = context.proposed_patch_draft
    assert result.status in {"ok", "invalid"}
    assert pending is not None
    assert pending.associated_finding_id == "F1"
    assert pending.source_scan_id is not None
    assert pending.target_finding is not None
    assert (
        pending.target_finding.check_id
        == "autopatch-j.java.correctness.unsafe-equals-order"
    )
    assert pending.target_finding.region == _source_region(
        source,
        'return user.getName().equals("admin");',
    )
    assert result.payload["target_finding"] == pending.target_finding.to_dict()
