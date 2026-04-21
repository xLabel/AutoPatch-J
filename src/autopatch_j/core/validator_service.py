from __future__ import annotations

from pathlib import Path
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.scanners.base import JavaScanner


class SemanticValidator:
    """
    语义验证器 (Core Service)
    职责：通过重新扫描，验证补丁是否真正消除了漏洞。
    算法：基于规则 ID 和代码指纹比对。
    """

    def __init__(self, repo_root: Path, scanner: JavaScanner) -> None:
        self.repo_root = repo_root
        self.scanner = scanner

    def perform_verification(self, draft: PatchDraft) -> tuple[bool, str]:
        """
        针对补丁草案执行重新扫描并验证漏洞是否消失。
        返回: (是否修好, 详细说明)
        """
        if not draft.target_check_id or not draft.target_snippet:
            return True, "补丁未关联具体的漏洞源，跳过语义验证。"

        # 1. 针对补丁涉及的文件执行定向重扫
        rescan_result = self.scanner.scan(self.repo_root, [draft.file_path])
        
        if rescan_result.status != "ok":
            return False, f"语义重扫执行失败：{rescan_result.message}"

        # 2. 指纹比对逻辑
        # 寻找是否依然存在 规则一致 且 内容特征一致 的漏洞
        is_fixed = True
        for finding in rescan_result.findings:
            if finding.check_id == draft.target_check_id:
                # 如果原来的“有毒”片段依然出现在新漏洞的 snippet 中，说明没修掉
                if draft.target_snippet in finding.snippet:
                    is_fixed = False
                    break
        
        if not is_fixed:
            return False, f"语义校验失败：规则 [{draft.target_check_id}] 在重扫中依然被触发，补丁逻辑可能不正确。"
        
        return True, f"语义校验通过：规则 [{draft.target_check_id}] 已在该位置消失。"
