from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import ToolCall

class DeepSeekAliyunDialect:
    """
    阿里云百炼 DSML 流式方言适配器。

    职责边界：
    1. 从流式文本中隐藏 <｜DSML｜> 标记，避免标签泄漏到 CLI。
    2. 把 DSML function_calls 解析为标准 ToolCall。
    3. 不参与工具执行，也不处理非 DSML 的标准 OpenAI tool_calls。
    """
    _DSML_MARKER_PATTERN = re.compile(r"<\s*[｜|]\s*DSML\s*[｜|]")
    _DSML_INVOKE_PATTERN = re.compile(
        r"<\s*[｜|]\s*DSML\s*[｜|]\s*invoke\s+name=\"(?P<name>[^\"]+)\">\s*(?P<params>.*?)\s*</\s*[｜|]\s*DSML\s*[｜|]\s*invoke>",
        re.DOTALL,
    )
    _DSML_PARAM_PATTERN = re.compile(
        r"<\s*[｜|]\s*DSML\s*[｜|]\s*parameter\s+name=\"(?P<key>[^\"]+)\"[^>]*>(?P<value>.*?)</\s*[｜|]\s*DSML\s*[｜|]\s*parameter>",
        re.DOTALL,
    )

    def __init__(self) -> None:
        self.suppressed = False
        self.pending = ""

    def consume_visible_text(self, chunk: str) -> str:
        if self.suppressed:
            return ""

        marker = "<｜DSML｜"
        combined = f"{self.pending}{chunk}"
        marker_index = combined.find(marker)
        if marker_index >= 0:
            self.suppressed = True
            self.pending = ""
            return combined[:marker_index]

        keep = min(len(marker) - 1, len(combined))
        if len(combined) <= keep:
            self.pending = combined
            return ""

        emit = combined[:-keep]
        self.pending = combined[-keep:]
        return emit

    def flush_visible_text(self) -> str:
        if self.suppressed:
            self.pending = ""
            return ""
        pending = str(self.pending)
        self.pending = ""
        return pending

    def extract_tool_calls(self, full_content: str) -> list[ToolCall]:
        from .base import ToolCall
        if not self._DSML_MARKER_PATTERN.search(full_content):
            return []
            
        calls = []
        for i, match in enumerate(self._DSML_INVOKE_PATTERN.finditer(full_content)):
            name = match.group("name")
            params_raw = match.group("params")
            arguments = {}

            for p_match in self._DSML_PARAM_PATTERN.finditer(params_raw):
                val = p_match.group("value").strip()
                if val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                elif val.isdigit():
                    val = int(val)
                arguments[p_match.group("key")] = val

            calls.append(ToolCall(
                name=name,
                arguments=arguments,
                call_id=f"dsml-{i}",
                raw_arguments=params_raw
            ))
        return calls

    def strip_markup(self, full_content: str) -> str:
        match = self._DSML_MARKER_PATTERN.search(full_content)
        return full_content[:match.start()].rstrip() if match else full_content
