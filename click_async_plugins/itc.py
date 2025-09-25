import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Any

logger = logging.getLogger(__name__)


class ITC:
    def __init__(self) -> None:
        self._events: dict[str, list[asyncio.Event]] = defaultdict(list[asyncio.Event])
        self._objects: dict[str, Any] = {}

    def __repr__(self) -> str:
        ret = f"{self.__class__.__name__}("
        i = 0
        for i, (key, obj) in enumerate(self._objects.items()):
            ret = (
                f"{ret}{'\n' if i == 0 else ''}       "
                f"{key}={obj!r} "
                f"({len(self._events[key])} listeners)\n"
            )
        return f"{ret}{'    ' if i > 0 else ''})"

    def set(self, key: str, obj: Any) -> None:
        self._objects[key] = obj
        self.fire(key)

    def fire(self, key: str) -> None:
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

    def has_subscribers(self, key: str) -> bool:
        return len(self._events.get(key, [])) > 0

    def knows_about(self, key: str) -> bool:
        return key in self._objects
