"""A live, private copy of a seeded world that tool calls can change, plus the per-process world cache.

`WorldSession` is the one place where calls are executed and recorded. The executor replays a list
of calls through it; the MCP server forwards each client call to it. Both therefore produce the same
state and the same recorded `ToolCall`s.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from types import TracebackType

from wf_core.models import Json, Persona, Snapshot, ToolCall

from .context import IdAllocator, SimClock, ToolContext, ToolError
from .schema import connect
from .seed import WORLD_NOW, seed
from .snapshot import fork, read_snapshot
from .tools import REGISTRY

DEFAULT_PERSONA = Persona(id="agent-01", role="agent")

_worlds: dict[int, Path] = {}
_initial: dict[int, Snapshot] = {}
_cache_dir: tempfile.TemporaryDirectory[str] | None = None


def world_path(seed_value: int) -> Path:
    """The seeded world file for `seed_value`, built once per process into a private temp dir."""
    global _cache_dir
    if seed_value not in _worlds:
        if _cache_dir is None:
            _cache_dir = tempfile.TemporaryDirectory(prefix="wf-world-")
        _worlds[seed_value] = seed(seed_value, _cache_dir.name)
    return _worlds[seed_value]


def initial_snapshot(seed_value: int) -> Snapshot:
    if seed_value not in _initial:
        conn = connect(world_path(seed_value), readonly=True)
        try:
            _initial[seed_value] = read_snapshot(conn)
        finally:
            conn.close()
    return _initial[seed_value]


class WorldSession:
    def __init__(
        self,
        world: Path,
        *,
        persona: Persona = DEFAULT_PERSONA,
        now: datetime = WORLD_NOW,
        tools: Iterable[str] | None = None,
    ) -> None:
        handle, name = tempfile.mkstemp(prefix="wf-episode-", suffix=".sqlite")
        os.close(handle)
        self.path = fork(world, Path(name))
        self.conn = connect(self.path)
        self.conn.execute("PRAGMA synchronous = OFF")  # an episode is disposable: skip fsync
        self.conn.execute("PRAGMA journal_mode = MEMORY")
        self.ctx = ToolContext(self.conn, persona, SimClock(now), IdAllocator(self.conn))
        self.allowed = frozenset(tools) if tools is not None else frozenset(REGISTRY.names())
        self.calls: list[ToolCall] = []

    def call(self, tool: str, args: dict[str, Json]) -> ToolCall:
        """Execute one call and record it. Refusals are recorded as failed calls, never raised."""
        idx = len(self.calls)
        try:
            if tool not in self.allowed:
                raise ToolError(f"tool {tool!r} is not available in this task")
            result = REGISTRY.call(self.ctx, tool, dict(args))
            record = ToolCall(idx=idx, tool=tool, args=args, result=result)
        except ToolError as exc:
            record = ToolCall(idx=idx, tool=tool, args=args, ok=False, error=str(exc))
        self.calls.append(record)
        return record

    def snapshot(self) -> Snapshot:
        return read_snapshot(self.conn)

    def close(self) -> None:
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def __enter__(self) -> WorldSession:
        return self

    def __exit__(self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()
