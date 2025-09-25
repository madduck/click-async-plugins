import asyncio
import logging

import click

from click_async_plugins import (
    CliContext,
    PluginLifespan,
    cli_core,
    pass_clictx,
    plugin,
)
from click_async_plugins.debug import debug

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()
logging.getLogger("asyncio").setLevel(logging.WARNING)


@cli_core.plugin_command
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
@pass_clictx
async def countdown(
    clictx: CliContext, start: int = 3, sleep: float = 1
) -> PluginLifespan:
    async def counter(start: int, sleep: float) -> None:
        cur = start
        while cur > 0:
            logger.info(f"Counting down… {cur}")
            clictx.itc.set("countdown", cur)
            cur = await asyncio.sleep(sleep, cur - 1)

        logger.info("Finished counting down")

    yield counter(start, sleep)

    logger.debug("Lifespan over for countdown")


# For fun, let's add_command the second plugin, instead of using a decorator:
@plugin
@click.option(
    "--immediately",
    is_flag=True,
    help="Don't wait for first update but echo right upon start",
)
@pass_clictx
async def echo(clictx: CliContext, immediately: bool) -> PluginLifespan:
    async def reactor() -> None:
        async for cur in clictx.itc.updates("countdown", yield_immediately=immediately):
            logger.info(f"Countdown currently at {cur}")

    yield reactor()

    logger.debug("Lifespan over for echo")


cli_core.add_command(echo)
cli_core.add_command(debug)

if __name__ == "__main__":
    import sys

    sys.exit(cli_core())
