"""The full grade of an API episode, rebuilt from its trajectory: what a harness uses in eval mode, where rs-api shows
only the strict verdict.

    info = GET /episodes/{id}; trajectory = GET /episodes/{id}/trajectory
    full = regrade(info["episode_id"], info["task"]["id"], info["seed"], Trajectory.model_validate(trajectory))

The recorded calls are replayed by the executor on a fresh copy of the instance's seeded world, which reproduces
every result and refusal (the parity tests check it, and `regrade` refuses a trajectory it does not reproduce), and
the replayed episode is graded by the same function as the API's grade. Run it in the harness's own process: rs-world's
caches are not thread-safe, and inside rs-api they belong to the store's world thread.
"""

from __future__ import annotations

from rs_core.models import ToolCall, Trajectory
from rs_world import WorldExecutor, load_task

from .grading import grade
from .models import Grade


class NotReproduced(ValueError):
    """The replay recorded different calls than the trajectory: it is not an episode of this instance."""


def regrade(episode_id: str, task_id: str, seed: int, trajectory: Trajectory) -> Grade:
    calls = [ToolCall(idx=call.idx, tool=call.tool, args=call.args) for call in trajectory.calls]
    replayed = WorldExecutor().run(load_task(task_id, seed), calls, trajectory.final_answer)
    if replayed.trajectory.calls != trajectory.calls:
        raise NotReproduced(f"replaying the trajectory on {task_id} seed {seed} does not reproduce its calls")
    stopped = replayed.trajectory.model_copy(update={"stop": trajectory.stop})
    episode = replayed.model_copy(update={"trajectory": stopped})
    result = grade(episode_id, episode, seed, trajectory.final_answer or "")
    return result if trajectory.final_answer is not None else result.model_copy(update={"summary": None})
