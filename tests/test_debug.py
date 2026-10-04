import asyncio
import logging
import os
import pty
import sys
import types
from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
from click.testing import CliRunner

from click_async_plugins import ITC, CliContext, cli_core
from click_async_plugins import debug as debug_module
from click_async_plugins.debug import (
    KeyAndFunc,
    _monitor_stdin,
    _name_for_coro,
    adjust_loglevel,
    debug,
    debug_info,
    echo_newline,
    getch,
    monitor_stdin_for_debug_commands,
    print_help,
    puts,
    terminal_block,
)


@pytest.fixture
def clictx() -> CliContext:
    return CliContext(itc=ITC())


@pytest.fixture
def restore_root_loglevel() -> Iterator[None]:
    root = logging.getLogger()
    level = root.level
    yield
    root.setLevel(level)


def test_puts_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    puts("hello")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "hello\n"


def test_echo_newline(clictx: CliContext) -> None:
    assert echo_newline(clictx) == ""


def test_terminal_block(clictx: CliContext) -> None:
    block = terminal_block(clictx)
    assert block.startswith("\n" * 8 + "The time is now: ")
    assert block.endswith("\n")


@pytest.mark.asyncio
async def test_name_for_coro() -> None:
    async def some_coroutine() -> None: ...

    coro = some_coroutine()
    try:
        assert _name_for_coro(coro).endswith("some_coroutine")
    finally:
        coro.close()


def test_name_for_coro_none() -> None:
    assert _name_for_coro(None) == "None"


def test_name_for_coro_unknown() -> None:
    assert _name_for_coro(object()) == "(unknown)"  # type: ignore[arg-type]


def test_name_for_coro_falls_back_to_name() -> None:
    # an object with __name__ but no __qualname__
    obj = types.SimpleNamespace(__name__="named")
    assert _name_for_coro(obj) == "named"


@pytest.mark.asyncio
async def test_debug_info(clictx: CliContext) -> None:
    async def sleeper() -> None:
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(sleeper())
    task.set_name("sleeper-task")
    await asyncio.sleep(0)
    try:
        info = debug_info(clictx)
    finally:
        task.cancel()

    lines = info.splitlines()
    assert lines[0] == "*** BEGIN DEBUG INFO: ***"
    assert lines[-1] == "*** END DEBUG INFO: ***"
    assert "Tasks:" in lines
    assert any("sleeper-task" in line and "sleeper" in line for line in lines)
    assert "CliContext:" in lines
    assert f"  itc = {clictx.itc!r}" in info


@pytest.mark.parametrize(
    ("start", "change", "expected"),
    [
        (logging.INFO, -10, "Log level now at DEBUG"),
        (logging.INFO, 10, "Log level now at WARN"),
        (logging.ERROR, 10, "Log level now at CRITICAL"),
        (logging.DEBUG, -10, None),
        (logging.CRITICAL, 10, None),
    ],
)
def test_adjust_loglevel(
    clictx: CliContext,
    restore_root_loglevel: None,
    start: int,
    change: int,
    expected: str | None,
) -> None:
    root = logging.getLogger()
    root.setLevel(start)
    assert adjust_loglevel(clictx, change) == expected
    assert root.level == (start if expected is None else start + change)


def test_print_help(clictx: CliContext) -> None:
    def hello(_: CliContext) -> str:
        """Say hello"""
        return "hello"

    text = print_help(clictx, {0x68: KeyAndFunc("h", hello)})
    assert text == (
        "Keys I know about for debugging:\n"
        "  h     Say hello\n"
        "  ?     Print this message"
    )


async def _chars(*chars: str) -> AsyncGenerator[str]:
    for ch in chars:
        yield ch


