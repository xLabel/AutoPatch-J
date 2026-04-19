from __future__ import annotations

SCAN_KEYWORDS = (
    "finding",
    "findings",
    "issue",
    "issues",
    "scan",
    "vulnerability",
    "vulnerabilities",
    "扫描",
    "检查",
    "漏洞",
    "问题",
)

PATCH_KEYWORDS = (
    "fix",
    "patch",
    "pacth",
    "修复",
    "补丁",
)

APPLY_KEYWORDS = (
    "apply",
    "应用",
    "写入",
    "落盘",
)

FINDINGS_REVIEW_KEYWORDS = (
    "show findings",
    "show finding",
    "show issues",
    "show issue",
    "列出问题",
    "查看问题",
    "看看问题",
    "显示问题",
)

PENDING_REVIEW_KEYWORDS = (
    "show patch",
    "show pending",
    "show diff",
    "查看 patch",
    "看看 patch",
    "查看patch",
    "看看patch",
    "查看补丁",
    "看看补丁",
    "查看 diff",
    "看看 diff",
)


def contains_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered or keyword in text for keyword in keywords)


def has_scan_intent(text: str) -> bool:
    return contains_keyword(text, SCAN_KEYWORDS)


def has_patch_intent(text: str) -> bool:
    return contains_keyword(text, PATCH_KEYWORDS)


def has_apply_intent(text: str) -> bool:
    return contains_keyword(text, APPLY_KEYWORDS)


def has_findings_review_intent(text: str) -> bool:
    return contains_keyword(text, FINDINGS_REVIEW_KEYWORDS)


def has_pending_review_intent(text: str) -> bool:
    return contains_keyword(text, PENDING_REVIEW_KEYWORDS)
