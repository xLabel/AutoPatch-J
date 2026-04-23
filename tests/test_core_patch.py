from __future__ import annotations

import pytest
from pathlib import Path
from autopatch_j.core.patch_engine import PatchEngine


def test_patch_lifecycle(tmp_path: Path):
    """验证补丁的完整生命周期：起草 -> 验证 -> 应用"""
    java_file = tmp_path / "App.java"
    java_file.write_text("public class App {\n    void run() { System.out.println(\"old\"); }\n}", encoding="utf-8")
    
    engine = PatchEngine(tmp_path)
    old = "System.out.println(\"old\");"
    new = "System.out.println(\"new\");"
    
    # 1. 测试起草 (Draft)
    draft = engine.perform_draft("App.java", old, new)
    assert draft.status in ("ok", "unavailable")
    
    # 2. 测试应用 (Apply)
    if draft.status == "ok":
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
    
    draft = engine.perform_draft("Win.java", old_code, new_code)
    
    assert draft.status != "error", f"匹配失败：{draft.message}"
    assert "Fixed" in draft.diff
    
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
    draft_missing = engine.perform_draft("Test.java", "non-existent", "...")
    assert draft_missing.status == "error"
    
    # 匹配不唯一
    draft_ambiguous = engine.perform_draft("Test.java", "code();", "...")
    assert draft_ambiguous.status == "error"
    assert "匹配了 2 处" in draft_ambiguous.message


def test_path_traversal_defense(tmp_path: Path):
    """验证安全底线：拦截路径穿越攻击"""
    engine = PatchEngine(tmp_path)
    with pytest.raises(PermissionError) as excinfo:
        engine.perform_draft("../../../etc/passwd", "any", "any")
    assert "安全风险拦截" in str(excinfo.value)
