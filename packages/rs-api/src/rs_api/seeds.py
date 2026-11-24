"""A bound on the per-seed caches behind episodes.

Every world seed an episode uses leaves something in this process: rs-world builds the seed's world file (about
240 KB in a temp dir) and keeps its initial snapshot (about 0.65 MB), and `load_task`, `instance_spec` and
`instance_verifier` (grading.py) cache that instance's task and verifiers. None of these caches has a bound, so a
harness that sweeps seeds would grow the process by about 0.7 MB of memory and 240 KB of disk per seed, for good.

`SeedCaches` keeps at most `max_seeds` seeds besides the tasks' own seeds (which every default episode uses and
which stay), least recently used first out. Evicting a seed deletes its world file and drops its snapshot; the
function caches have no per-key delete, so they are cleared whole and refill on the next use of each instance.
Nothing is lost by evicting: every cached value is a pure function of (task, seed) and is rebuilt the same way, and a
live episode on an evicted seed keeps working on its own copy of the world.

Only the store's world thread calls this, as it does every other world operation.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable

from rs_world import session as world_session
from rs_world.tasks import load_task

from .grading import instance_spec, instance_verifier

MAX_SEEDS = 64


def release(seed: int) -> None:
    """Drop what this process caches for `seed`: its world file, its snapshot, and the per-instance caches.

    rs-world keeps the world path and the snapshot per seed in module dicts and offers no public way to drop one, so
    this reaches into them; `test_seed_caches_are_bounded_and_released` fails if that layout changes."""
    path = world_session._worlds.pop(seed, None)
    if path is not None:
        path.unlink(missing_ok=True)
    world_session._initial.pop(seed, None)
    load_task.cache_clear()
    instance_spec.cache_clear()
    instance_verifier.cache_clear()


class SeedCaches:
    def __init__(self, max_seeds: int = MAX_SEEDS, pinned: Iterable[int] = ()) -> None:
        if max_seeds < 1:
            raise ValueError("max_seeds must be at least 1")
        self.max_seeds = max_seeds
        self.pinned = frozenset(pinned)  # the tasks' own seeds: never counted, never released
        self._seeds: OrderedDict[int, None] = OrderedDict()
        self.released = 0

    def use(self, seed: int) -> None:
        """`seed` is about to be used: mark it most recent, and release the least recent beyond the bound."""
        if seed in self.pinned:
            return
        self._seeds[seed] = None
        self._seeds.move_to_end(seed)
        while len(self._seeds) > self.max_seeds:
            evicted, _ = self._seeds.popitem(last=False)
            release(evicted)
            self.released += 1

    def kept(self) -> list[int]:
        """The seeds whose caches are kept, least recently used first (the pinned seeds excluded)."""
        return list(self._seeds)
