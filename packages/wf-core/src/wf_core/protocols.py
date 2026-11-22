"""Interfaces other packages implement. Kept tiny on purpose."""

from __future__ import annotations

from typing import Protocol

from .models import Episode, SchemaInfo, Snapshot, Task, ToolCall, ToolSpec, Verdict


class Executor(Protocol):
    """A world that can be reset and driven by tool calls.

    `run` always starts from a fresh copy of the task's initial state and records the
    real result of every call, so replaying the same calls is deterministic.
    """

    schema: SchemaInfo
    tools: tuple[ToolSpec, ...]

    def initial(self, task: Task) -> Snapshot: ...

    def run(self, task: Task, calls: list[ToolCall], final_answer: str | None = None) -> Episode: ...


class Verifier(Protocol):
    id: str

    def verify(self, episode: Episode) -> Verdict: ...
