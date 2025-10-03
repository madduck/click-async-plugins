import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial, update_wrapper
from typing import Any, Never

import click

from .itc import ITC
from .typedefs import PluginFactory, PluginTask

logger = logging.getLogger(__name__)


@dataclass
class CliContext:
    itc: ITC


pass_clictx = click.make_pass_decorator(CliContext)


@dataclass
class TaskWithName:
    task: PluginTask | None
    name: str


async def sleep_forever(sleep: float = 1, *, forever: bool = True) -> Never | None:
    while await asyncio.sleep(sleep, forever):
        pass
    return None


def create_plugin_task[T](
    task: TaskWithName,
    *,
    create_task_fn: Callable[..., asyncio.Task[T]] = asyncio.create_task,
) -> asyncio.Task[T]:
    async def task_wrapper() -> None:
        try:
            if asyncio.iscoroutine(task.task):
                logger.debug(f"Scheduling task for '{task.name}'")
                await task.task

            else:
                logger.debug(f"Waiting until programme termination for '{task.name}'")
                await sleep_forever(3600)

        except asyncio.CancelledError:
            logger.debug(f"Task for '{task.name}' cancelled")

    if task.task is not None:
        task_wrapper = update_wrapper(task_wrapper, task.task)  # type: ignore[arg-type]
    else:
        task_wrapper = update_wrapper(task_wrapper, sleep_forever)

    return create_task_fn(task_wrapper(), name=task.name)


def _get_name(plugin_factory: PluginFactory) -> str:
    # if plugin_factory is e.g. a functools.partial instance, get at the actual
    # function, but if not, then set func to the factory
    func = getattr(plugin_factory, "func", None) or plugin_factory

    if (nameattr := getattr(func, "__name__", None)) is not None:
        return str(nameattr)

    return str(func)


async def setup_plugins(
    plugin_factories: list[PluginFactory],
    *args: Any,
    stack: AsyncExitStack,
    **kwargs: Any,
) -> list[TaskWithName]:
    tasks: list[TaskWithName] = []
    for plugin_factory in plugin_factories:
        plugin_fn = plugin_factory(*args, **kwargs)
        name = _get_name(plugin_factory)
        logger.debug(f"Setting up task for '{name}'")
        task = await stack.enter_async_context(plugin_fn)
        tasks.append(TaskWithName(task=task, name=name))

    return tasks


async def run_tasks(tasks: list[TaskWithName]) -> None:
    try:
        async with asyncio.TaskGroup() as tg:
            plugin_task = partial(create_plugin_task, create_task_fn=tg.create_task)
            for task in tasks:
                plugin_task(task)

    except* asyncio.CancelledError:
        pass

    finally:
        logger.debug("Terminating…")


async def run_plugins(
    plugin_factories: list[PluginFactory], *args: Any, **kwargs: Any
) -> None:
    async with AsyncExitStack() as stack:
        tasks = await setup_plugins(plugin_factories, *args, stack=stack, **kwargs)
        await run_tasks(tasks)

    logger.debug("Finished.")


async def react_to_data_update[T](
    updates_gen: AsyncGenerator[T],
    *,
    callback: Callable[[T], Coroutine[None, None, None]],
) -> None:
    try:
        async for update in updates_gen:
            if update is not None:
                await callback(update)

    except asyncio.CancelledError:
        pass
