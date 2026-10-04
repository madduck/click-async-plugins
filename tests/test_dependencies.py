from collections.abc import AsyncGenerator
from functools import partial

import click
import pytest
from click.testing import CliRunner

from click_async_plugins import (
    PluginLifespan,
    depends_on,
    plugin,
    plugin_group,
    resolve_dependencies,
    runner,
)
from click_async_plugins.typedefs import PluginTask

EVENTS: list[str] = []


@pytest.fixture(autouse=True)
def clear_events() -> None:
    EVENTS.clear()


async def lifespan(name: str) -> AsyncGenerator[PluginTask]:
    async def task() -> None:
        EVENTS.append(f"run {name}")

    EVENTS.append(f"setup {name}")
    yield task()
    EVENTS.append(f"teardown {name}")


@plugin
async def base() -> PluginLifespan:
    async for task in lifespan("base"):
        yield task


@plugin
@depends_on("base")
async def mid() -> PluginLifespan:
    async for task in lifespan("mid"):
        yield task


@plugin
@depends_on(base, "mid")
async def top() -> PluginLifespan:
    async for task in lifespan("top"):
        yield task


@plugin
async def other() -> PluginLifespan:
    async for task in lifespan("other"):
        yield task


@plugin
@depends_on("loop-b")
async def loop_a() -> PluginLifespan:
    async for task in lifespan("loop_a"):
        yield task


@plugin
@depends_on("loop-a")
async def loop_b() -> PluginLifespan:
    async for task in lifespan("loop_b"):
        yield task


def make_cli(*extra: click.Command) -> click.Group:
    @plugin_group
    def cli() -> None:
        pass

    cli.result_callback()(runner)
    for command in (base, mid, top, other, loop_a, loop_b, *extra):
        cli.add_command(command)
    return cli


def invoke(args: list[str]) -> tuple[list[str], click.testing.Result]:
    result = CliRunner().invoke(make_cli(), args)
    return EVENTS, result


def setups(events: list[str]) -> list[str]:
    return [e.split()[1] for e in events if e.startswith("setup")]


def teardowns(events: list[str]) -> list[str]:
    return [e.split()[1] for e in events if e.startswith("teardown")]


def test_satisfied_in_order() -> None:
    events, result = invoke(["base", "mid", "top"])
    assert result.exit_code == 0, result.output
    assert setups(events) == ["base", "mid", "top"]


def test_dependencies_are_set_up_first_regardless_of_command_line_order() -> None:
    events, result = invoke(["top", "mid", "base"])
    assert result.exit_code == 0, result.output
    assert setups(events) == ["base", "mid", "top"]


def test_teardown_is_reverse_of_setup() -> None:
    events, result = invoke(["top", "mid", "base"])
    assert result.exit_code == 0, result.output
    assert teardowns(events) == ["top", "mid", "base"]


def test_unrelated_plugins_keep_command_line_order() -> None:
    events, result = invoke(["other", "top", "base", "mid"])
    assert result.exit_code == 0, result.output
    assert setups(events) == ["other", "base", "mid", "top"]


def test_plugins_without_dependencies_are_unaffected() -> None:
    events, result = invoke(["other", "base"])
    assert result.exit_code == 0, result.output
    assert setups(events) == ["other", "base"]


def test_missing_dependency_is_a_usage_error() -> None:
    events, result = invoke(["mid"])
    assert result.exit_code == 2
    assert "Plugin 'mid' requires plugin 'base', which was not specified." in (
        result.output
    )
    assert events == [], "nothing should be set up if dependencies are not met"


def test_circular_dependency_is_a_usage_error() -> None:
    _, result = invoke(["loop-a", "loop-b"])
    assert result.exit_code == 2
    assert "Circular plugin dependency: loop-a → loop-b → loop-a" in result.output


def test_dependency_satisfied_by_every_instance_of_a_plugin() -> None:
    events, result = invoke(["mid", "base", "base"])
    assert result.exit_code == 0, result.output
    assert setups(events) == ["base", "base", "mid"]


def test_depends_on_requires_an_argument() -> None:
    with pytest.raises(ValueError):
        depends_on()


def test_depends_on_accumulates_when_stacked() -> None:
    @plugin
    @depends_on("a")
    @depends_on("b")
    async def stacked() -> PluginLifespan:
        yield None

    assert stacked.callback is not None
    assert stacked.callback.__plugin_requires__ == ("b", "a")  # type: ignore[attr-defined]


def test_depends_on_works_above_plugin() -> None:
    @depends_on("base")
    @plugin
    async def above() -> PluginLifespan:
        async for task in lifespan("above"):
            yield task

    result = CliRunner().invoke(make_cli(above), ["above"])
    assert result.exit_code == 2
    assert "Plugin 'above' requires plugin 'base'" in result.output


def test_resolve_dependencies_looks_through_partials() -> None:
    ctx = click.Context(make_cli())
    f_mid = mid.invoke(ctx)
    f_base = base.invoke(ctx)
    assert f_mid is not None and f_base is not None

    resolved = resolve_dependencies([partial(f_mid), partial(f_base)])
    assert [r.func for r in resolved] == [f_base, f_mid]  # type: ignore[attr-defined]


def test_resolve_dependencies_accepts_plain_factories() -> None:
    def plain() -> None:
        pass

    assert resolve_dependencies([plain]) == [plain]  # type: ignore[comparison-overlap,list-item]


def test_depends_on_command_without_callback_is_a_type_error() -> None:
    command = click.Command("nocallback")
    assert command.callback is None
    with pytest.raises(TypeError, match="has no callback"):
        depends_on("base")(command)


def test_depends_on_nameless_command_is_a_value_error() -> None:
    nameless = click.Command(None)
    with pytest.raises(ValueError, match="has no name"):
        depends_on(nameless)
