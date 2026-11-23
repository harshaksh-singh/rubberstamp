"""Shared test helpers: toy rollouts, TRL-shaped reward calls, and verifier stubs.

The stubs here deliberately do NOT use rs_train's detectors (unlike rs_train.toy.ToyStubVerifier), so
reward tests are not circular: a strict stub that compares the final state with the golden one, and a
lenient stub that only looks at the target row's status.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from rs_core.diff import snapshot_digest
from rs_core.models import Check, Episode, Verdict
from rs_core.testing.toyworld import SCHEMA, TASKS, ToyExecutor, golden_calls
from rs_train.env import LoopholeEnv, make_env_factory
from rs_train.scripted import Script, play
from rs_train.toy import SCRIPTS, toy_world
from rs_train.world import World

CONFIGS = Path(__file__).parents[1] / "configs"
WIDE = "rstrain_helpers:wide_toy_world"


def wide_toy_world() -> World:
    """The toy world with its three tasks spread over 20 template ids, like the helpdesk world's first 20 tasks: the
    shipped designs then fit (16 training templates for main.yaml's 9 cycle slots, 16 rows for 4 or 8 prompts
    per step)."""
    world = toy_world()
    tasks = sorted(world.tasks)
    return dataclasses.replace(world, templates={f"{tasks[i % 3]}-{i:02d}": (tasks[i % 3],) for i in range(20)})


def load_cached_tokenizer(model: str):
    """The model's tokenizer from the local Hugging Face cache (files only, no weights), or skip the test."""
    import pytest
    from huggingface_hub import try_to_load_from_cache
    from transformers import AutoTokenizer

    if not isinstance(try_to_load_from_cache(model, "tokenizer_config.json"), str):
        pytest.skip(f"{model} tokenizer is not in the local Hugging Face cache")
    return AutoTokenizer.from_pretrained(model, local_files_only=True)


def config_file(directory: Path, name: str, **changes: Any) -> Path:
    """A copy of configs/<name>.yaml with top-level keys replaced (`grpo`/`gates` entries are merged)."""
    body = yaml.safe_load((CONFIGS / f"{name}.yaml").read_text())
    for key, value in changes.items():
        body[key] = {**body.get(key, {}), **value} if key in ("grpo", "gates", "design") else value
    path = directory / f"{name}-test.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


GOLDEN_DIGEST = {t: snapshot_digest(ToyExecutor().run(TASKS[t], golden_calls(t)).final, SCHEMA) for t in TASKS}
TARGET = {
    "refund_email": ("orders", "o1", "refunded"),
    "close_ticket": ("tickets", "t1", "closed"),
    "refund_approved": ("orders", "o2", "refunded"),
}


class StrictStub:
    """Passes only when the final state equals the golden final state."""

    id = "stub.strict"

    def verify(self, episode: Episode) -> Verdict:
        ok = snapshot_digest(episode.final, SCHEMA) == GOLDEN_DIGEST[episode.task.id]
        return Verdict(passed=ok, reward=float(ok), checks=(Check(id="state:digest", layer="state", passed=ok),))


class StatusOnlyStub:
    """Passes when the target row ends with the right status, whatever else happened."""

    id = "stub.status_only"

    def verify(self, episode: Episode) -> Verdict:
        table, pk, want = TARGET[episode.task.id]
        ok = episode.final.tables[table].get(pk, {}).get("status") == want
        return Verdict(passed=ok, reward=float(ok), checks=(Check(id="state:status", layer="state", passed=ok),))


class BrokenStub:
    id = "stub.broken"

    def verify(self, episode: Episode) -> Verdict:
        return Verdict(passed=False, reward=0.0, error="harness exploded")


def new_env(options: Any = None) -> LoopholeEnv:
    factory = make_env_factory(ToyExecutor(), TASKS.values(), options)
    assert callable(factory)
    return factory()


def rollout(task_id: str, script: str | Script) -> tuple[LoopholeEnv, list[dict[str, Any]]]:
    """A fresh environment that has played one script, and the TRL-shaped completion."""
    env = new_env()
    env.reset(task_id=task_id, prompt=[{"role": "user", "content": TASKS[task_id].prompt}])
    return env, play(env, SCRIPTS[task_id][script] if isinstance(script, str) else script)


def trl_call(func: Any, envs: Any, completions: Any, rows: list[dict[str, Any]] | None = None, step: int = 0):
    """Call a reward function with the keyword arguments GRPOTrainer._calculate_rewards passes."""
    envs, completions = list(envs), list(completions)
    rows = rows or [{"task_id": env.task.id} for env in envs]
    columns = {key: [row[key] for row in rows] for key in rows[0]}
    return func(
        prompts=[[] for _ in envs],
        completions=completions,
        completion_ids=None,
        trainer_state=SimpleNamespace(global_step=step),
        log_extra=lambda *a: None,
        log_metric=lambda *a: None,
        environments=envs,
        **columns,
    )
