import asyncio
import logging
import time

import pytest

from click_async_plugins import ITC


@pytest.fixture
def itc() -> ITC:
    return ITC()


def test_repr_empty(itc: ITC) -> None:
    assert repr(itc) == "ITC()"


def test_repr_single_object(itc: ITC) -> None:
    itc.set("a", 1)
    assert repr(itc) == "ITC(\n       a=1 (0 listeners)\n)"


def test_repr_multiple_objects(itc: ITC) -> None:
    itc.set("a", 1)
    itc.set("b", "two")
    assert repr(itc) == (
        "ITC(\n       a=1 (0 listeners)\n       b='two' (0 listeners)\n    )"
    )


def test_set_and_get(itc: ITC) -> None:
    itc.set("key", {"x": 1})
    assert itc.get("key") == {"x": 1}


def test_get_missing_returns_default(itc: ITC) -> None:
    assert itc.get("missing") is None
    assert itc.get("missing", "fallback") == "fallback"


def test_knows_about(itc: ITC) -> None:
    assert not itc.knows_about("key")
    itc.set("key", None)
    assert itc.knows_about("key")


def test_has_subscribers_false_without_events(itc: ITC) -> None:
    assert not itc.has_subscribers("key")


def test_fire_without_subscribers_is_noop(itc: ITC) -> None:
    itc.fire("nobody-listens")
    assert not itc.has_subscribers("nobody-listens")


@pytest.mark.asyncio
async def test_fire_sets_registered_events(itc: ITC) -> None:
    event = asyncio.Event()
    itc._events["key"].append(event)
    assert itc.has_subscribers("key")
    itc.fire("key")
    assert event.is_set()


@pytest.mark.asyncio
async def test_updates_yields_current_value_immediately(itc: ITC) -> None:
    itc.set("key", 1)
    gen = itc.updates("key")
    assert await anext(gen) == 1
    await gen.aclose()


@pytest.mark.asyncio
async def test_updates_immediate_yield_without_value(itc: ITC) -> None:
    gen = itc.updates("key")
    assert await anext(gen) is None
    await gen.aclose()


@pytest.mark.asyncio
async def test_updates_yield_for_no_value(itc: ITC) -> None:
    gen = itc.updates("key", yield_for_no_value="nothing yet")
    assert await anext(gen) == "nothing yet"
    await gen.aclose()


@pytest.mark.asyncio
async def test_updates_waits_for_set(itc: ITC) -> None:
    gen = itc.updates("key", yield_immediately=False)
    pending = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0)
    assert not pending.done()
    assert itc.has_subscribers("key")

    itc.set("key", 42)
    assert await asyncio.wait_for(pending, 1) == 42

    await gen.aclose()
    assert not itc.has_subscribers("key")


@pytest.mark.asyncio
async def test_updates_yields_consecutive_updates(itc: ITC) -> None:
    gen = itc.updates("key", yield_immediately=False)
    seen = []
    for value in (1, 2, 3):
        pending = asyncio.ensure_future(anext(gen))
        await asyncio.sleep(0)
        itc.set("key", value)
        seen.append(await asyncio.wait_for(pending, 1))
    await gen.aclose()
    assert seen == [1, 2, 3]


@pytest.mark.asyncio
async def test_updates_multiple_subscribers(itc: ITC) -> None:
    gens = [itc.updates("key", yield_immediately=False) for _ in range(2)]
    pending = [asyncio.ensure_future(anext(g)) for g in gens]
    await asyncio.sleep(0)

    itc.set("key", "hello")
    assert await asyncio.wait_for(asyncio.gather(*pending), 1) == ["hello", "hello"]

    for gen in gens:
        await gen.aclose()
    assert not itc.has_subscribers("key")


@pytest.mark.asyncio
async def test_updates_timeout_yields_without_update(
    itc: ITC, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    gen = itc.updates(
        "key", yield_immediately=False, timeout=0.01, yield_for_no_value="timed out"
    )
    assert await asyncio.wait_for(anext(gen), 1) == "timed out"
    await gen.aclose()
    assert "Timeout after 0.01s" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1])
async def test_updates_non_positive_timeout_disabled(
    itc: ITC, caplog: pytest.LogCaptureFixture, timeout: float
) -> None:
    gen = itc.updates("key", timeout=timeout)
    await anext(gen)
    await gen.aclose()
    assert "Updates timeout <= 0, disabling timeout" in caplog.text


@pytest.mark.asyncio
async def test_updates_timeout_adjusted_to_at_most_every(
    itc: ITC, caplog: pytest.LogCaptureFixture
) -> None:
    gen = itc.updates("key", timeout=0.01, at_most_every=1)
    await anext(gen)
    await gen.aclose()
    assert "timeout < at_most_every makes no sense" in caplog.text


@pytest.mark.asyncio
async def test_updates_at_most_every_throttles(
    itc: ITC, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    gen = itc.updates("key", yield_immediately=False, at_most_every=0.1)

    first = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0)
    itc.set("key", 1)
    assert await asyncio.wait_for(first, 1) == 1

    second = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0)
    start = time.monotonic()
    itc.set("key", 2)
    assert await asyncio.wait_for(second, 2) == 2
    assert time.monotonic() - start >= 0.05
    assert "Too early, sleeping for" in caplog.text

    await gen.aclose()
