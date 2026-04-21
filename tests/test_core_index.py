from __future__ import annotations

import pytest
from pathlib import Path
from autopatch_j.core.index_service import IndexService


def test_rebuild_index_and_stats(tmp_path: Path):
    """测试全量重建索引"""
    # 准备测试文件
    java_file = tmp_path / "com" / "demo" / "AuthService.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text(
        "package com.demo;\n"
        "public class AuthService {\n"
        "    public void login() {}\n"
        "}\n",
        encoding="utf-8"
    )
    
    indexer = IndexService(tmp_path)
    stats = indexer.rebuild_index()
    
    # 核心测试：验证物理文件和目录是否被发现
    # 在这个结构下，应该有 1 个文件 (AuthService.java) 和 2 个目录 (com, demo)
    assert stats.get("file", 0) >= 1
    assert stats.get("dir", 0) >= 2
    assert stats.get("total", 0) >= 3


def test_search_symbols(tmp_path: Path):
    """测试模糊搜索功能"""
    java_file = tmp_path / "AuthService.java"
    java_file.write_text("public class AuthService { public void login() {} }", encoding="utf-8")
    
    indexer = IndexService(tmp_path)
    indexer.rebuild_index()
    
    # 基础测试：搜索文件名
    results = indexer.search("AuthService")
    assert any("AuthService" in r.name for r in results)


def test_ignored_dirs(tmp_path: Path):
    """测试忽略目录功能"""
    # 创建一个应该被忽略的目录 (如 .git)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("...")
    
    indexer = IndexService(tmp_path)
    indexer.rebuild_index()
    
    # 搜索 .git，虽然物理存在，但由于在 GlobalConfig.ignored_dirs 中，结果应为空
    # 注意：我们的 IndexService 会在 dirs[:] 中剔除黑名单，所以它根本不会进库
    results = indexer.search(".git")
    assert len(results) == 0
