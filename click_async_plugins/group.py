from collections.abc import Callable
from typing import cast

import click

from .command import PluginCommand
from .typedefs import PluginLifespan


class PluginGroup(click.Group):
    def plugin_command(self, fn: Callable[..., PluginLifespan]) -> PluginCommand:
        return cast(PluginCommand, self.command(cls=PluginCommand)(fn))


def plugin_group(fn: Callable[..., None]) -> PluginGroup:
    return click.group(cls=PluginGroup, chain=True)(fn)
