import asyncio

import click

from click_async_plugins.itc import ITC

from .group import plugin_group
from .typedefs import PluginFactory
from .util import CliContext, run_plugins


@plugin_group
@click.pass_context
def cli_core(ctx: click.Context) -> None:
    ctx.obj = CliContext(itc=ITC())


@cli_core.result_callback()
def runner(plugin_factories: list[PluginFactory]) -> None:
    asyncio.run(run_plugins(plugin_factories))
