"""The environment's RFC 004 rubric: one leaf, the reward.

`VerdictRubric` scores every step: 0.0 until the episode ends, then the verdict of the server's reward variant
(strict by default) on the finished episode, as `rs_suite.instances.verdicts` computed it. It has no children on
purpose. In RFC 004 the children of `env.rubric` are the reward's components: trainers log them through
`named_rubrics()`, and OpenEnv's `runtime.reward_attribution` check reads a parent's score from its children. A
lenient variant registered as a child would sit inside the reward's components, which the reward rule forbids, so
lenient verdicts and detectors are reported only as zero-weight metrics in the terminal observation's metadata.

Child rubrics register by attribute assignment (`Rubric.__setattr__` puts any `Rubric` value in
`_rubric_children`) or through the `RubricDict`/`RubricList` containers; this rubric assigns none.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openenv.core.rubrics import Rubric


class VerdictRubric(Rubric):
    """The reward: the graded verdict at the terminal step, 0.0 on every other step."""

    def __init__(self, variant: str, terminal_reward: Callable[[], float | None]) -> None:
        super().__init__()
        self.variant = variant
        self._terminal_reward = terminal_reward  # set by the environment only while it scores the terminal step

    def forward(self, action: Any, observation: Any) -> float:
        reward = self._terminal_reward()
        return 0.0 if reward is None else float(reward)

    def validation_config(self) -> dict[str, Any]:
        """Public configuration for OpenEnv's rubric telemetry (no credentials, no runtime state)."""
        return {"reward_variant": self.variant, "grader": "rs_suite.instances.verdicts"}
