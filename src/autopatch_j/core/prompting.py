from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptSection:
    """A named prompt block that keeps long prompts readable and reusable."""

    title: str
    body: str

    def render(self) -> str:
        return f"## {self.title}\n{self.body.strip()}"


def render_prompt_sections(*sections: PromptSection | str) -> str:
    """Render prompt assets with stable spacing between logical sections."""
    rendered: list[str] = []
    for section in sections:
        if isinstance(section, PromptSection):
            rendered.append(section.render())
        else:
            rendered.append(section.strip())
    return "\n\n".join(part for part in rendered if part)
