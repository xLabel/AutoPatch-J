from __future__ import annotations

import re

from autopatch_j.core.models import IntentType


class ChatFilter:
    """
    输出内容治理服务 (Output Governance)。
    核心职责：限制非编程域的闲聊，并对 LLM 冗长的回答进行物理裁切。
    设计理念是不鼓励模型输出长篇大论的“教程”或 Markdown 编号大纲，而是追求类似原生 CLI 工具的克制输出。
    包含“去 Markdown 化”和智能截断逻辑，强制回答控制在几句话以内（除非用户显式给出如“详细”的展开指令）。
    """

    _DETAIL_HINTS: tuple[str, ...] = (
        "详细",
        "展开",
        "完整",
        "逐步",
        "一步一步",
        "详细过程",
        "完整过程",
        "详细讲",
        "展开讲",
        "详细解释",
    )
    _CODE_HINTS: tuple[str, ...] = (
        "代码",
        "示例",
        "实现",
        "demo",
        "sample",
        "python",
        "java",
        "javascript",
        "typescript",
        "go",
        "rust",
        "c++",
        "c#",
    )
    _PROGRAMMING_HINTS: tuple[str, ...] = (
        "代码",
        "编程",
        "程序",
        "开发",
        "调试",
        "报错",
        "异常",
        "堆栈",
        "漏洞",
        "修复",
        "补丁",
        "架构",
        "算法",
        "leetcode",
        "npe",
        "nullpointerexception",
        "java",
        "python",
        "javascript",
        "typescript",
        "go",
        "rust",
        "sql",
        "接口",
        "框架",
        "日志",
        "模型",
        "llm",
        "deepseek",
        "autopatch",
        "bug",
        "fix",
        "error",
        "stacktrace",
    )
    _IDENTITY_HINTS: tuple[str, ...] = (
        "你是谁",
        "你能做什么",
        "接入的llm",
        "接入的模型",
        "什么模型",
        "哪家的模型",
        "autopatch",
    )
    _NON_PROGRAMMING_HINTS: tuple[str, ...] = (
        "番茄炒蛋",
        "做饭",
        "菜谱",
        "旅游",
        "减肥",
        "穿搭",
        "感情",
        "恋爱",
        "星座",
        "育儿",
        "睡眠",
        "食谱",
        "怎么做菜",
    )
    _CONTINUE_SUFFIX: str = "如需展开，我可以继续给代码示例或逐步说明。"

    def verify_programming_related(self, user_text: str) -> bool:
        normalized = self._normalize_text(user_text)
        if self._contains_any(normalized, self._IDENTITY_HINTS):
            return True
        if self._contains_any(normalized, self._NON_PROGRAMMING_HINTS):
            return False
        if self._contains_any(normalized, self._PROGRAMMING_HINTS):
            return True
        return bool(re.search(r"[@/`{}();:=<>_\[\]]", user_text))

    def fetch_out_of_scope_reply(self) -> str:
        return "我主要处理代码、修复和项目相关问题。如果你有代码、错误日志或补丁需求，我可以继续。"

    def build_display_answer(
        self,
        user_text: str,
        answer: str,
        intent: IntentType,
    ) -> str:
        if intent not in {IntentType.GENERAL_CHAT, IntentType.CODE_EXPLAIN}:
            return answer.strip()

        keep_code = self.verify_explicit_code_request(user_text)
        normalized = self._strip_markdown_artifacts(answer.strip(), keep_code=keep_code)
        if self.verify_explicit_detail_request(user_text):
            return normalized
        return self._compress_answer(normalized)

    def verify_explicit_detail_request(self, user_text: str) -> bool:
        normalized = self._normalize_text(user_text)
        return self._contains_any(normalized, self._DETAIL_HINTS) or self.verify_explicit_code_request(user_text)

    def verify_explicit_code_request(self, user_text: str) -> bool:
        normalized = self._normalize_text(user_text)
        return self._contains_any(normalized, self._CODE_HINTS)

    def _strip_markdown_artifacts(self, text: str, keep_code: bool) -> str:
        normalized = text.replace("\r\n", "\n")
        if keep_code:
            normalized = re.sub(r"```[^\n]*\n", "", normalized)
            normalized = normalized.replace("```", "")
        else:
            normalized = re.sub(r"```.*?```", "", normalized, flags=re.DOTALL)
        normalized = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", normalized)
        normalized = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "", normalized)
        normalized = normalized.replace("**", "")
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def _compress_answer(self, answer: str) -> str:
        cleaned = answer.strip()
        if not cleaned:
            return cleaned
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not self._should_compress(cleaned, lines):
            return cleaned

        sentences = [
            segment.strip()
            for segment in re.split(r"(?<=[。！？!?\.])\s+", cleaned)
            if segment.strip()
        ]
        summary_parts: list[str] = []
        if sentences:
            for sentence in sentences:
                candidate = " ".join(summary_parts + [sentence]).strip()
                if len(candidate) > 220 and summary_parts:
                    break
                summary_parts.append(sentence)
                if len(summary_parts) >= 3:
                    break
            summary = " ".join(summary_parts).strip()
        else:
            summary = " ".join(lines[:3]).strip()

        if not summary:
            summary = cleaned[:220].strip()
        if len(summary) > 260:
            summary = summary[:260].rstrip(" ，。,.") + "。"
        return f"{summary}\n\n{self._CONTINUE_SUFFIX}"

    def _should_compress(self, answer: str, lines: list[str]) -> bool:
        if len(answer) > 420:
            return True
        if len(lines) > 8:
            return True
        if re.search(r"(?m)^\s*(\d+\.|[-*])\s+", answer):
            return True
        return "##" in answer or "```" in answer

    def _contains_any(self, normalized_text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in normalized_text for keyword in keywords)

    def _normalize_text(self, user_text: str) -> str:
        compact = re.sub(r"\s+", "", user_text)
        return compact.lower()
