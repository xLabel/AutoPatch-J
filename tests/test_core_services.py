from __future__ import annotations

from pathlib import Path

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.intent_detector import IntentDetector
from autopatch_j.core.models import CodeScopeKind, IntentType
from autopatch_j.core.scanner_runner import ScannerRunner
from autopatch_j.core.scope_service import ScopeService


def test_intent_detector_relies_entirely_on_llm_classifier() -> None:
    service = IntentDetector(
        classify_with_llm=lambda text, has_pending_review: (
            IntentType.GENERAL_CHAT if not has_pending_review else IntentType.PATCH_EXPLAIN
        )
    )

    assert service.detect_intent("@A.java 看看这个", has_pending_review=False) is IntentType.GENERAL_CHAT
    assert service.detect_intent("@A.java 这个咋样", has_pending_review=True) is IntentType.PATCH_EXPLAIN

    service_fallback = IntentDetector()
    assert service_fallback.detect_intent("没有LLM时的兜底", has_pending_review=False) is IntentType.GENERAL_CHAT
    assert service_fallback.detect_intent("没有LLM时的兜底", has_pending_review=True) is IntentType.PATCH_REVISE


def test_scope_service_resolves_file_directory_and_project(tmp_path: Path) -> None:
    repo_root = tmp_path
    demo_dir = repo_root / "src" / "main" / "java" / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "User.java").write_text("class User {}", encoding="utf-8")
    (demo_dir / "UserService.java").write_text("class UserService {}", encoding="utf-8")
    (repo_root / "README.md").write_text("hello", encoding="utf-8")

    symbol_indexer = SymbolIndexer(repo_root)
    symbol_indexer.rebuild_index()
    service = ScopeService(repo_root, symbol_indexer, ignored_dirs={".git", ".autopatch-j"})

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


def test_scope_service_rejects_class_and_method_mentions(tmp_path: Path) -> None:
    repo_root = tmp_path
    demo_dir = repo_root / "src" / "main" / "java" / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "UserService.java").write_text(
        "package demo;\n"
        "public class UserService {\n"
        "    public boolean isAdmin() { return true; }\n"
        "}\n",
        encoding="utf-8",
    )

    symbol_indexer = SymbolIndexer(repo_root)
    symbol_indexer.rebuild_index()
    service = ScopeService(repo_root, symbol_indexer, ignored_dirs={".git", ".autopatch-j"})

    assert service.fetch_scope("@UserService 检查代码") is None
    assert service.fetch_scope("@isAdmin 检查代码") is None


def test_scanner_runner_persists_scan_result(tmp_path: Path) -> None:
    repo_root = tmp_path
    java_file = repo_root / "Demo.java"
    java_file.write_text("class Demo {}", encoding="utf-8")

    artifacts = ArtifactManager(repo_root)
    symbol_indexer = SymbolIndexer(repo_root)
    symbol_indexer.rebuild_index()
    scope_service = ScopeService(repo_root, symbol_indexer)
    scope = scope_service.fetch_scope("@Demo.java 检查代码")
    assert scope is not None

    scanner_runner = ScannerRunner(repo_root, artifacts)
    artifact_id, result = scanner_runner.run_scan_and_save(scope)

    restored = artifacts.load_scan_result(artifact_id)
    assert result.status == "ok"
    assert restored is not None
    assert restored.targets == ["Demo.java"]
