import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Any

import click

logger = logging.getLogger(__name__)


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
