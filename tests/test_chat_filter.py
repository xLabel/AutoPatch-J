from __future__ import annotations

from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.models import IntentType


def test_chat_filter_basic_markdown_stripping() -> None:
    service = ChatFilter()
    answer = (
        "## 常见解法\n"
        "1. 暴力枚举\n"
        "2. 哈希表\n"
        "```python\n"
        "def two_sum(nums, target):\n"
        "    return []\n"
        "```\n"
        "哈希表一次遍历通常是最优解，时间复杂度 O(n)，空间复杂度 O(n)。\n"
        "如果需要，我还可以继续展开 Java 和 Python 代码实现。\n"
    )

    rendered = service.build_display_answer(
        user_text="leetcode 第1题的解法？",
        answer=answer,
        intent=IntentType.GENERAL_CHAT,
    )

    assert "##" not in rendered
    assert "```python" in rendered
    assert "常见解法" in rendered
    assert "哈希表一次遍历" in rendered
