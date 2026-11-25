"""One live helpdesk world per Inspect sample, and the sample's record of it.

`open_world()` is the task's setup solver: it opens a private `WorldSession` (a forked copy of the instance's seeded
SQLite world) for the sample and keeps it in `_LIVE`, keyed by the sample's `TaskState.uuid`, so every epoch and
every concurrent sample has its own world. `close_world` is the task's cleanup: it records the final digest and
closes and deletes the session; Inspect calls it after the solvers and scorers, also when a sample fails.

`HelpdeskEpisode` is the sample's store entry (it is written to the .eval log): the instance, the calls exactly as
the world recorded them, the submitted summary, the live final digest and, if the world failed, why. The scorer
replays those calls offline, so `inspect score` can regrade a log without the live world; an episode whose world
failed is never graded (see `scorer.py`).

A sample cannot be resumed from an Inspect checkpoint: the live world is not part of a checkpoint, so a resumed
sample would carry on against a fresh world. The task vetoes checkpoints (`task.py`); should a sample be resumed
anyway, `refuse_resume` (the task's `on_resume`) ends it with a `WorldFailure`, and `open_world` refuses a store that
already holds an episode.

A `WorldSession` belongs to one thread (its SQLite connection keeps `check_same_thread=True`). Inspect runs a
sample's setup, tools, scorer and cleanup as coroutines on the eval's event loop thread, so the session never
changes threads.
"""

from __future__ import annotations

from typing import Any, Literal

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import StoreModel, store_as
from pydantic import Field
from rs_core.diff import snapshot_digest
from rs_core.models import ToolCall
from rs_suite.instances import instance_id, instance_task
from rs_suite.suite import CLAIM_TOOL, EXECUTOR
from rs_world import SCHEMA, WorldSession

__all__ = [
    "HelpdeskEpisode",
    "WorldFailure",
    "close_world",
    "live_count",
    "live_session",
    "open_world",
    "refuse_resume",
    "world_failed",
]


class WorldFailure(RuntimeError):
    """The world itself failed (not a refusal the app makes): an infrastructure failure. Inspect does not map a
    RuntimeError to a model-visible tool error, so it ends the sample with an error; it is never scored."""


class HelpdeskEpisode(StoreModel):
    """The sample's episode, kept in the Inspect store (and so in the .eval log)."""

    key: str = ""  # TaskState.uuid of the sample that owns the live session
    sample_id: str = ""
    task_id: str = ""
    seed: int = -1
    calls: list[dict[str, Any]] = Field(default_factory=list)  # ToolCall.model_dump(mode="json"), in order
    summary: str | None = None  # the summary of the first successful submit_resolution
    final_digest: str | None = None  # snapshot digest of the live world when the episode was graded or closed
    world_failure: str | None = None  # why the world failed (an infrastructure failure): never graded

    @property
    def instance_id(self) -> str:
        return instance_id(self.task_id, self.seed)

    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(ToolCall.model_validate(call) for call in self.calls)


_LIVE: dict[str, WorldSession] = {}
"""TaskState.uuid -> the sample's live world. Never shared between samples."""


def live_count() -> int:
    """How many sample worlds are open in this process (0 once every sample has been cleaned up)."""
    return len(_LIVE)


def live_session(episode: HelpdeskEpisode) -> WorldSession | None:
    return _LIVE.get(episode.key) if episode.key else None


def claimed(session: WorldSession) -> bool:
    """The episode ends at the first successful claim (submit_resolution); later calls are refused, never run."""
    return any(call.ok and call.tool == CLAIM_TOOL for call in session.calls)


def world_failed(episode: HelpdeskEpisode, message: str) -> WorldFailure:
    """Record in the episode that its world failed (the first failure is kept) and return the error to raise."""
    if episode.world_failure is None:
        episode.world_failure = message
    return WorldFailure(message)


def record_digest(episode: HelpdeskEpisode, session: WorldSession) -> str:
    digest = snapshot_digest(session.snapshot(), SCHEMA)
    episode.final_digest = digest
    return digest


@solver
def open_world() -> Solver:
    """Setup solver: open the sample's own world (instance from the sample metadata `task_id` and `seed`)."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        task_id = state.metadata.get("task_id")
        seed = state.metadata.get("seed")
        if not isinstance(task_id, str) or not isinstance(seed, int):
            raise ValueError(f"sample {state.sample_id!r} has no task_id/seed metadata")
        task = instance_task(task_id, seed)
        episode = state.store_as(HelpdeskEpisode)
        if episode.key or episode.calls:  # a store carried over from an earlier run of the sample: its world is gone
            raise world_failed(episode, f"sample {state.sample_id!r} already holds an episode; it cannot be resumed")
        stale = _LIVE.pop(state.uuid, None)  # a retried attempt of the same sample starts from a fresh world
        if stale is not None:
            stale.close()
        _LIVE[state.uuid] = EXECUTOR.session(task)
        episode.key = state.uuid
        episode.sample_id = str(state.sample_id)
        episode.task_id = task_id
        episode.seed = seed
        episode.calls = []
        episode.summary = None
        episode.final_digest = None
        from .tools import helpdesk_tools  # the tools import this module

        state.tools = helpdesk_tools()  # for solvers that use state.tools (react gets them directly)
        return state

    return solve


async def close_world(state: TaskState) -> None:
    """Task cleanup: record the live final digest (so a `score=False` run can be scored later) and close and
    delete the sample's world. A world that failed gets no digest. Idempotent."""
    session = _LIVE.pop(state.uuid, None)
    if session is None:
        return
    try:
        episode = state.store_as(HelpdeskEpisode)
        if episode.final_digest is None and episode.world_failure is None:
            record_digest(episode, session)
    finally:
        session.close()


async def refuse_resume(state: TaskState, attempt: Literal["initial", "resume", "resume_for_scoring"]) -> None:
    """The task's `on_resume`: Inspect calls it after restoring a sample's store from a checkpoint, before the agent
    goes on. The restored episode's world is gone (only the store was checkpointed), so the sample ends with a
    `WorldFailure` and is never graded."""
    episode = state.store_as(HelpdeskEpisode)
    message = f"sample {state.sample_id!r}: checkpoint {attempt} is not supported (the live world is not checkpointed)"
    raise world_failed(episode, message)


def current_episode() -> HelpdeskEpisode:
    """The running sample's episode (Inspect keeps one store per sample in a context variable)."""
    return store_as(HelpdeskEpisode)
