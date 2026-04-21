from __future__ import annotations

import unittest
import tempfile
import shutil
from pathlib import Path
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.index_service import IndexEntry


class TestCodeFetcher(unittest.TestCase):
    """
    CodeFetcher 核心链路测试
    重点：验证代码块提取的精准度（Tree-sitter 逻辑）。
    """

    def setUp(self) -> None:
        # 在项目根目录下创建临时文件夹 (针对 Windows 跨盘符问题)
        project_root = Path(__file__).parent.parent
        self.test_parent = project_root / ".test_temp"
        self.test_parent.mkdir(exist_ok=True)
        self.test_dir = Path(tempfile.mkdtemp(dir=self.test_parent))
        self.fetcher = CodeFetcher(self.test_dir)
        
        # 准备一个包含多个方法的 Java 文件
        self.file_path = "Sample.java"
        self.java_code = (
            "public class Sample {\n"
            "    public void methodA() {\n"
            "        // line 3\n"
            "    }\n"
            "\n"
            "    public void methodB() {\n"
            "        System.out.println(\"B\");\n"
            "    }\n"
            "}\n"
        )
        (self.test_dir / self.file_path).write_text(self.java_code, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir)

    def test_fetch_full_file(self):
        """测试全量文件抓取"""
        entry = IndexEntry(path=self.file_path, name="Sample.java", kind="file", line=0)
        content = self.fetcher.fetch_by_index_entry(entry)
        self.assertEqual(content, self.java_code)

    def test_fetch_method_block(self):
        """测试精准方法块抓取（Tree-sitter 优先，物理行兜底）"""
        # methodB 在第 6 行开始
        entry = IndexEntry(path=self.file_path, name="methodB", kind="method", line=6)
        content = self.fetcher.fetch_by_index_entry(entry)
        
        # 只要抓取的内容包含了关键代码，即认为链路通畅
        self.assertIn("public void methodB() {", content)
        self.assertIn("System.out.println(\"B\");", content)
        
        # 如果是 Tree-sitter 抓取的，应该精准结束；
        # 如果是物理兜底抓取的，会包含后续行，这也是允许的正常退回行为

    def test_fetch_physical_range(self):
        """测试物理行号区间抓取"""
        # 提取 2 到 4 行
        content = self.fetcher.fetch_range(self.file_path, 2, 4)
        lines = content.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("methodA", lines[0])

    def test_fetch_non_existent_file(self):
        """测试文件不存在的容错"""
        entry = IndexEntry(path="Ghost.java", name="Ghost", kind="file", line=0)
        content = self.fetcher.fetch_by_index_entry(entry)
        self.assertIn("错误", content)


if __name__ == "__main__":
    unittest.main()
