from collections.abc import Callable, Sequence
from typing import Any

import click

from .typedefs import PluginFactory

# Dependencies are recorded as attributes, rather than in some registry, so
# that they survive decorators that use functools.wraps (e.g. pass_clictx).
_REQUIRES_ATTR = "__plugin_requires__"
_NAME_ATTR = "__plugin_name__"


class PluginDependencyError(click.UsageError):
    pass


def depends_on[T](*plugins: str | click.Command) -> Callable[[T], T]:
    if not plugins:
        raise ValueError("depends_on() requires at least one plugin")

    names = tuple(
        _command_name(p) if isinstance(p, click.Command) else p for p in plugins
    )

    def decorator(obj: T) -> T:
        # Also allow use above @plugin, where we get handed the command.
        target: Any = obj.callback if isinstance(obj, click.Command) else obj
        if target is None:
            raise TypeError(f"{obj!r} has no callback to attach dependencies to")

        existing: tuple[str, ...] = getattr(target, _REQUIRES_ATTR, ())
        setattr(target, _REQUIRES_ATTR, (*existing, *names))
        return obj

    return decorator


def _command_name(command: click.Command) -> str:
    if command.name is None:
        raise ValueError(f"{command!r} has no name, so cannot be depended upon")
    return command.name


def get_dependencies(obj: object) -> tuple[str, ...]:
    return tuple(getattr(obj, _REQUIRES_ATTR, ()))


def mark_plugin_factory(
    factory: PluginFactory, *, name: str | None, requires: tuple[str, ...]
) -> None:
    setattr(factory, _NAME_ATTR, name)
    setattr(factory, _REQUIRES_ATTR, requires)


def _lookup(factory: PluginFactory, attr: str) -> Any:
    # Also look through functools.partial, like util._get_name does.
    for candidate in (factory, getattr(factory, "func", None)):
        if candidate is not None and (value := getattr(candidate, attr, None)):
            return value
    return None


def _plugin_name(factory: PluginFactory) -> str | None:
    name: str | None = _lookup(factory, _NAME_ATTR)
    return name


def _display_name(factory: PluginFactory) -> str:
    return _plugin_name(factory) or str(getattr(factory, "__name__", factory))


def resolve_dependencies(
    plugin_factories: Sequence[PluginFactory],
) -> list[PluginFactory]:
    names = [_display_name(f) for f in plugin_factories]
    requires: list[tuple[str, ...]] = [
        _lookup(f, _REQUIRES_ATTR) or () for f in plugin_factories
    ]

    by_name: dict[str, list[int]] = {}
    for idx, factory in enumerate(plugin_factories):
        if (name := _plugin_name(factory)) is not None:
            by_name.setdefault(name, []).append(idx)

    ordered: list[int] = []
    done: set[int] = set()

    def visit(idx: int, trail: tuple[int, ...]) -> None:
        if idx in done:
            return

        if idx in trail:
            cycle = [*trail[trail.index(idx) :], idx]
            raise PluginDependencyError(
                "Circular plugin dependency: " + " → ".join(names[i] for i in cycle)
            )

        for dependency in requires[idx]:
            if dependency not in by_name:
                raise PluginDependencyError(
                    f"Plugin '{names[idx]}' requires plugin '{dependency}', "
                    "which was not specified."
                )
            for dependency_idx in by_name[dependency]:
                visit(dependency_idx, (*trail, idx))

        done.add(idx)
        ordered.append(idx)

    for idx in range(len(plugin_factories)):
        visit(idx, ())

    return [plugin_factories[idx] for idx in ordered]