@pytest.mark.asyncio
async def test_monitor_stdin_dispatches_keys(
    clictx: CliContext,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(debug_module, "getch", lambda: _chars("a", "", "?", "q", "z"))
    output: list[str] = []
    calls: list[CliContext] = []

    def key_a(ctx: CliContext) -> str:
        """Key a"""
        calls.append(ctx)
        return "pressed a"

    def key_q(ctx: CliContext) -> None:
        """Key q, which has no output"""
        calls.append(ctx)

    key_to_cmd = {0x61: KeyAndFunc("a", key_a), 0x71: KeyAndFunc("q", key_q)}
    await _monitor_stdin(clictx, key_to_cmd, puts=output.append)

    assert calls == [clictx, clictx]
    assert output[0] == "pressed a"
    assert output[1].startswith("Keys I know about for debugging:")
    assert len(output) == 2
    assert "Ignoring character 0x7a on stdin" in caplog.text


@pytest.mark.asyncio
async def test_monitor_stdin_handles_unsupported_platform(
    clictx: CliContext,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unsupported() -> AsyncGenerator[str]:
        if True:
            raise NotImplementedError
        yield ""  # type: ignore[unreachable]  # pragma: no cover

    monkeypatch.setattr(debug_module, "getch", unsupported)
    await _monitor_stdin(clictx, {}, puts=lambda _: None)
    assert "does not work on this platform" in caplog.text


@pytest.mark.asyncio
async def test_monitor_stdin_for_debug_commands_default_keys(
    clictx: CliContext, monkeypatch: pytest.MonkeyPatch, restore_root_loglevel: None
) -> None:
    logging.getLogger().setLevel(logging.INFO)
    monkeypatch.setattr(
        debug_module, "getch", lambda: _chars("\n", "\r", "\x1b", "\x04", "+", "-")
    )
    output: list[str] = []

    async with monitor_stdin_for_debug_commands(clictx, puts=output.append) as task:
        assert task is not None
        await task

    assert output[0] == output[1] == ""
    assert output[2].startswith("\n" * 8 + "The time is now: ")
    assert output[3].startswith("*** BEGIN DEBUG INFO: ***")
    assert output[4] == "Log level now at DEBUG"
    assert output[5] == "Log level now at INFO"


@pytest.mark.asyncio
async def test_monitor_stdin_for_debug_commands_custom_keys(
    clictx: CliContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(debug_module, "getch", lambda: _chars("x", "\n"))
    output: list[str] = []

    def custom(_: CliContext) -> str:
        """Custom"""
        return "custom!"

    async with monitor_stdin_for_debug_commands(
        clictx,
        key_to_cmd={0x78: KeyAndFunc("x", custom), 0xA: KeyAndFunc("n", custom)},
        puts=output.append,
    ) as task:
        assert task is not None
        await task

    assert output == ["custom!", "custom!"]


def test_debug_plugin_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(debug_module, "getch", lambda: _chars("?"))
    monkeypatch.setitem(cli_core.commands, "debug", debug)

    result = CliRunner().invoke(cli_core, ["debug"], standalone_mode=False)

    assert result.exception is None
    assert "Keys I know about for debugging:" in result.output
    assert "Increase the logging level" in result.output


@pytest.fixture
def fake_tty(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """Replace sys.stdin by the slave end of a pty; yield the master fd"""
    try:
        master, slave = pty.openpty()
    except OSError as exc:  # pragma: no cover
        pytest.skip(f"no pty available: {exc}")
    stdin = os.fdopen(slave, "r")
    monkeypatch.setattr(sys, "stdin", stdin)
    yield master
    stdin.close()
    os.close(master)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX terminal")
async def test_getch_reads_from_terminal_and_restores_it(
    fake_tty: int, caplog: pytest.LogCaptureFixture
) -> None:
    import fcntl
    import termios

    caplog.set_level(logging.DEBUG)
    fd = sys.stdin.fileno()
    attrs_before = termios.tcgetattr(fd)
    flags_before = fcntl.fcntl(fd, fcntl.F_GETFL)

    gen = getch()
    # nothing typed yet: non-blocking read gives an empty string
    assert await anext(gen) == ""
    assert "Configuring stdin for raw input" in caplog.text
    assert termios.tcgetattr(fd) != attrs_before
    assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK

    os.write(fake_tty, b"ab")
    assert [await anext(gen), await anext(gen)] == ["a", "b"]

    # cancellation stops reading and restores the terminal; as there is no
    # msvcrt on POSIX, the generator then reports the platform as unsupported
    with pytest.raises(NotImplementedError):
        await gen.athrow(asyncio.CancelledError)

    assert "Restoring stdin" in caplog.text
    assert termios.tcgetattr(fd) == attrs_before
    assert fcntl.fcntl(fd, fcntl.F_GETFL) == flags_before


@pytest.mark.asyncio
async def test_getch_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    # a None entry in sys.modules makes the import raise ImportError
    for module in ("fcntl", "termios", "tty", "msvcrt"):
        monkeypatch.setitem(sys.modules, module, None)

    with pytest.raises(NotImplementedError):
        await anext(getch())


@pytest.mark.asyncio
async def test_getch_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fcntl", None)  # force the Windows branch

    presses = iter([True, False, True])
    keys = iter(["a", "b"])
    fake_msvcrt: Any = types.ModuleType("msvcrt")
    fake_msvcrt.kbhit = lambda: next(presses)
    fake_msvcrt.getch = lambda: next(keys)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    gen = getch()
    assert await asyncio.wait_for(anext(gen), 2) == "a"
    # the second poll finds no key, sleeps, and then sees the next one
    assert await asyncio.wait_for(anext(gen), 2) == "b"
    await gen.aclose()
