"""有限终端输入驱动真实 CLI；仅替代交互表面。"""

from collections.abc import Callable
import io
from unittest import mock

from rich import console

from autopatch_j.cli import app
from autopatch_j.core.repair import models


def drive_terminal(
    cli: app.AutoPatchCli,
    inputs: list[str],
    before_input: Callable[[str | None, models.RepairState, str], None],
) -> str:
    """只替代有限终端输入输出，业务路由、展示与人工动作均由 CLI 执行。"""
    output = io.StringIO()
    cli.renderer.console = console.Console(
        file=output, width=160, color_system=None
    )
    pending = iter(inputs)
    failures: list[Exception] = []

    def prompt(*_args, **unused_options):
        command = next(pending, None)
        state = cli.command_handlers.displayed_state
        try:
            assert state is not None
            before_input(command, state, output.getvalue())
        except (
            Exception
        ) as exc:  # CLI 会处理普通输入错误，测试断言须在循环外继续抛出。
            failures.append(exc)
            raise EOFError from exc
        if command is None:
            raise EOFError
        return command

    cli.prompt_session = mock.MagicMock()
    cli.prompt_session.prompt.side_effect = prompt
    assert cli.run() == 0, output.getvalue()
    if failures:
        failures[0].add_note(output.getvalue()[-4000:])
        raise failures[0]
    assert not list(pending), output.getvalue()
    return output.getvalue()
