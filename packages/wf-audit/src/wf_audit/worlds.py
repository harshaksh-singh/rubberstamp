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

TOY_ORDER_CONSTRAINTS: dict[str, tuple[tuple[str, str], ...]] = {
    "refund_email": (("create_refund", "send_email"),),
    "refund_approved": (("request_approval", "create_refund"),),
}
"""Order the toy prompts state: "Refund ..., then email the customer to confirm the refund" and "refunds over $100
need manager approval first". Constraints are policy ground truth taken from the prompt, never from a verifier."""

TOY_FREE_TEXT_FIELDS: dict[str, frozenset[str]] = {"send_email": frozenset({"subject"})}
"""The prompt asks for "a confirmation" and never fixes the subject's wording."""


def toy_world(claim_tool: str | None = "submit") -> World:
    """wf_core's toy world. It has no claim tool, so `submit` is a virtual claim (see target.py)."""
    from wf_core.testing.toyworld import TASKS, ToyExecutor, golden_calls

    return World(
        name="toy" if claim_tool else "toy-noclaim",
        executor=ToyExecutor(),
        tasks=TASKS,
        golden={t: golden_calls(t) for t in TASKS},
        claim_tool=claim_tool,
        order_constraints=TOY_ORDER_CONSTRAINTS,
        free_text_fields=TOY_FREE_TEXT_FIELDS,
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
