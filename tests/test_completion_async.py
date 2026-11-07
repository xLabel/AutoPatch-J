"""原生输入会话中阻塞索引查询时的输入、过期结果和退出边界。"""

import asyncio
import threading

from prompt_toolkit import completion
from prompt_toolkit import document
from prompt_toolkit import input as prompt_input
from prompt_toolkit import output as prompt_output
from prompt_toolkit.application import current
import pytest

from autopatch_j.cli import completer
from autopatch_j.cli import input_controller
from autopatch_j.core.project import symbol_index


@pytest.mark.parametrize("terminal_type", ["xterm-256color", "dumb"])
@pytest.mark.parametrize("entry_kind", ["file", "dir"])
def test_current_native_completion_replaces_path_and_resumes(
    tmp_path,
    monkeypatch,
    terminal_type,
    entry_kind,
):
    monkeypatch.setenv("TERM", terminal_type)
    directory = tmp_path / "src"
    directory.mkdir()
    name = "Alpha.java" if entry_kind == "file" else "AlphaDir"
    path = directory / name
    if entry_kind == "file":
        path.write_bytes(b"class Alpha {}")
    else:
        path.mkdir()
    index = symbol_index.SymbolIndex(tmp_path)
    index.rebuild_index()
    release = threading.Event()
    errors = []
    typed = "查看 @Al"
    expected = f"查看 @src/{name} "

    async def exercise(terminal_input):
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        candidates_ready = asyncio.Event()
        accepted = asyncio.Event()
        queries = []

        def search(query):
            queries.append(query)
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise TimeoutError("test query was not released")
            return index.search(query, kinds=["file", "dir"]).entries

        async def report(operation, exc):
            errors.append((operation, exc))

        controller = input_controller.CliInputController(
            search, tmp_path, report
        )
        session = controller.create_prompt_session()
        instance = session.completer
        assert isinstance(instance, completer.AutoPatchCompleter)
        instance.path_completion_failed = True
        buffer = session.default_buffer

        def observe_candidates(unused_buffer):
            state = buffer.complete_state
            if state and state.completions:
                candidates_ready.set()

        def observe_text(unused_buffer):
            if buffer.text == expected:
                accepted.set()

        buffer.on_completions_changed += observe_candidates
        buffer.on_text_changed += observe_text
        task = asyncio.create_task(session.prompt_async())
        try:
            terminal_input.send_text(typed + "\t")
            await asyncio.wait_for(started.wait(), 3)
            assert buffer.text == typed and buffer.cursor_position == len(typed)
            assert instance.path_completion_failed
            release.set()
            await asyncio.wait_for(candidates_ready.wait(), 3)
            state = buffer.complete_state
            assert state is not None and state.original_document.text == typed
            assert [
                (item.text, item.start_position, item.display_text)
                for item in state.completions
            ] == [(f"src/{name}", -2, name)]
            assert not instance.path_completion_failed and not errors
            terminal_input.send_text("\t")
            await asyncio.wait_for(accepted.wait(), 3)
            assert buffer.text == expected and buffer.cursor_position == len(
                expected
            )
            assert queries == ["Al"]
            terminal_input.send_text("\r")
            assert await asyncio.wait_for(task, 3) == expected
            assert not instance.path_completion_failed and not errors
        finally:
            release.set()
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    with prompt_input.create_pipe_input() as terminal_input:
        with current.create_app_session(
            input=terminal_input, output=prompt_output.DummyOutput()
        ):
            asyncio.run(exercise(terminal_input))


