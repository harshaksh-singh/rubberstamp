"""What the harness needs from a world, in one object, and how a config names it.

`world: toy` in a config loads the bundled toy world (wf_train.toy). Any other value is an import path
`package.module:function` returning a `World`; that is where Track C's world and Track A's verifiers
plug in later without this package importing them.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from wf_core.models import Task
from wf_core.protocols import Executor, Verifier

from .design import Variant
from .detectors import TaskProfile
from .scripted import Script

# (task id, variant) -> the verifier that grades that task under that variant.
VerifierRegistry = Callable[[str, Variant], Verifier]


@dataclass(frozen=True)
class World:
    name: str
    executor: Executor
    tasks: Mapping[str, Task]  # task id -> task
    templates: Mapping[str, tuple[str, ...]]  # template id -> its task ids (procedural instances)
    profiles: Mapping[str, TaskProfile]  # task id -> golden-derived facts for the detectors
    verifier: VerifierRegistry
    scripts: Mapping[str, Mapping[str, Script]] = field(default_factory=dict)  # task id -> behaviour -> script

    def template_of(self, task_id: str) -> str:
        return next(t for t, ids in self.templates.items() if task_id in ids)

    def tasks_of(self, templates: tuple[str, ...] | list[str]) -> list[Task]:
        return [self.tasks[task_id] for t in templates for task_id in self.templates[t]]


def load_world(spec: str) -> World:
    if spec == "toy":
        spec = "wf_train.toy:toy_world"
    module, _, attr = spec.partition(":")
    if not attr:
        raise ValueError(f"world must be 'toy' or 'package.module:function', got {spec!r}")
    world = getattr(importlib.import_module(module), attr)()
    if not isinstance(world, World):
        raise TypeError(f"{spec} returned {type(world).__name__}, expected wf_train.world.World")
    return world
