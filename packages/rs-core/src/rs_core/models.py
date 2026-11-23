"""Core data contracts shared by every RubberStamp package.

Everything here is plain data (Pydantic models, frozen). Behaviour lives in other
modules: `diff` computes state differences, `protocols` defines what a world or a
verifier must implement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

CONTRACT_VERSION = 1

Json = Any  # any JSON-serialisable value


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- world state


class TableInfo(Frozen):
    """What the diff and the mutation operators need to know about one table."""

    pk: str
    kind: Literal["entity", "side_effect", "audit", "config"] = "entity"
    volatile: tuple[str, ...] = ()  # ignored when comparing states (e.g. updated_at)
    fk: dict[str, str] = {}  # field -> referenced table


class SchemaInfo(Frozen):
    version: str
    tables: dict[str, TableInfo]


class Snapshot(Frozen):
    """A whole world state: table -> primary key (as str) -> row."""

    schema_version: str
    tables: dict[str, dict[str, dict[str, Json]]]


# ---------------------------------------------------------------- tools and trajectories


class ToolSpec(Frozen):
    name: str
    app: str
    description: str
    params: dict[str, Json]  # JSON Schema of the arguments
    effect: Literal["read", "write"]
    idempotent: bool = True
    id_params: tuple[str, ...] = ()  # arguments that name a record
    roles: tuple[str, ...] = ("agent",)


class ToolCall(Frozen):
    idx: int
    tool: str
    args: dict[str, Json]
    ok: bool = True
    result: Json = None
    error: str | None = None


class Trajectory(Frozen):
    task_id: str
    agent_id: str
    calls: tuple[ToolCall, ...] = ()
    final_answer: str | None = None
    stop: Literal["done", "max_steps", "error", "budget"] = "done"


# ---------------------------------------------------------------- tasks


class Persona(Frozen):
    id: str
    role: str
    permissions: tuple[str, ...] = ()


class Task(Frozen):
    id: str
    version: int = 1
    source: str  # "rubberstamp", "toy", "tau2", ...
    prompt: str
    persona: Persona | None = None
    tools: tuple[str, ...]
    sim_now: datetime | None = None
    category: str | None = None
    split: Literal["dev", "train", "test", "heldout"] | None = None
    provenance: dict[str, Json] = {}


# ---------------------------------------------------------------- verdicts


class Check(Frozen):
    id: str  # e.g. "state:expect:0", "state:scope:refunds:7", "process:approval_first"
    layer: Literal["state", "process", "answer", "rubric", "native"]
    passed: bool
    detail: str = ""


class Verdict(Frozen):
    passed: bool
    reward: float
    checks: tuple[Check, ...] = ()
    not_evaluated: tuple[str, ...] = ()
    error: str | None = None


class Episode(Frozen):
    """Everything a verifier may look at after one attempt at a task."""

    task: Task
    initial: Snapshot
    final: Snapshot
    trajectory: Trajectory
