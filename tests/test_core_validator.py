from __future__ import annotations

import unittest
from unittest.mock import MagicMock
from pathlib import Path
from autopatch_j.core.validator_service import SemanticValidator
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.scanners.base import ScanResult, Finding
from autopatch_j.validators.java_syntax import SyntaxValidationResult


class TestSemanticValidator(unittest.TestCase):
    """
    SemanticValidator 核心链路测试
    重点：验证“代码指纹比对”算法是否能准确识别漏洞的消除。
    """

    def setUp(self) -> None:
        self.repo_root = Path("/tmp/mock-repo")
        self.mock_scanner = MagicMock()
        self.validator = SemanticValidator(self.repo_root, self.mock_scanner)
        
        # 准备一个补丁草案快照
        self.draft = PatchDraft(
            file_path="Auth.java",
            old_string="MD5",
            new_string="SHA256",
            diff="...",
            validation=SyntaxValidationResult(status="ok", message=""),
            status="ok",
            message="",
            target_check_id="weak-crypto",
            target_snippet="MessageDigest.getInstance(\"MD5\")"
        )

    def test_verify_fix_success(self):
        """测试修复成功场景：重扫结果中不再包含旧指纹"""
        # 模拟扫描返回：同一个规则，但 snippet 变了（指纹消失）
        self.mock_scanner.scan.return_value = ScanResult(
            engine="semgrep", scope=[], targets=[], status="ok", message="",
            findings=[
                Finding(check_id="weak-crypto", path="Auth.java", start_line=10, end_line=10, 
                        severity="error", message="...", rule="...", 
                        snippet="MessageDigest.getInstance(\"SHA256\")") # 指纹已变
            ]
        )
        
        success, msg = self.validator.verify_fix(self.draft)
        self.assertTrue(success)
        self.assertIn("已在该位置消失", msg)

    def test_verify_fix_failed(self):
        """测试修复失败场景：重扫结果中依然包含旧指纹"""
        # 模拟扫描返回：漏洞依然存在，指纹完全匹配
        self.mock_scanner.scan.return_value = ScanResult(
            engine="semgrep", scope=[], targets=[], status="ok", message="",
            findings=[
                Finding(check_id="weak-crypto", path="Auth.java", start_line=15, end_line=15, 
                        severity="error", message="...", rule="...", 
                        snippet="MessageDigest.getInstance(\"MD5\")") # 还是旧指纹！
            ]
        )
        
        success, msg = self.validator.verify_fix(self.draft)
        self.assertFalse(success)
        self.assertIn("依然被触发", msg)

    def test_verify_fix_rule_gone(self):
        """测试修复成功场景：该规则的漏洞完全消失"""
        self.mock_scanner.scan.return_value = ScanResult(
            engine="semgrep", scope=[], targets=[], status="ok", message="",
            findings=[] # 没有任何发现
        )
        
        success, msg = self.validator.verify_fix(self.draft)
        self.assertTrue(success)
        self.assertIn("已在该位置消失", msg)


if __name__ == "__main__":
    unittest.main()
