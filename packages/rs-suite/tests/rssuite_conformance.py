"""Shared body of the sharded all-500 tests (test_suite_conformance_shard<k>.py).

The 500 instances are split by world seed into `SHARDS` test files, so `pytest -n auto --dist loadfile` runs them on
separate workers (each shard takes well under a minute). Each shard checks, on its seeds:

1. the reference driver passes `rs_suite.conformance.check` on every instance x {golden, shortcut, noop};
2. the committed instance matrix rows regenerate exactly (with the unsharded serialisation test, byte for byte);
3. P0.3: rs-api's and rs-train's grading paths as they were before `rs_suite.instances` existed (frozen below,
   verbatim) give the same verdict as `rs_suite.instances.verdicts` on every instance x plan x variant.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from rs_core.models import Episode
from rs_core.protocols import Verifier
from rs_suite import VARIANTS, answer_is_claim, build_spec, claim_pattern, load_spec, verifier
from rs_suite.conformance import PLANS, ReferenceDriver, check, instances, plan_for
from rs_suite.instance_matrix import JSON_NAME, entries_for
from rs_suite.instances import SEEDS, instance_task, verdicts
from rs_verify import Leniency, VerifierSpec, lenient_verifier
from rs_world import SCHEMA, WorldExecutor, load_task
from rs_world.tasks import task_file

SHARDS = 5
RESULTS = Path(__file__).resolve().parents[3] / "results" / "suite"
EXECUTOR = WorldExecutor()


def shard_seeds(k: int) -> tuple[int, ...]:
    return SEEDS[k::SHARDS]


def assert_reference_driver_conforms(k: int) -> None:
    insts = instances(seeds=shard_seeds(k))
    assert len(insts) == 25 * len(shard_seeds(k))
    mismatches = check(ReferenceDriver(), insts)
    assert mismatches == [], "\n".join(str(m) for m in mismatches[:20])


def assert_matrix_rows_regenerate(k: int) -> None:
    committed = json.loads((RESULTS / JSON_NAME).read_text())["instances"]
    rows = entries_for(shard_seeds(k))
    assert len(rows) == 25 * len(shard_seeds(k))
    for iid, row in rows.items():
        assert row == committed[iid], f"{iid}: run scripts/instance_matrix.py"


# ---------------------------------------------------------------- the grading paths before P0.3, frozen verbatim


@cache
def legacy_api_spec(task_id: str, seed: int) -> VerifierSpec:
    """rs_api.grading.instance_spec before P0.3."""
    return build_spec(task_id, seed)


@cache
def legacy_api_verifier(task_id: str, seed: int, variant: str) -> Verifier:
    """rs_api.grading.instance_verifier before P0.3: rs-suite's own verifier on the task's own seed."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; known: {list(VARIANTS)}")
    if seed == task_file(task_id).seed:
        return verifier(task_id, variant)
    flags = () if variant == "strict" else (Leniency(variant),)
    return lenient_verifier(
        legacy_api_spec(task_id, seed),
        SCHEMA,
        *flags,
        claim_pattern=claim_pattern(task_id),
        answer_is_claim=answer_is_claim(task_id),
    )


@cache
def legacy_train_spec(task_id: str, seed: int) -> VerifierSpec:
    """rs_train.helpdesk.instance_spec before P0.3."""
    spec = load_spec(task_id) if seed == task_file(task_id).seed else build_spec(task_id, seed)
    iid = f"{task_id}@{seed}"
    return spec.model_copy(update={"id": f"helpdesk.{iid}.strict", "task_id": iid})


@cache
def legacy_train_verifier(task_id: str, seed: int, variant: str) -> Verifier:
    """rs_train.helpdesk.HelpdeskRegistry.__call__ before P0.3, for the variant that plants `variant`'s flag."""
    flags = () if variant == "strict" else (variant,)
    return lenient_verifier(
        legacy_train_spec(task_id, seed),
        SCHEMA,
        *(Leniency(flag) for flag in flags),
        claim_pattern=claim_pattern(task_id),
        answer_is_claim=answer_is_claim(task_id),
    )


def assert_grading_paths_agree(k: int) -> None:
    compared = 0
    for inst in instances(seeds=shard_seeds(k)):
        task_id, seed = inst.task_id, inst.seed
        for name in PLANS:
            plan = plan_for(inst, name)
            # rs-api's episodes are named by task id, rs-train's by instance id
            api: Episode = EXECUTOR.run(load_task(task_id, seed), plan.tool_calls(), plan.answer)
            train: Episode = EXECUTOR.run(instance_task(task_id, seed), plan.tool_calls(), plan.answer)
            new_api, new_train = verdicts(api, seed), verdicts(train, seed)
            for variant in VARIANTS:
                old_api = legacy_api_verifier(task_id, seed, variant).verify(api)
                old_train = legacy_train_verifier(task_id, seed, variant).verify(train)
                where = (inst.id, name, variant)
                assert old_api == new_api[variant], where
                assert old_train == new_train[variant], where
                assert old_api.reward == old_train.reward == new_api[variant].reward == new_train[variant].reward
                compared += 1
    assert compared == 25 * len(shard_seeds(k)) * len(PLANS) * len(VARIANTS)
