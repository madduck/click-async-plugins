# needed < 3.14 so that annotations aren't evaluated
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any, Coroutine, cast

from click_async_plugins.util import CliContext

from . import PluginLifespan, pass_clictx, plugin

logger = logging.getLogger(__name__)


def puts(s: str) -> None:
    print(s, file=sys.stderr)


def echo_newline(_: CliContext) -> str:
    """Outputs a new line"""
    return ""


def terminal_block(_: CliContext) -> str:
    """Outputs a couple of newlines and the current time"""
    return f"{'\n' * 8}The time is now: {datetime.datetime.now().isoformat(sep=' ')}\n"


def _name_for_coro(coro: Coroutine[Any, Any, Any] | None) -> str:
    if coro is None:
        return str(None)

    for attr in ("__qualname__", "__name__"):
        if (ret := getattr(coro, attr, None)) is not None:
            return cast(str, ret)

    return "(unknown)"


def debug_info(clictx: CliContext) -> str:
    """Prints debugging information on tasks and CliContext"""
    ret = "*** BEGIN DEBUG INFO: ***\n"
    ret += "Tasks:\n"
    for i, task in enumerate(asyncio.all_tasks(asyncio.get_event_loop()), 1):
        coro = task.get_coro()
        ret += (
            f"  {i:02n}  {task.get_name():32s}  "
            f"state={task._state.lower():8s}  "
            f"coro={_name_for_coro(coro)}\n"
        )
    ret += "CliContext:\n"
    maxlen = max([len(k) for k in clictx.__dict__.keys()])
    for attr, value in clictx.__dict__.items():
        ret += f"  {attr:>{maxlen}s} = {value!r}\n"
    return ret + "*** END DEBUG INFO: ***"


_LOGLEVELS = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARN: "WARN",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


def adjust_loglevel(_: CliContext, change: int) -> str | None:
    """Adjusts the log level"""
    rootlogger = logging.getLogger()
    newlevel = rootlogger.getEffectiveLevel() + change
    if newlevel < logging.DEBUG or newlevel > logging.CRITICAL:
        return None

    rootlogger.setLevel(newlevel)
    return f"Log level now at {_LOGLEVELS[logger.getEffectiveLevel()]}"


@dataclass
class KeyAndFunc[ContextT: CliContext]:
    key: str
    func: Callable[[ContextT], str | None]


type KeyCmdMapType[ContextT: CliContext] = dict[int, KeyAndFunc[ContextT]]


def print_help[ContextT: CliContext](
    _: ContextT, key_to_cmd: KeyCmdMapType[ContextT]
) -> str:
    ret = "Keys I know about for debugging:\n"
    for keyfunc in key_to_cmd.values():
        ret += f"  {keyfunc.key:5s} {keyfunc.func.__doc__}\n"
    return ret + "  ?     Print this message"


try:
    import fcntl
    import termios
    import tty

    async def _monitor_stdin[ContextT: CliContext](  # pyright: ignore[reportRedeclaration]
        clictx: ContextT,
        key_to_cmd: KeyCmdMapType[ContextT],
        *,
        puts: Callable[[str], Any] = puts,
    ) -> None:
        fd = sys.stdin.fileno()
        termios_saved = termios.tcgetattr(fd)
        fnctl_flags = fcntl.fcntl(sys.stdin, fcntl.F_GETFL)

        try:
            logger.debug("Configuring stdin for raw input")
            tty.setcbreak(fd)
            fcntl.fcntl(sys.stdin, fcntl.F_SETFL, fnctl_flags | os.O_NONBLOCK)

            while True:
                ch = sys.stdin.read(1)

                if len(ch) == 0:
                    await asyncio.sleep(0.1)
                    continue

                if (key := ord(ch)) == 0x3F:
                    puts(print_help(clictx, key_to_cmd))

                elif (keyfunc := key_to_cmd.get(key)) is not None and callable(
                    keyfunc.func
                ):
                    if (ret := keyfunc.func(clictx)) is not None:
                        puts(ret)

                else:
                    logger.debug(f"Ignoring character 0x{key:02x} on stdin")

        finally:
            logger.debug("Restoring stdin")
            termios.tcsetattr(fd, termios.TCSADRAIN, termios_saved)
            fcntl.fcntl(sys.stdin, fcntl.F_SETFL, fnctl_flags)

except ImportError:

    async def _monitor_stdin[ContextT: CliContext](
        clictx: ContextT,
        key_to_cmd: KeyCmdMapType[ContextT],
        *,
        puts: Callable[[str], Any] = puts,
    ) -> None:
        _ = clictx, key_to_cmd, puts
        logger.warning("The 'debug' plugin does not work on this platform")
        return None


@asynccontextmanager
async def monitor_stdin_for_debug_commands[ContextT: CliContext](
    clictx: CliContext,
    *,
    key_to_cmd: KeyCmdMapType[ContextT] | None = None,
    puts: Callable[[str], Any] = puts,
) -> PluginLifespan:
    key_to_cmd = key_to_cmd or {}

    increase_loglevel = partial(adjust_loglevel, change=-10)
    increase_loglevel.__doc__ = "Increase the logging level"
    decrease_loglevel = partial(adjust_loglevel, change=10)
    decrease_loglevel.__doc__ = "Decrease the logging level"

    map = {
        0xA: KeyAndFunc(r"\n", echo_newline),
        0x1B: KeyAndFunc("<Esc>", terminal_block),
        0x4: KeyAndFunc("^D", debug_info),
        0x2B: KeyAndFunc("+", increase_loglevel),
        0x2D: KeyAndFunc("-", decrease_loglevel),
        **key_to_cmd,
    }
    yield _monitor_stdin(clictx, map, puts=puts)


@plugin
@pass_clictx
async def debug(clictx: CliContext) -> PluginLifespan:
    """Monitor stdin for keypresses to trigger debugging functions

    Press '?' to get a list of possible keys.
    """

    async with monitor_stdin_for_debug_commands(clictx) as task:
        yield task
