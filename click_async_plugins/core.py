import asyncio

import click

from .group import plugin_group
from .itc import ITC
from .typedefs import PluginFactory
from .util import run_plugins


@plugin_group
@click.pass_context
def cli_core(ctx: click.Context) -> None:
    ctx.ensure_object(ITC)


@cli_core.result_callback()
def runner(plugin_factories: list[PluginFactory]) -> None:
    asyncio.run(run_plugins(plugin_factories))
