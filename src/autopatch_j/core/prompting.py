"""Markdown 提示章节及其确定性拼接。"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class PromptSection:
    """带标题的提示词内容块，用于明确分隔任务、证据和用户输入。"""

    title: str
    body: str

    def render(self) -> str:
        """将具名内容渲染为独立 Markdown section。"""
        return f"## {self.title}\n{self.body.strip()}"


def render_prompt_sections(*sections: PromptSection | str) -> str:
    """以稳定间距组合提示词 section，保留输入顺序。"""
    rendered: list[str] = []
    for section in sections:
        if isinstance(section, PromptSection):
            rendered.append(section.render())
        else:
            rendered.append(section.strip())
    return "\n\n".join(part for part in rendered if part)
