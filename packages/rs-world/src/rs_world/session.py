"""A live, private copy of a seeded world that tool calls can change, plus the per-process world cache.

`WorldSession` is the one place where calls are executed and recorded. The executor replays a list
of calls through it; the MCP server forwards each client call to it. Both therefore produce the same
state and the same recorded `ToolCall`s.

The world cache (`world_path`, `initial_snapshot`) is safe to fill from many threads: each seed is built once,
under its own lock, and other seeds keep building in parallel. `prewarm(seeds)` builds them up front, so a
server's first episodes do not pay the cold build. A `WorldSession` itself belongs to one thread at a time (its
SQLite connection keeps sqlite3's default `check_same_thread=True`).
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from types import TracebackType

from rs_core.models import Json, Persona, Snapshot, ToolCall

from .context import IdAllocator, SimClock, ToolContext, ToolError
from .schema import connect
from .seed import WORLD_NOW, seed
from .snapshot import fork, read_snapshot
from .tools import REGISTRY

DEFAULT_PERSONA = Persona(id="agent-01", role="agent")

_worlds: dict[int, Path] = {}
_initial: dict[int, Snapshot] = {}
_cache_dir: tempfile.TemporaryDirectory[str] | None = None
_cache_lock = threading.Lock()  # guards `_cache_dir` and `_seed_locks`
_seed_locks: dict[int, threading.RLock] = {}


def _seed_lock(seed_value: int) -> threading.RLock:
    with _cache_lock:
        lock = _seed_locks.get(seed_value)
        if lock is None:
            lock = _seed_locks[seed_value] = threading.RLock()
        return lock


def _cache_root() -> str:
    global _cache_dir
    with _cache_lock:
        if _cache_dir is None:
            _cache_dir = tempfile.TemporaryDirectory(prefix="rs-world-")
        return _cache_dir.name


def world_path(seed_value: int) -> Path:
    """The seeded world file for `seed_value`, built once per process into a private temp dir (thread-safe)."""
    path = _worlds.get(seed_value)
    if path is not None:
        return path
    with _seed_lock(seed_value):
        path = _worlds.get(seed_value)
        if path is None:  # the first thread here builds it; the others waited on the lock and reuse it
            path = _worlds[seed_value] = seed(seed_value, _cache_root())
        return path


def initial_snapshot(seed_value: int) -> Snapshot:
    """The seeded world's state before any call, read once per process (thread-safe)."""
    snapshot = _initial.get(seed_value)
    if snapshot is not None:
        return snapshot
    with _seed_lock(seed_value):
        snapshot = _initial.get(seed_value)
        if snapshot is None:
            conn = connect(world_path(seed_value), readonly=True)
            try:
                snapshot = _initial[seed_value] = read_snapshot(conn)
            finally:
                conn.close()
        return snapshot


def prewarm(seeds: Iterable[int]) -> dict[int, Path]:
    """Build (or reuse) the world file and the initial snapshot of every seed now, e.g. at server start-up, so no
    episode pays a cold build. Returns seed -> world file."""
    out = {}
    for seed_value in seeds:
        out[seed_value] = world_path(seed_value)
        initial_snapshot(seed_value)
    return out


class WorldSession:
    def __init__(
        self,
        world: Path,
        *,
        persona: Persona = DEFAULT_PERSONA,
        now: datetime = WORLD_NOW,
        tools: Iterable[str] | None = None,
    ) -> None:
        handle, name = tempfile.mkstemp(prefix="rs-episode-", suffix=".sqlite")
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
