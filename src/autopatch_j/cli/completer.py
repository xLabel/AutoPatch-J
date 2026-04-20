from __future__ import annotations

import re
from collections.abc import Callable, Iterator

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from autopatch_j.indexer import IndexEntry
from autopatch_j.mentions import build_mention_completions


MENTION_TOKEN_PATTERN = re.compile(r"(?<!\S)@([^\s]*)$")


class MentionCompleter(Completer):
    def __init__(
        self,
        index: Callable[[], list[IndexEntry]],
        recent_paths: Callable[[], list[str]],
    ) -> None:
        self.index = index
        self.recent_paths = recent_paths

    def get_completions(
        self,
        document: Document,
        complete_event: object,
    ) -> Iterator[Completion]:
        del complete_event
        match = MENTION_TOKEN_PATTERN.search(document.text_before_cursor)
        if match is None:
            return

        token = f"@{match.group(1)}"
        for candidate in build_mention_completions(
            index=self.index(),
            token=token,
            recent_paths=self.recent_paths(),
        ):
            yield Completion(
                candidate,
                start_position=-len(token),
                display=candidate.strip(),
            )
