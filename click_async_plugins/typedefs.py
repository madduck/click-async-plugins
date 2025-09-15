from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import AsyncContextManager

type PluginTask = Coroutine[None, None, None]
type PluginLifespan = AsyncGenerator[PluginTask | None]
type Plugin = AsyncContextManager[PluginTask | None]
type PluginFactory = Callable[..., Plugin]
