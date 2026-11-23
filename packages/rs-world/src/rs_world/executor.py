"""`WorldExecutor`: the helpdesk world behind rs-core's `Executor` protocol.

`run(task, calls)` forks a fresh copy of the task's seeded world, executes the calls in order through
the registry (recording ok / result / error for each), and returns an `Episode` holding the initial
and final snapshots. Nothing is shared between runs, so the same calls always give the same digest.
"""

from __future__ import annotations

from rs_core.models import Episode, SchemaInfo, Snapshot, Task, ToolCall, ToolSpec, Trajectory

from .schema import SCHEMA
from .seed import DEFAULT_SEED, WORLD_NOW
from .session import DEFAULT_PERSONA, WorldSession, initial_snapshot, world_path
from .tools import REGISTRY


def task_seed(task: Task) -> int:
    return int(task.provenance.get("seed", DEFAULT_SEED))


class WorldExecutor:
    schema: SchemaInfo = SCHEMA
    tools: tuple[ToolSpec, ...] = REGISTRY.specs()

    def initial(self, task: Task) -> Snapshot:
        return initial_snapshot(task_seed(task))

    def session(self, task: Task) -> WorldSession:
        """A live world for this task (persona, clock and tool list from the task). Close it when done."""
        return WorldSession(
            world_path(task_seed(task)),
            persona=task.persona or DEFAULT_PERSONA,
            now=task.sim_now or WORLD_NOW,
            tools=task.tools,
        )

    def run(self, task: Task, calls: list[ToolCall], final_answer: str | None = None) -> Episode:
        with self.session(task) as session:
            for call in calls:
                session.call(call.tool, call.args)
            final = session.snapshot()
            recorded = tuple(session.calls)
        trajectory = Trajectory(task_id=task.id, agent_id="replay", calls=recorded, final_answer=final_answer)
        return Episode(task=task, initial=self.initial(task), final=final, trajectory=trajectory)