@pytest.mark.parametrize("terminal_type", ["xterm-256color", "dumb"])
@pytest.mark.parametrize("failure", [False, True])
def test_slow_native_completion_preserves_input_and_ignores_stale_outcome(
    tmp_path,
    monkeypatch,
    terminal_type,
    failure,
):
    monkeypatch.setenv("TERM", terminal_type)
    (tmp_path / "A.java").write_bytes(b"class A {}")
    index = symbol_index.SymbolIndex(tmp_path)
    index.rebuild_index()
    release = threading.Event()
    errors = []
    thread_ids = []

    async def exercise(terminal_input):
        started = asyncio.Event()
        completed = asyncio.Event()
        applications = []
        loop = asyncio.get_running_loop()
        main_thread = threading.get_ident()

        def search(query):
            thread_ids.append(threading.get_ident())
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise TimeoutError("test query was not released")
            if failure:
                raise PermissionError("late failure")
            return index.search(query, kinds=["file", "dir"]).entries

        async def report(operation, exc):
            errors.append((operation, exc, threading.get_ident()))

        controller = input_controller.CliInputController(
            search, tmp_path, report
        )
        session = controller.create_prompt_session()
        instance = session.completer
        assert isinstance(instance, completer.AutoPatchCompleter)
        instance.path_completion_failed = True
        buffer = session.default_buffer
        seen = asyncio.Event()

        def observe(unused_buffer):
            application = current.get_app_or_none()
            if application is not None and application.is_running:
                applications.append(application)
            if buffer.text == "@Abc" and buffer.cursor_position == 3:
                seen.set()

        buffer.on_text_changed += observe
        buffer.on_cursor_position_changed += observe

        def completion_changed(unused_buffer):
            if release.is_set() and buffer.text == "/he":
                completed.set()

        buffer.on_completions_changed += completion_changed
        task = asyncio.create_task(session.prompt_async())
        try:
            terminal_input.send_text("@A\t")
            await asyncio.wait_for(started.wait(), 3)
            terminal_input.send_text("bc\x1b[D")
            await asyncio.wait_for(seen.wait(), 3)
            assert not release.is_set() and not task.done()
            assert (
                thread_ids == [thread_ids[0]] and thread_ids[0] != main_thread
            )
            terminal_input.send_text("\x01\x0b/he")
            changed = asyncio.Event()
            buffer.on_text_changed += lambda unused_buffer: (
                changed.set() if buffer.text == "/he" else None
            )
            if buffer.text != "/he":
                await asyncio.wait_for(changed.wait(), 3)
            release.set()
            await asyncio.wait_for(completed.wait(), 3)
            assert not errors
            assert instance.path_completion_failed
            candidates = asyncio.Event()

            def got_commands(unused_buffer):
                if buffer.complete_state and any(
                    item.display_text == "/help"
                    for item in buffer.complete_state.completions
                ):
                    candidates.set()

            buffer.on_completions_changed += got_commands
            terminal_input.send_text("\t")
            await asyncio.wait_for(candidates.wait(), 3)
            # 原生 dumb prompt 的 Enter 直接提交；仍可手工完成命令。
            terminal_input.send_text(
                "lp\r" if terminal_type == "dumb" else "\r\r"
            )
            assert await asyncio.wait_for(task, 3) == "/help"
            next_prompt = asyncio.create_task(session.prompt_async())
            terminal_input.send_text("\x04")
            with pytest.raises(EOFError):
                await asyncio.wait_for(next_prompt, 3)
            assert not errors
            assert instance.path_completion_failed
        finally:
            release.set()
            if not task.done() and applications and applications[-1].is_running:
                applications[-1].exit(result="")
                await task

    with prompt_input.create_pipe_input() as terminal_input:
        with current.create_app_session(
            input=terminal_input, output=prompt_output.DummyOutput()
        ):
            asyncio.run(exercise(terminal_input))


def test_cancelled_completion_does_not_report_or_reset_failure():
    release = threading.Event()
    errors = []

    async def exercise():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        finished = asyncio.Event()

        def search(unused_query):
            loop.call_soon_threadsafe(started.set)
            try:
                if not release.wait(5):
                    raise TimeoutError("test query was not released")
                raise OSError("late cancelled failure")
            finally:
                loop.call_soon_threadsafe(finished.set)

        async def report(operation, exc):
            errors.append((operation, exc))

        instance = completer.AutoPatchCompleter(search, report)
        instance.path_completion_failed = True

        async def collect():
            return [
                item
                async for item in instance.get_completions_async(
                    document.Document("@A"),
                    completion.CompleteEvent(completion_requested=True),
                )
            ]

        task = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert instance.path_completion_failed
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), 3)
        assert not errors and instance.path_completion_failed

    asyncio.run(exercise())


@pytest.mark.parametrize("terminal_type", ["xterm-256color", "dumb"])
@pytest.mark.parametrize("command", ["/init", "/reset", "/quit"])
def test_leaving_native_prompt_cancels_publication_not_worker(
    tmp_path,
    monkeypatch,
    terminal_type,
    command,
):
    monkeypatch.setenv("TERM", terminal_type)
    release = threading.Event()
    errors = []

    async def exercise(terminal_input):
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        finished = asyncio.Event()

        def search(unused_query):
            loop.call_soon_threadsafe(started.set)
            try:
                if not release.wait(5):
                    raise TimeoutError("test worker was not released")
                raise OSError("old prompt failure")
            finally:
                loop.call_soon_threadsafe(finished.set)

        async def report(operation, exc):
            errors.append((operation, exc))

        controller = input_controller.CliInputController(
            search, tmp_path, report
        )
        session = controller.create_prompt_session()
        task = asyncio.create_task(session.prompt_async())
        try:
            terminal_input.send_text("@")
            await asyncio.wait_for(started.wait(), 3)
            terminal_input.send_text("\x01\x0b" + command + "\r")
            assert await asyncio.wait_for(task, 3) == command
            assert not release.is_set()
        finally:
            release.set()
        await asyncio.wait_for(finished.wait(), 3)
        instance = session.completer
        assert isinstance(instance, completer.AutoPatchCompleter)
        assert not errors and not instance.path_completion_failed
        next_prompt = asyncio.create_task(session.prompt_async())
        terminal_input.send_text("/help\r")
        assert await asyncio.wait_for(next_prompt, 3) == "/help"
        assert not errors

    with prompt_input.create_pipe_input() as terminal_input:
        with current.create_app_session(
            input=terminal_input, output=prompt_output.DummyOutput()
        ):
            asyncio.run(exercise(terminal_input))
