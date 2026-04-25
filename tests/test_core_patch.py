from __future__ import annotations

import pytest
from pathlib import Path
from autopatch_j.core.patch_engine import PatchEngine, TargetFileNotFoundError, OldStringNotFoundError, OldStringNotUniqueError
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.core.patch_verifier import SyntaxCheckResult

def test_patch_lifecycle(tmp_path: Path):
    """验证补丁的完整生命周期：起草 -> 验证 -> 应用"""
    java_file = tmp_path / "App.java"
    java_file.write_text("public class App {\n    void run() { System.out.println(\"old\"); }\n}", encoding="utf-8")
    
    engine = PatchEngine(tmp_path)
    old = "System.out.println(\"old\");"
    new = "System.out.println(\"new\");"
    
    # 1. 测试起草 (Draft)
    new_c, diff_c = engine.perform_draft("App.java", old, new)
    assert new_c is not None
    assert diff_c is not None
    
    # 2. 测试应用 (Apply)
    draft = PatchDraft(
        file_path="App.java",
        old_string=old,
        new_string=new,
        diff=diff_c,
        validation=SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message=""
    )
    success = engine.perform_apply(draft)
    assert success
    updated_content = java_file.read_text(encoding="utf-8")
    assert "new" in updated_content
    assert "old" not in updated_content

def test_windows_crlf_matching(tmp_path: Path):
    """
    专项测试：验证 Windows CRLF 环境下的补丁匹配鲁棒性
    场景：磁盘文件是 CRLF，LLM 生成的是 LF。
    """
    java_file = tmp_path / "Win.java"
    # 显式使用 CRLF 写入
    content = "public class Win {\r\n    public void test() {\r\n        return;\r\n    }\r\n}"
    java_file.write_bytes(content.encode("utf-8"))
    
    engine = PatchEngine(tmp_path)
    # LLM 生成的 old_string 只有 LF
    old_code = "    public void test() {\n        return;\n    }"
    new_code = "    public void test() {\n        // Fixed\n        return;\n    }"
    
    new_c, diff_c = engine.perform_draft("Win.java", old_code, new_code)

    assert new_c is not None
    assert "Fixed" in diff_c
    
    draft = PatchDraft(
        file_path="Win.java",
        old_string=old_code,
        new_string=new_code,
        diff=diff_c,
        validation=SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message=""
    )
    # 验证应用后是否依然保持了 CRLF (或至少成功应用)
    success = engine.perform_apply(draft)
    assert success
    final_content = java_file.read_text(encoding="utf-8")
    assert "// Fixed" in final_content

def test_create_draft_failures(tmp_path: Path):
    """验证物理门禁的拦截逻辑"""
    java_file = tmp_path / "Test.java"
    java_file.write_text("code();\ncode();", encoding="utf-8")
    
    engine = PatchEngine(tmp_path)
    
    # 匹配不到
    with pytest.raises(OldStringNotFoundError):
        engine.perform_draft("Test.java", "non-existent", "...")
    
    # 匹配不唯一
    with pytest.raises(OldStringNotUniqueError):
        engine.perform_draft("Test.java", "code();", "...")

def test_path_traversal_defense(tmp_path: Path):
    """验证安全底线：拦截路径穿越攻击"""
    engine = PatchEngine(tmp_path)
    with pytest.raises(PermissionError) as excinfo:
        engine.perform_draft("../../../etc/passwd", "any", "any")
    assert "安全风险拦截" in str(excinfo.value)
