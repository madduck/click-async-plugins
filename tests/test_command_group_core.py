import asyncio
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

import click
import pytest
from click.testing import CliRunner

from click_async_plugins import (
    ITC,
    CliContext,
    PluginFactory,
    PluginLifespan,
    cli_core,
    pass_clictx,
    plugin,
    plugin_group,
    runner,
)
from click_async_plugins.command import PluginCommand
from click_async_plugins.group import PluginGroup


def _make_group() -> PluginGroup:
    @plugin_group
    def group() -> None: ...

    @group.result_callback()
    def collect(factories: list[PluginFactory]) -> list[PluginFactory]:
        return factories

    return group


def _invoke(group: click.Group, args: list[str], **kwargs: Any) -> Any:
    result = CliRunner().invoke(group, args, standalone_mode=False, **kwargs)
    if result.exception:
        raise result.exception
    return result.return_value


def test_plugin_group_is_chained_plugin_group() -> None:
    group = _make_group()
    assert isinstance(group, PluginGroup)
    assert group.chain


def test_plugin_decorator_returns_plugin_command() -> None:
    @plugin
    async def myplugin() -> PluginLifespan:
        yield None

    assert isinstance(myplugin, PluginCommand)
    assert myplugin.name == "myplugin"


def test_plugin_command_registers_on_group() -> None:
    group = _make_group()

    @group.plugin_command
    async def myplugin() -> PluginLifespan:
        yield None

    assert isinstance(myplugin, PluginCommand)
    assert group.commands["myplugin"] is myplugin


def test_plugin_command_without_callback_invokes_to_none() -> None:
    command = PluginCommand("noop")
    with click.Context(command) as ctx:
        assert command.invoke(ctx) is None


@pytest.mark.asyncio
async def test_plugin_command_invoke_returns_lifespan_factory() -> None:
    group = _make_group()
    events: list[tuple[str, int]] = []

    @group.plugin_command
    @click.option("--count", type=int, default=1)
    async def counter(count: int) -> PluginLifespan:
        async def work() -> None:
            events.append(("work", count))

        events.append(("setup", count))
        yield work()
        events.append(("teardown", count))

    (factory,) = _invoke(group, ["counter", "--count", "3"])
    assert factory.__name__ == "counter"
    assert events == []  # nothing runs until the factory is entered

    async with factory() as task:
        assert task is not None
        await task

    assert events == [("setup", 3), ("work", 3), ("teardown", 3)]


def test_chained_plugin_commands_yield_one_factory_each() -> None:
    group = _make_group()

    @group.plugin_command
    async def first() -> PluginLifespan:
        yield None

    @group.plugin_command
    async def second() -> PluginLifespan:
        yield None

    factories = _invoke(group, ["first", "second"])
    assert [f.__name__ for f in factories] == ["first", "second"]


@pytest.fixture
def registered_commands() -> Iterator[list[str]]:
    """Track commands added to the global cli_core and remove them afterwards"""
    before = set(cli_core.commands)
    names: list[str] = []
    yield names
    for name in set(cli_core.commands) - before:
        del cli_core.commands[name]


def test_cli_core_provides_cli_context_and_runs_plugins(
    registered_commands: list[str],
) -> None:
    events: list[Any] = []

    @cli_core.plugin_command
    @pass_clictx
    async def spy(clictx: CliContext) -> PluginLifespan:
        async def work() -> None:
            events.append(("work", clictx.itc))

        events.append(("setup", clictx))
        yield work()
        events.append(("teardown", clictx))

    result = CliRunner().invoke(cli_core, ["spy"], standalone_mode=False)
    assert result.exception is None

    assert [e[0] for e in events] == ["setup", "work", "teardown"]
    clictx = events[0][1]
    assert isinstance(clictx, CliContext)
    assert isinstance(clictx.itc, ITC)
    assert events[1][1] is clictx.itc


def test_cli_core_chains_plugins_sharing_itc(
    registered_commands: list[str],
) -> None:
    received: list[int] = []

    @cli_core.plugin_command
    @pass_clictx
    async def producer(clictx: CliContext) -> PluginLifespan:
        async def work() -> None:
            await asyncio.sleep(0.01)
            clictx.itc.set("answer", 42)

        yield work()

    @cli_core.plugin_command
    @pass_clictx
    async def consumer(clictx: CliContext) -> PluginLifespan:
        async def work() -> None:
            async for value in clictx.itc.updates("answer", yield_immediately=False):
                assert value is not None
                received.append(value)
                return

        yield work()

    result = CliRunner().invoke(
        cli_core, ["consumer", "producer"], standalone_mode=False
    )
    assert result.exception is None
    assert received == [42]


def test_runner_runs_plugin_factories() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan() -> PluginLifespan:
        async def work() -> None:
            events.append("work")

        yield work()
        events.append("teardown")

    runner([lifespan])
    assert events == ["work", "teardown"]
