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


def contains_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered or keyword in text for keyword in keywords)


def has_scan_intent(text: str) -> bool:
    return contains_keyword(text, SCAN_KEYWORDS)


def has_patch_intent(text: str) -> bool:
    return contains_keyword(text, PATCH_KEYWORDS)


def has_apply_intent(text: str) -> bool:
    return contains_keyword(text, APPLY_KEYWORDS)
