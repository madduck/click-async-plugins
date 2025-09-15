from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import partial, wraps
from typing import Any

import click

from .typedefs import PluginFactory, PluginLifespan


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


def plugin(fn: Callable[..., PluginLifespan]) -> PluginCommand:
    return click.command(cls=PluginCommand)(fn)
