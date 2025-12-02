import asyncio
import logging
import time
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

    def get(self, key: str, default: Any = None) -> Any:
        return self._objects.get(key, default)

    def fire(self, key: str) -> None:
        logger.debug(f"Notifying subscribers of update to '{key}'…")
        for event in self._events.get(key) or []:
            event.set()

    async def updates(
        self,
        key: str,
        *,
        yield_immediately: bool = True,
        timeout: float | None = None,
        at_most_every: float | None = None,
        yield_for_no_value: Any | None = None,
    ) -> AsyncGenerator[Any | None]:
        at_most_every = 0 if at_most_every is None else at_most_every

        if timeout is not None and timeout <= 0:
            logger.warning("Updates timeout <= 0, disabling timeout…")
            timeout = None

        if timeout is not None and timeout < at_most_every:
            logger.warning("timeout < at_most_every makes no sense, adjusting…")
            timeout = at_most_every

        event = asyncio.Event()
        self._events[key].append(event)

        if yield_immediately:
            yield self._objects.get(key, yield_for_no_value)

        try:
            timestamp = 0.0
            while True:
                logger.debug(f"Waiting for update to '{key}'…")
                try:
                    async with asyncio.timeout(timeout):
                        await event.wait()
                except TimeoutError:
                    logger.debug(f"Timeout after {timeout}s")

                if (waitremain := timestamp + at_most_every - time.monotonic()) > 0:
                    logger.debug(f"Too early, sleeping for {waitremain:.02f}s")
                    await asyncio.sleep(waitremain)

                yield self._objects.get(key, yield_for_no_value)
                event.clear()
                timestamp = time.monotonic()

        finally:
            self._events[key].remove(event)

    def has_subscribers(self, key: str) -> bool:
        return len(self._events.get(key, [])) > 0

    def knows_about(self, key: str) -> bool:
        return key in self._objects
