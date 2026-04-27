from __future__ import annotations

from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.models import IntentType


def test_chat_filter_limits_general_chat_to_programming_topics() -> None:
    service = ChatFilter()

    assert service.verify_programming_related("你是谁")
    assert service.verify_programming_related("NPE 一般发生在什么场景")
    assert service.verify_programming_related("leetcode 第1题的解法？")
    assert not service.verify_programming_related("番茄炒蛋怎么做")


def test_chat_filter_compacts_long_markdown_answer_by_default() -> None:
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

    compacted = service.build_display_answer(
        user_text="leetcode 第1题的解法？",
        answer=answer,
        intent=IntentType.GENERAL_CHAT,
    )

    assert "##" not in compacted
    assert "```" not in compacted
    assert "如需展开，我可以继续给代码示例或逐步说明。" in compacted


def test_chat_filter_keeps_more_detail_when_user_explicitly_requests_code() -> None:
    service = ChatFilter()
    answer = (
        "## Python 示例\n"
        "```python\n"
        "def hello():\n"
        "    return 'world'\n"
        "```\n"
    )

    rendered = service.build_display_answer(
        user_text="给我 Python 代码示例",
        answer=answer,
        intent=IntentType.GENERAL_CHAT,
    )

    assert "##" not in rendered
    assert "```" not in rendered
    assert "def hello()" in rendered


def test_chat_filter_detects_explicit_detail_and_code_requests() -> None:
    service = ChatFilter()

    assert service.verify_explicit_detail_request("详细展开讲一下")
    assert service.verify_explicit_code_request("给我一个 Python 代码示例")


def test_chat_filter_out_of_scope_reply_has_no_garbled_text() -> None:
    service = ChatFilter()

    reply = service.fetch_out_of_scope_reply()

    assert "代码" in reply
    assert "错误日志" in reply
    assert "锟" not in reply
    assert "�" not in reply
