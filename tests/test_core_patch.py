from __future__ import annotations

import unittest
import tempfile
import shutil
from pathlib import Path
from autopatch_j.core.patch_engine import PatchEngine, PatchDraft


class TestPatchEngine(unittest.TestCase):
    """
    PatchEngine 核心链路测试
    重点：物理唯一性校验、补丁应用一致性、路径安全防御。
    """

    def setUp(self) -> None:
        # 创建临时测试仓库环境 (盘符一致性)
        project_root = Path(__file__).parent.parent
        self.test_parent = project_root / ".test_temp"
        self.test_parent.mkdir(exist_ok=True)
        self.test_dir = Path(tempfile.mkdtemp(dir=self.test_parent))
        self.engine = PatchEngine(self.test_dir)
        
        # 准备一个标准的 Java 测试文件
        self.file_path = "UserService.java"
        self.java_content = (
            "public class UserService {\n"
            "    public void login() {\n"
            "        System.out.println(\"Hello\");\n"
            "    }\n"
            "}\n"
        )
        (self.test_dir / self.file_path).write_text(self.java_content, encoding="utf-8")

    def tearDown(self) -> None:
        # 清理临时目录
        shutil.rmtree(self.test_dir)

    def test_create_draft_success(self):
        """测试正常起草补丁"""
        old = "System.out.println(\"Hello\");"
        new = "System.out.println(\"World\");"
        
        draft = self.engine.create_draft(self.file_path, old, new)
        
        # 允许 ok (成功) 或 unavailable (因环境缺 tree-sitter)
        self.assertIn(draft.status, ("ok", "unavailable"))
        
        if draft.status == "ok":
            self.assertIn("-System.out.println(\"Hello\");", draft.diff)
            self.assertIn("+System.out.println(\"World\");", draft.diff)

    def test_create_draft_not_found(self):
        """测试 old_string 匹配不到的情况"""
        draft = self.engine.create_draft(self.file_path, "Non-existent code", "...")
        self.assertEqual(draft.status, "error")
        self.assertIn("未找到", draft.message)

    def test_create_draft_ambiguous(self):
        """测试 old_string 匹配多次的情况（物理门禁）"""
        # 注入重复内容
        (self.test_dir / self.file_path).write_text("duplicate();\nduplicate();", encoding="utf-8")
        
        draft = self.engine.create_draft(self.file_path, "duplicate();", "fixed();")
        self.assertEqual(draft.status, "error")
        self.assertIn("匹配了 2 处", draft.message)

    def test_apply_patch_physical_integrity(self):
        """测试补丁的物理落盘及一致性"""
        old = "System.out.println(\"Hello\");"
        new = "System.out.println(\"Applied\");"
        
        draft = self.engine.create_draft(self.file_path, old, new)
        success = self.engine.apply_patch(draft)
        
        self.assertTrue(success)
        updated_content = (self.test_dir / self.file_path).read_text(encoding="utf-8")
        self.assertIn("Applied", updated_content)
        self.assertNotIn("Hello", updated_content)

    def test_path_traversal_defense(self):
        """测试路径穿越攻击防御 (安全底线)"""
        malicious_path = "../../../etc/passwd"
        
        with self.assertRaises(PermissionError) as cm:
            self.engine.create_draft(malicious_path, "any", "any")
        
        self.assertIn("安全风险拦截", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
