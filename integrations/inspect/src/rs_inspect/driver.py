"""The rs_suite.conformance driver: scripted plans played through the real Inspect task.

`InspectRunner.run(jobs)` takes (instance, plan) pairs and runs them as one Inspect eval: the registered
`rubberstamp_helpdesk` task with its dataset replaced by those instances (`task_with`), the replay model
(`replay.py`: `mockllm/model` answering each turn with the plan's next call), the react agent, Inspect's tool-call
validation and execution, the per-sample worlds, the scorer and a written .eval log. Each `Outcome` is read back
from the eval log: the calls from the sample's store (as the world recorded them), the live final digest, and the
rewards from the score (strict is `Score.value`; the 10 lenient variants come from the score metadata, where they
are zero-weight metrics). A sample that ends in an error becomes an exception, never an outcome.

Three drivers sit on top of it:

- `InspectDriver.run(inst, plan)`: one eval per call (the plain `Driver`).
- `InspectDriver.prefetch(jobs)`: run many jobs in a few large evals first; `run` then returns the prefetched
  outcome for exactly that plan (the plan's calls are part of the key) and runs anything else on its own. This is how
  `conformance.check` covers 500 instances x 3 plans in minutes instead of 1,500 separate evals.
- `BatchingDriver` (async), for `conformance.check_isolation`: concurrent `run`s are gathered into one eval with
  `max_samples` = the concurrency, so the samples (and their worlds) really run at the same time inside Inspect.
  Inspect allows one `eval_async` at a time per process, so batches run one after another.
"""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from inspect_ai import Task, eval_async, task_with
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import MemoryDataset
from inspect_ai.log import EvalLog, EvalSample
from inspect_ai.model import Model
from rs_suite.conformance import Instance, Outcome
from rs_suite.instances import STRICT
from rs_world.oracle_kit import Plan

from .dataset import sample
from .episode import HelpdeskEpisode
from .replay import replay_model
from .scorer import LENIENT
from .task import rubberstamp_helpdesk

__all__ = ["BatchingDriver", "Batcher", "InspectDriver", "InspectRunner", "SampleFailed", "outcome", "plan_key"]

Job = tuple[Instance, Plan]
SCORER = "helpdesk_graders"


class SampleFailed(RuntimeError):
    """The sample ended with an error in Inspect (a world failure, a replay mismatch, ...): never an outcome."""


def plan_key(plan: Plan) -> str:
    return json.dumps([[tool, args] for tool, args in plan.calls], sort_keys=True, default=str)


def outcome(sample_: EvalSample) -> Outcome:
    """The Outcome an eval log reports for one sample."""
    if sample_.error is not None:
        raise SampleFailed(f"sample {sample_.id} failed in Inspect: {sample_.error.message}")
    score = (sample_.scores or {}).get(SCORER)
    if score is None:
        raise SampleFailed(f"sample {sample_.id} has no {SCORER} score")
    if not isinstance(score.value, float) or math.isnan(score.value):  # Score.unscored: an infrastructure failure
        raise SampleFailed(f"sample {sample_.id} was not graded: {score.explanation}")
    meta = score.metadata or {}
    if meta.get("reward_variant") != STRICT:
        raise SampleFailed(f"sample {sample_.id} was scored under {meta.get('reward_variant')!r}, not strict")
    episode = sample_.store_as(HelpdeskEpisode)
    if episode.final_digest is None or meta.get("final_digest") != episode.final_digest:
        raise SampleFailed(f"sample {sample_.id}: the score's final digest is not the live one")
    value: Any = score.value
    rewards = {STRICT: value, **{name: meta["variants"][name] for name in LENIENT}}
    return Outcome(calls=episode.tool_calls(), final_digest=episode.final_digest, rewards=rewards)


