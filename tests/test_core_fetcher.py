from __future__ import annotations

import pytest
from pathlib import Path
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.index_service import IndexEntry


def test_fetch_logic(tmp_path: Path):
    """验证代码提取逻辑：全量提取与智能块提取"""
    java_code = (
        "public class Demo {\n"
        "    public void run() {\n"
        "        // Line 3\n"
        "    }\n"
        "}\n"
    )
    file_path = "Demo.java"
    (tmp_path / file_path).write_text(java_code, encoding="utf-8")
    
    fetcher = CodeFetcher(tmp_path)
    
    # 1. 测试全文提取
    full_entry = IndexEntry(path=file_path, name="Demo.java", kind="file", line=0)
    assert fetcher.fetch_entry(full_entry) == java_code
    
    # 2. 测试智能块提取 (行号 2 开始)
    method_entry = IndexEntry(path=file_path, name="run", kind="method", line=2)
    snippet = fetcher.fetch_entry(method_entry)
    assert "public void run()" in snippet
    assert "// Line 3" in snippet


def test_fetch_range(tmp_path: Path):
    """验证物理行号区间提取"""
    (tmp_path / "L.txt").write_text("1\n2\n3\n4\n5", encoding="utf-8")
    fetcher = CodeFetcher(tmp_path)
    assert fetcher.fetch_lines("L.txt", 2, 4) == "2\n3\n4"
