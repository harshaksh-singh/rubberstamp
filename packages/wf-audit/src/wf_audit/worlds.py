"""Named worlds the CLI can audit, and loading of verifier-variant factories.

A variant factory is any `module:function` that takes a `World` and returns `{variant: Verifier}`.
Until the wf-verify package lands, the toy world's default factory is the stand-in set in
`wf_audit.testing.toy_verifiers`.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from wf_core.protocols import Verifier

from .target import World


def toy_world(claim_tool: str | None = "submit") -> World:
    """wf_core's toy world. It has no claim tool, so `submit` is a virtual claim (see target.py)."""
    from wf_core.testing.toyworld import TASKS, ToyExecutor, golden_calls

    return World(
        name="toy" if claim_tool else "toy-noclaim",
        executor=ToyExecutor(),
        tasks=TASKS,
        golden={t: golden_calls(t) for t in TASKS},
        claim_tool=claim_tool,
    )


WORLDS: dict[str, Callable[..., World]] = {"toy": toy_world}
DEFAULT_VARIANTS: dict[str, str] = {"toy": "wf_audit.testing.toy_verifiers:toy_variants"}


def load_world(name: str, **kwargs: object) -> World:
    if name not in WORLDS:
        raise KeyError(f"unknown world {name!r}; known: {sorted(WORLDS)}")
    return WORLDS[name](**kwargs)


def load_variants(spec: str, world: World) -> dict[str, Verifier]:
    """Call the `module:function` factory `spec` with `world`."""
    module, _, attr = spec.partition(":")
    if not attr:
        raise ValueError(f"variant factory must be module:function, got {spec!r}")
    factory = getattr(importlib.import_module(module), attr)
    return dict(factory(world))