class InspectRunner:
    """Runs (instance, plan) jobs as Inspect evals of the rubberstamp_helpdesk task with the replay model."""

    def __init__(self, *, max_samples: int = 16, log_dir: str | Path | None = None, variant: str | None = None) -> None:
        self.max_samples = max_samples
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.variant = variant
        self.evals = 0  # evals run so far
        self.logs: list[str] = []  # their log files (when log_dir is kept)

    def task(self, jobs: Sequence[Job]) -> tuple[Task, Model, list[str]]:
        """The registered task with the jobs as its dataset, the replay model, and each job's sample id."""
        seen: Counter[str] = Counter()
        ids, plans, items = [], {}, []
        for inst, plan in jobs:
            seen[inst.id] += 1
            sid = inst.id if seen[inst.id] == 1 else f"{inst.id}#{seen[inst.id]}"
            ids.append(sid)
            plans[sid] = plan
            items.append(sample(inst, sid))
        base = rubberstamp_helpdesk(
            split="all", tasks=sorted({inst.task_id for inst, _ in jobs}), seeds="own", variant=self.variant
        )
        task = task_with(base, dataset=MemoryDataset(items, name="rubberstamp_helpdesk_replay"))
        return task, replay_model(plans), ids

    def options(self, log_dir: str, **overrides: Any) -> dict[str, Any]:
        """eval() options (`eval_async` has no `display`: it keeps the process's display type, else "plain")."""
        return {
            "log_dir": log_dir,
            "max_samples": self.max_samples,
            "max_connections": self.max_samples,
            "fail_on_error": False,  # a failed sample is reported (as an error), the others still run
            "log_level": "warning",
            **overrides,
        }

    def eval_log(self, jobs: Sequence[Job], log_dir: str | Path, **overrides: Any) -> tuple[EvalLog, list[str]]:
        """Run the jobs as one eval writing its .eval log to `log_dir`; the log and each job's sample id."""
        task, model, ids = self.task(jobs)
        logs = inspect_eval(task, model=model, **self.options(str(log_dir), **{"display": "none", **overrides}))
        return self._one(logs), ids

    def _one(self, logs: list[EvalLog]) -> EvalLog:
        self.evals += 1
        if len(logs) != 1:
            raise RuntimeError(f"the eval returned {len(logs)} logs")
        log = logs[0]
        if log.status not in ("success", "error") or log.samples is None:
            raise RuntimeError(f"the eval ended with status {log.status} ({log.error})")
        if self.log_dir is not None:
            self.logs.append(log.location)
        return log

    def _collect(self, log: EvalLog, ids: list[str]) -> list[Outcome | Exception]:
        by_id = {str(s.id): s for s in log.samples or []}
        results: list[Outcome | Exception] = []
        for sid in ids:
            try:
                results.append(outcome(by_id[sid]))
            except Exception as exc:  # reported per job, never scored
                results.append(exc)
        return results

    def run(self, jobs: Sequence[Job]) -> list[Outcome | Exception]:
        if not jobs:
            return []
        with _LogDir(self.log_dir) as log_dir:
            log, ids = self.eval_log(jobs, log_dir)
            return self._collect(log, ids)

    async def run_async(self, jobs: Sequence[Job]) -> list[Outcome | Exception]:
        if not jobs:
            return []
        task, model, ids = self.task(jobs)
        with _LogDir(self.log_dir) as log_dir:
            log = self._one(await eval_async(task, model=model, **self.options(log_dir)))
            return self._collect(log, ids)


class _LogDir:
    """The runner's log dir, or a temporary one that is deleted afterwards."""

    def __init__(self, keep: Path | None) -> None:
        self.keep = keep
        self.tmp: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> str:
        if self.keep is not None:
            self.keep.mkdir(parents=True, exist_ok=True)
            return str(self.keep)
        self.tmp = tempfile.TemporaryDirectory(prefix="rs-inspect-logs-")
        return self.tmp.name

    def __exit__(self, *exc: object) -> None:
        if self.tmp is not None:
            self.tmp.cleanup()


def _result(value: Outcome | Exception) -> Outcome:
    if isinstance(value, Exception):
        raise value
    return value


class InspectDriver:
    """A conformance `Driver` through Inspect: `run` plays one plan as a one-sample eval, unless `prefetch` already
    ran it in a batch."""

    def __init__(self, runner: InspectRunner | None = None) -> None:
        self.runner = runner or InspectRunner()
        self._ready: dict[tuple[str, str], Outcome | Exception] = {}

    def prefetch(self, jobs: Sequence[Job], batch: int = 500) -> None:
        """Run `jobs` through Inspect `batch` samples per eval and keep their outcomes for `run`."""
        jobs = list(jobs)
        for start in range(0, len(jobs), batch):
            chunk = jobs[start : start + batch]
            for (inst, plan), result in zip(chunk, self.runner.run(chunk), strict=True):
                self._ready[(inst.id, plan_key(plan))] = result

    def run(self, inst: Instance, plan: Plan) -> Outcome:
        ready = self._ready.pop((inst.id, plan_key(plan)), None)
        if ready is None:
            ready = self.runner.run([(inst, plan)])[0]
        return _result(ready)


class Batcher:
    """Gathers concurrent jobs into one Inspect eval (`eval_async`), one eval at a time."""

    def __init__(self, runner: InspectRunner, window_s: float = 0.05) -> None:
        self.runner = runner
        self.window_s = window_s
        self.pending: list[tuple[Instance, Plan, asyncio.Future[Outcome]]] = []
        self.batches: list[int] = []  # the size of every eval run
        self._drain: asyncio.Task[None] | None = None

    async def submit(self, inst: Instance, plan: Plan) -> Outcome:
        future: asyncio.Future[Outcome] = asyncio.get_running_loop().create_future()
        self.pending.append((inst, plan, future))
        if self._drain is None or self._drain.done():
            self._drain = asyncio.create_task(self._run())
        return await future

    async def _run(self) -> None:
        while self.pending:
            await asyncio.sleep(self.window_s)  # let the other concurrent submitters join this eval
            batch, self.pending = self.pending, []
            self.batches.append(len(batch))
            try:
                results = await self.runner.run_async([(inst, plan) for inst, plan, _ in batch])
            except Exception as exc:  # the whole eval failed: every job in it reports the error
                results = [exc] * len(batch)
            for (_, _, future), result in zip(batch, results, strict=True):
                if isinstance(result, Exception):
                    future.set_exception(result)
                else:
                    future.set_result(result)


class BatchingDriver:
    """An async conformance `Driver` whose concurrent runs share one Inspect eval (see `Batcher`)."""

    def __init__(self, batcher: Batcher) -> None:
        self.batcher = batcher

    async def run(self, inst: Instance, plan: Plan) -> Outcome:
        return await self.batcher.submit(inst, plan)
