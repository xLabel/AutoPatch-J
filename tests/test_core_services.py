from __future__ import annotations

from pathlib import Path

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.intent_service import IntentService
from autopatch_j.core.models import CodeScopeKind, IntentType
from autopatch_j.core.scan_service import ScanService
from autopatch_j.core.scope_service import ScopeService


def test_intent_service_prefers_local_rules() -> None:
    service = IntentService()

    assert service.fetch_intent("@A.java 检查代码", has_pending_review=False) is IntentType.CODE_AUDIT
    assert service.fetch_intent("@A.java 解释一下代码的功能", has_pending_review=False) is IntentType.CODE_EXPLAIN
    assert service.fetch_intent("为什么这么改？", has_pending_review=True) is IntentType.PATCH_EXPLAIN
    assert service.fetch_intent("加一句注释", has_pending_review=True) is IntentType.PATCH_REVISE


def test_intent_service_falls_back_to_llm_classifier() -> None:
    service = IntentService(
        classify_with_llm=lambda text, has_pending_review: (
            IntentType.GENERAL_CHAT if not has_pending_review else IntentType.PATCH_EXPLAIN
        )
    )

    assert service.fetch_intent("@A.java 看看这个", has_pending_review=False) is IntentType.GENERAL_CHAT
    assert service.fetch_intent("@A.java 这个咋样", has_pending_review=True) is IntentType.PATCH_EXPLAIN


def test_scope_service_resolves_file_directory_and_project(tmp_path: Path) -> None:
    repo_root = tmp_path
    demo_dir = repo_root / "src" / "main" / "java" / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "User.java").write_text("class User {}", encoding="utf-8")
    (demo_dir / "UserService.java").write_text("class UserService {}", encoding="utf-8")
    (repo_root / "README.md").write_text("hello", encoding="utf-8")

    indexer = IndexService(repo_root)
    indexer.perform_rebuild()
    service = ScopeService(repo_root, indexer, ignored_dirs={".git", ".autopatch-j"})

    file_scope = service.fetch_scope("@User.java 检查代码")
    assert file_scope is not None
    assert file_scope.kind is CodeScopeKind.SINGLE_FILE
    assert file_scope.focus_files == ["src/main/java/demo/User.java"]

    dir_scope = service.fetch_scope("@src/main/java/demo 检查代码")
    assert dir_scope is not None
    assert dir_scope.kind is CodeScopeKind.MULTI_FILE
    assert dir_scope.focus_files == [
        "src/main/java/demo/User.java",
        "src/main/java/demo/UserService.java",
    ]

    project_scope = service.fetch_scope("检查代码", default_to_project=True)
    assert project_scope is not None
    assert project_scope.kind is CodeScopeKind.PROJECT
    assert project_scope.focus_files == [
        "src/main/java/demo/User.java",
        "src/main/java/demo/UserService.java",
    ]


def test_scan_service_persists_scan_result(tmp_path: Path) -> None:
    repo_root = tmp_path
    java_file = repo_root / "Demo.java"
    java_file.write_text("class Demo {}", encoding="utf-8")

    artifacts = ArtifactManager(repo_root)
    indexer = IndexService(repo_root)
    indexer.perform_rebuild()
    scope_service = ScopeService(repo_root, indexer)
    scope = scope_service.fetch_scope("@Demo.java 检查代码")
    assert scope is not None

    scan_service = ScanService(repo_root, artifacts)
    artifact_id, result = scan_service.fetch_scan_snapshot(scope)

    restored = artifacts.fetch_scan_result(artifact_id)
    assert result.status == "ok"
    assert restored is not None
    assert restored.targets == ["Demo.java"]
