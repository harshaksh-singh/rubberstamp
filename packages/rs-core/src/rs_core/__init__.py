"""RubberStamp core contracts: data models, state diff, and the toy world fixture."""

from .diff import StateDiff, apply, diff, digest, normalize, snapshot_digest
from .models import (
    CONTRACT_VERSION,
    Episode,
    SchemaInfo,
    Snapshot,
    TableInfo,
    Task,
    ToolCall,
    ToolSpec,
    Trajectory,
    Verdict,
)

__all__ = [
    "CONTRACT_VERSION",
    "Episode",
    "SchemaInfo",
    "Snapshot",
    "StateDiff",
    "TableInfo",
    "Task",
    "ToolCall",
    "ToolSpec",
    "Trajectory",
    "Verdict",
    "apply",
    "diff",
    "digest",
    "normalize",
    "snapshot_digest",
]
