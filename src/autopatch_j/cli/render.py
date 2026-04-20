from __future__ import annotations

from rich.console import Console


class CliRenderer:
    def __init__(self) -> None:
        self.console = Console(markup=False)

    def print(self, text: object = "", end: str = "\n", style: str | None = None) -> None:
        self.console.print(text, end=end, style=style)
