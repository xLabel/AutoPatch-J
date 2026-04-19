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


def has_scan_intent(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered or keyword in text for keyword in SCAN_KEYWORDS)
