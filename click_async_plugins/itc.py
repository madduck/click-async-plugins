import asyncio
import inspect
import logging
import time
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Any

logger = logging.getLogger(__name__)


def _caller(depth: int = 1) -> str:
    """Return a description of a calling frame, ``depth`` frames up.

    ``depth`` counts frames starting at this helper, so ``depth=1`` (the
    default) reports the direct caller of this helper, ``depth=2`` the frame
    above that, and so on. Returns a ``file:lineno in function`` string, or
    a fallback marker if the frame cannot be determined.

    Raises:
        ValueError: If ``depth`` is less than 1.
    """
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth}")

    frame = inspect.currentframe()
    try:
        for _ in range(depth):
            frame = frame.f_back if frame is not None else None
        if frame is None:
            return "<unknown>"
        return f"{frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}"
    finally:
        del frame


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
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"set('{key}', {obj!r}) called from {_caller(2)}")
        self._objects[key] = obj
        self.fire(key)

    def get(self, key: str, default: Any = None) -> Any:
        value = self._objects.get(key, default)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"get('{key}') -> {value!r} called from {_caller(2)}")
        return value

    def fire(self, key: str) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"fire('{key}') called from {_caller(2)}")
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

        caller = _caller(2) if logger.isEnabledFor(logging.DEBUG) else ""
        logger.debug(f"updates('{key}') subscribing from {caller}")

        if timeout is not None and timeout <= 0:
            logger.warning("Updates timeout <= 0, disabling timeout…")
            timeout = None

        if timeout is not None and timeout < at_most_every:
            logger.warning("timeout < at_most_every makes no sense, adjusting…")
            timeout = at_most_every

        event = asyncio.Event()
        self._events[key].append(event)

        try:
            if yield_immediately:
                value = self._objects.get(key, yield_for_no_value)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        f"updates('{key}') yielding initial {value!r} to {caller}"
                    )
                yield value

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

                # Clear the event in the same synchronous step as reading the
                # value, and *before* yielding: updates that arrive while the
                # consumer is busy will then trigger the next iteration
                # instead of being wiped out.
                event.clear()
                value = self._objects.get(key, yield_for_no_value)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"updates('{key}') yielding {value!r} to {caller}")
                yield value
                timestamp = time.monotonic()

        finally:
            self._events[key].remove(event)

    def has_subscribers(self, key: str) -> bool:
        return len(self._events.get(key, [])) > 0

    def knows_about(self, key: str) -> bool:
        return key in self._objects
