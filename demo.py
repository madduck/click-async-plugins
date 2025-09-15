import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial, wraps
from typing import Any, AsyncContextManager, Never, cast

import click

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()
logging.getLogger("asyncio").setLevel(logging.WARNING)

### Command and Group classes and types

type PluginTask = Coroutine[None, None, None]
type PluginLifespan = AsyncGenerator[PluginTask | None]
type Plugin = AsyncContextManager[PluginTask | None]
type PluginFactory = Callable[..., Plugin]


class PluginCommand(click.Command):
    def invoke(self, ctx: click.Context) -> PluginFactory | None:
        if (callback := self.callback) is None:
            return None

        @wraps(callback)
        def wrapper(*args: list[Any], **kwargs: dict[str, Any]) -> PluginFactory:
            lifespan_manager = asynccontextmanager(partial(callback, *args, **kwargs))
            lifespan_manager.__name__ = callback.__name__
            return lifespan_manager

        return ctx.invoke(wrapper, **ctx.params)


class PluginGroup(click.Group):
    def plugin_command(self, fn: Callable[..., PluginLifespan]) -> PluginCommand:
        return cast(PluginCommand, self.command(cls=PluginCommand)(fn))


def plugin_group(fn):
    return click.group(cls=PluginGroup, chain=True)(fn)


### Inter-task communication handler


class ITC:
    def __init__(self) -> None:
        self._events: dict[str, list[asyncio.Event]] = defaultdict(list[asyncio.Event])
        self._objects: dict[str, Any] = {}

    def set(self, key: str, obj: Any) -> None:
        self._objects[key] = obj
        logger.debug(f"Notifying subscribers of update to '{key}'…")
        for event in self._events.get(key) or []:
            event.set()

    async def updates(
        self, key: str, *, yield_immediately: bool = True
    ) -> AsyncGenerator[Any | None]:
        event = asyncio.Event()
        self._events[key].append(event)

        if yield_immediately:
            yield self._objects.get(key)

        try:
            while True:
                logger.debug(f"Waiting for update to '{key}'…")
                await event.wait()
                yield self._objects.get(key)
                event.clear()

        finally:
            self._events[key].remove(event)


pass_itc = click.make_pass_decorator(ITC)


### Core setup and scheduler


@plugin_group
@click.pass_context
def demo(ctx: click.Context) -> None:
    ctx.ensure_object(ITC)


async def sleep_forever(sleep: float = 1, *, forever: bool = True) -> Never | None:
    while await asyncio.sleep(sleep, forever):
        pass
    return None


def create_plugin_task[T](
    task: PluginTask | None,
    *,
    name: str | None = None,
    create_task_fn: Callable[..., asyncio.Task[T]] = asyncio.create_task,
) -> asyncio.Task[T]:
    async def task_wrapper() -> None:
        try:
            if asyncio.iscoroutine(task):
                logger.debug(f"Scheduling task for '{name}'")
                await task

            else:
                logger.debug(f"Waiting until programme termination for '{name}'")
                await sleep_forever(3600)

        except asyncio.CancelledError:
            logger.debug(f"Task for '{name}' cancelled")

    return create_task_fn(task_wrapper(), name=name)


async def setup_plugins(
    plugin_factories: list[PluginFactory], *, stack: AsyncExitStack
) -> dict[str, PluginTask | None]:
    tasks: dict[str, PluginTask | None] = {}
    for plugin_factory in plugin_factories:
        plugin_fn = plugin_factory()
        name = plugin_factory.__name__
        logger.debug(f"Setting up task for '{name}'")
        task = await stack.enter_async_context(plugin_fn)
        tasks[name] = task

    return tasks


async def run_tasks(tasks: dict[str, PluginTask | None]) -> None:
    try:
        async with asyncio.TaskGroup() as tg:
            plugin_task = partial(create_plugin_task, create_task_fn=tg.create_task)
            for name, task in tasks.items():
                plugin_task(task, name=name)

    except* asyncio.CancelledError:
        pass

    finally:
        logger.debug("Terminating…")


async def run_plugins(plugin_factories: list[PluginFactory]) -> None:
    async with AsyncExitStack() as stack:
        tasks = await setup_plugins(plugin_factories, stack=stack)
        await run_tasks(tasks)

    logger.debug("Finished.")


@demo.result_callback()
def runner(plugin_factories: list[PluginFactory]) -> None:
    asyncio.run(run_plugins(plugin_factories))


### Finally, the plugins


@demo.plugin_command
@click.option(
    "--from",
    "-f",
    "start",
    type=click.IntRange(min=1),
    default=3,
    help="Count down from this number",
)
@click.option(
    "--sleep", "-s", type=float, default=1, help="Sleep this long between counts"
)
@pass_itc
async def countdown(itc: ITC, start: int = 3, sleep: float = 1) -> PluginLifespan:
    async def counter(start: int, sleep: float) -> None:
        cur = start
        while cur > 0:
            logger.info(f"Counting down… {cur}")
            itc.set("countdown", cur)
            cur = await asyncio.sleep(sleep, cur - 1)

        logger.info("Finished counting down")

    yield counter(start, sleep)

    logger.debug("Lifespan over for countdown")


@demo.plugin_command
@click.option(
    "--immediately",
    is_flag=True,
    help="Don't wait for first update but echo right upon start",
)
@pass_itc
async def echo(itc: ITC, immediately: bool) -> PluginLifespan:
    async def reactor() -> None:
        async for cur in itc.updates("countdown", yield_immediately=immediately):
            logger.info(f"Countdown currently at {cur}")

    yield reactor()

    logger.debug("Lifespan over for echo")


if __name__ == "__main__":
    import sys

    sys.exit(demo())
