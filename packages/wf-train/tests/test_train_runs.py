"""Run directories: arms, run ids, the pinned design, and what happens to an earlier attempt."""

from datetime import UTC, datetime

import pytest
from wf_core.testing.toyworld import SCHEMA
from wf_train.design import latin_design, load_manifest
from wf_train.env import episode_for
from wf_train.rollouts import RolloutStore, record_of
from wf_train.runs import (
    ManifestMismatch,
    RunDirInUse,
    arm_label,
    arm_of,
    attempt_id,
    checkpoints,
    pin_manifest,
    prepare_run_dir,
    run_dir,
    run_id,
)
from wftrain_helpers import rollout

TEMPLATES = [f"t{i:02d}" for i in range(20)]
VARIANTS = [{"id": "strict", "kind": "strict"}, "status_only", "ignore_order", "trust_claim"]


def design(seed: int = 0):
    return latin_design(TEMPLATES, VARIANTS, design_seed=seed)


def test_the_arm_follows_the_train_variant():
    """Finding #22: a pure-strict run used to share run ids and cells with the multiplexed one."""
    manifest = design()
    assert arm_of("cell", manifest) == "mixed"
    assert arm_of("strict", manifest) == "pure_strict"
    assert arm_of("status_only", manifest) == "single_variant"
    with pytest.raises(KeyError):
        arm_of("nope", manifest)
    assert arm_label("single_variant", "status_only") == "single_variant-status_only"
    assert arm_label("pure_strict", "strict") == "pure_strict"


def test_run_ids_and_directories_carry_the_arm_and_the_attempt(tmp_path):
    """Findings #6/#36: a retry used to reuse `main-s0`, so episode ids collided across attempts."""
    attempt = attempt_id(datetime(2026, 11, 23, 1, 2, 3, tzinfo=UTC))
    assert attempt == "20261123T010203Z"
    assert run_id("main", "mixed", 2, "train", attempt) == "main-mixed-s2-20261123T010203Z"
    assert run_id("main", "mixed", 0, "base", attempt) == "main-base-20261123T010203Z"
    assert run_dir(tmp_path, "pure_strict", 1, "train") == tmp_path / "pure_strict" / "seed1"
    assert run_dir(tmp_path, "pure_strict", 1, "base") == tmp_path / "base"


def test_the_first_invocation_pins_the_design(tmp_path):
    """Finding #24: every invocation used to rebuild and overwrite OUT/manifest.json."""
    assert pin_manifest(tmp_path, design(), given=False) == design()
    assert load_manifest(tmp_path / "manifest.json") == design()
    assert pin_manifest(tmp_path, design(), given=False) == design()  # same design: fine
    with pytest.raises(ManifestMismatch, match="the design rebuilt from the config and world"):
        pin_manifest(tmp_path, design(seed=1), given=False)
    with pytest.raises(ManifestMismatch, match="the --manifest file"):
        pin_manifest(tmp_path, design(seed=1), given=True)
    assert load_manifest(tmp_path / "manifest.json") == design()


def store_with_steps(directory, steps):
    episode = episode_for(*rollout("close_ticket", "golden"))
    store = RolloutStore(directory / "rollouts.jsonl")
    store.append(
        record_of(episode, SCHEMA, run_id="r-a", phase="train", seed=0, step=step, cell_id="c", template="t",
                  variant="strict", sample=i)
        for i, step in enumerate(steps)
    )  # fmt: skip
    return store


def test_a_fresh_run_dir_needs_no_flag_and_cannot_be_resumed(tmp_path):
    directory = tmp_path / "out" / "mixed" / "seed0"
    assert prepare_run_dir(tmp_path / "out", directory, attempt="A", resume=False, overwrite=False) is None
    with pytest.raises(RunDirInUse, match="no earlier attempt"):
        prepare_run_dir(tmp_path / "out", directory, attempt="A", resume=True, overwrite=False)


def test_an_earlier_attempt_is_refused_without_a_flag(tmp_path):
    out = tmp_path / "out"
    directory = out / "mixed" / "seed0"
    (directory / "checkpoint-10").mkdir(parents=True)
    with pytest.raises(RunDirInUse, match="already holds checkpoints"):
        prepare_run_dir(out, directory, attempt="B", resume=False, overwrite=False)
    store_with_steps(directory, [0])
    with pytest.raises(RunDirInUse, match="rollouts.jsonl and checkpoints"):
        prepare_run_dir(out, directory, attempt="B", resume=False, overwrite=False)


def test_resume_continues_from_the_last_checkpoint_and_drops_later_records(tmp_path):
    out = tmp_path / "out"
    directory = out / "mixed" / "seed0"
    for step in (10, 20, 5):
        (directory / f"checkpoint-{step}").mkdir(parents=True)
    (directory / "checkpoint-old").mkdir()  # not a checkpoint name: ignored
    store = store_with_steps(directory, [0, 10, 19, 20, 21])
    assert [s for s, _ in checkpoints(directory)] == [5, 10, 20]
    resumed = prepare_run_dir(out, directory, attempt="C", resume=True, overwrite=False)
    assert resumed is not None and (resumed.step, resumed.checkpoint.name, resumed.dropped) == (20, "checkpoint-20", 2)
    assert [r.step for r in store.read()] == [0, 10, 19]


def test_resume_needs_a_checkpoint(tmp_path):
    out = tmp_path / "out"
    store_with_steps(out / "mixed" / "seed0", [0, 1])
    with pytest.raises(RunDirInUse, match="no checkpoint; use --overwrite"):
        prepare_run_dir(out, out / "mixed" / "seed0", attempt="D", resume=True, overwrite=False)


def test_overwrite_moves_the_earlier_attempt_out_of_the_analysis(tmp_path):
    out = tmp_path / "out"
    directory = out / "mixed" / "seed0"
    store_with_steps(directory, [0, 1])
    assert prepare_run_dir(out, directory, attempt="E", resume=False, overwrite=True) is None
    moved = tmp_path / "out.superseded" / "E" / "mixed" / "seed0" / "rollouts.jsonl"
    assert moved.exists() and not directory.exists()
    assert not list(out.rglob("rollouts.jsonl"))  # `--analyze` collects OUT/**/rollouts.jsonl
    with pytest.raises(ValueError, match="exclude each other"):
        prepare_run_dir(out, directory, attempt="F", resume=True, overwrite=True)
