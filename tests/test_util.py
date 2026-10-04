import asyncio
import importlib.util
import inspect
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from typing import Any

import pytest

import click_async_plugins.util as util
from click_async_plugins import (
    ITC,
    CliContext,
    PluginLifespan,
    create_plugin_task,
    react_to_data_update,
    run_plugins,
    run_tasks,
    setup_plugins,
)
from click_async_plugins.util import TaskWithName, _get_name, sleep_forever


def test_cli_context_holds_itc() -> None:
    itc = ITC()
    assert CliContext(itc=itc).itc is itc


def test_iscoroutine_falls_back_to_asyncio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the legacy-import fallback without disturbing the real module"""
    monkeypatch.delattr(inspect, "iscoroutine")
    name = "click_async_plugins._util_fallback"
    assert util.__file__ is not None
    spec = importlib.util.spec_from_file_location(name, util.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    assert module.iscoroutine is asyncio.iscoroutine


@pytest.mark.asyncio
async def test_sleep_forever_returns_when_not_forever() -> None:
    assert await sleep_forever(0, forever=False) is None


@pytest.mark.asyncio
async def test_sleep_forever_runs_until_cancelled() -> None:
    task = asyncio.ensure_future(sleep_forever(0.001))
    await asyncio.sleep(0.01)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_get_name_of_function() -> None:
    def plugin_fn() -> None: ...

    assert _get_name(plugin_fn) == "plugin_fn"  # type: ignore[arg-type]


def test_get_name_of_partial() -> None:
    def plugin_fn(a: int) -> None: ...

    assert _get_name(partial(plugin_fn, 1)) == "plugin_fn"  # type: ignore[arg-type]


def test_get_name_without_name_attribute() -> None:
    class Nameless:
        def __repr__(self) -> str:
            return "<nameless>"

    assert _get_name(Nameless()) == "<nameless>"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_create_plugin_task_runs_coroutine() -> None:
    ran = []

    async def work() -> None:
        ran.append(True)

    task: asyncio.Task[None] = create_plugin_task(
        TaskWithName(task=work(), name="work")
    )
    assert task.get_name() == "work"
    await task
    assert ran == [True]


@pytest.mark.asyncio
async def test_create_plugin_task_without_coroutine_waits_until_cancelled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    task: asyncio.Task[None] = create_plugin_task(TaskWithName(task=None, name="idle"))
    assert task.get_name() == "idle"
    await asyncio.sleep(0.01)
    assert not task.done()
    assert "Waiting until programme termination for 'idle'" in caplog.text

    task.cancel()
    await task  # cancellation is swallowed by the wrapper
    assert not task.cancelled()
    assert "Task for 'idle' cancelled" in caplog.text


@pytest.mark.asyncio
async def test_create_plugin_task_swallows_cancellation_of_coroutine() -> None:
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.sleep(3600)

    task: asyncio.Task[None] = create_plugin_task(
        TaskWithName(task=work(), name="work")
    )
    await started.wait()
    task.cancel()
    await task
    assert not task.cancelled()


@pytest.mark.asyncio
async def test_create_plugin_task_uses_custom_task_factory() -> None:
    created: list[str | None] = []

    def factory(coro: Any, name: str | None = None) -> asyncio.Task[None]:
        created.append(name)
        return asyncio.ensure_future(coro)

    async def work() -> None: ...

    task = create_plugin_task(
        TaskWithName(task=work(), name="custom"), create_task_fn=factory
    )
    await task
    assert created == ["custom"]


@pytest.mark.asyncio
async def test_setup_plugins_enters_contexts_and_collects_tasks() -> None:
    events: list[str] = []

    def make(name: str) -> Any:
        @asynccontextmanager
        async def lifespan(*args: Any, **kwargs: Any) -> PluginLifespan:
            events.append(f"enter {name} {args} {kwargs}")
            yield None
            events.append(f"exit {name}")

        lifespan.__name__ = name
        return lifespan

    async with AsyncExitStack() as stack:
        tasks = await setup_plugins(
            [make("one"), make("two")], 1, stack=stack, key="value"
        )
        assert [t.name for t in tasks] == ["one", "two"]
        assert all(t.task is None for t in tasks)
        assert events == [
            "enter one (1,) {'key': 'value'}",
            "enter two (1,) {'key': 'value'}",
        ]

    assert events[2:] == ["exit two", "exit one"]


@pytest.mark.asyncio
async def test_setup_plugins_empty() -> None:
    async with AsyncExitStack() as stack:
        assert await setup_plugins([], stack=stack) == []


@pytest.mark.asyncio
async def test_run_tasks_runs_all_tasks(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    ran: list[str] = []

    async def work(name: str) -> None:
        ran.append(name)

    await run_tasks(
        [
            TaskWithName(task=work("a"), name="a"),
            TaskWithName(task=work("b"), name="b"),
        ]
    )
    assert sorted(ran) == ["a", "b"]
    assert "Terminating…" in caplog.text


@pytest.mark.asyncio
async def test_run_tasks_with_no_tasks() -> None:
    await run_tasks([])


@pytest.mark.asyncio
async def test_run_tasks_swallows_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    runner = asyncio.ensure_future(run_tasks([TaskWithName(task=None, name="idle")]))
    await asyncio.sleep(0.01)
    assert not runner.done()

    runner.cancel()
    await runner
    assert "Terminating…" in caplog.text


@pytest.mark.asyncio
async def test_run_tasks_propagates_failures() -> None:
    async def fail() -> None:
        raise ValueError("boom")

    with pytest.raises(ExceptionGroup) as excinfo:
        await run_tasks([TaskWithName(task=fail(), name="fail")])
    assert excinfo.group_contains(ValueError, match="boom")


@pytest.mark.asyncio
async def test_run_plugins_runs_and_tears_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(*args: Any, **kwargs: Any) -> PluginLifespan:
        async def work() -> None:
            events.append(f"work {args} {kwargs}")

        events.append("setup")
        yield work()
        events.append("teardown")

    await run_plugins([lifespan], "arg", kw="kwarg")
    assert events == ["setup", "work ('arg',) {'kw': 'kwarg'}", "teardown"]
    assert "Finished." in caplog.text


@pytest.mark.asyncio
async def test_react_to_data_update_calls_back_for_non_none_values() -> None:
    async def updates() -> AsyncGenerator[int | None]:
        for value in (1, None, 2):
            yield value

    seen: list[int] = []

    async def callback(value: int | None) -> None:
        assert value is not None
        seen.append(value)

    await react_to_data_update(updates(), callback=callback)
    assert seen == [1, 2]


@pytest.mark.asyncio
async def test_react_to_data_update_swallows_cancellation() -> None:
    async def updates() -> AsyncGenerator[int]:
        yield 1
        raise asyncio.CancelledError

    seen: list[int] = []

    async def callback(value: int | None) -> None:
        assert value is not None
        seen.append(value)

    await react_to_data_update(updates(), callback=callback)
    assert seen == [1]


@pytest.mark.asyncio
async def test_react_to_data_update_with_itc() -> None:
    itc = ITC()
    seen: list[int] = []

    async def callback(value: Any) -> None:
        seen.append(value)
        if value == 2:
            raise asyncio.CancelledError

    reactor = asyncio.ensure_future(
        react_to_data_update(
            itc.updates("key", yield_immediately=False), callback=callback
        )
    )
    for value in (1, 2):
        await asyncio.sleep(0.01)
        itc.set("key", value)
    await asyncio.wait_for(reactor, 1)
    assert seen == [1, 2]
