"""CPU rehearsal: the real GRPOTrainer, built by build_trainer from the smoke config, on a tiny randomly
initialised Qwen3 (no weights downloaded; the Qwen3-1.7B tokenizer from the local cache, else skipped) with
use_vllm=False. Real steps run through environment_factory, the reward functions, the recorder and the gate
callback, which the scripted dry run cannot show: finding #32's empty sampler trained 0 steps and exited 0.
"""

from collections import Counter

import pytest
from rs_train.analysis import collect
from rs_train.config import GateSettings, load_config
from rs_train.rollouts import RolloutStore
from rs_train.runs import prepare_run_dir
from rs_train.toy import toy_world
from rs_train.train import BaseTopUp, build_manifest, build_trainer, training_problem
from rstrain_helpers import CONFIGS, load_cached_tokenizer

CPU = {
    "use_vllm": False,
    "use_cpu": True,
    "bf16": False,
    "model_init_kwargs": None,
    "gradient_checkpointing": False,
    "log_completions": False,
    "save_strategy": "no",
    "num_generations": 2,
    "per_device_train_batch_size": 2,
    "gradient_accumulation_steps": 2,  # 4 rollouts per step: 2 prompts, the toy world's 2 training rows
    "max_completion_length": 16,
    "max_tool_calling_iterations": 2,
    "max_steps": 2,
}


@pytest.fixture(scope="module")
def tokenizer():
    return load_cached_tokenizer("Qwen/Qwen3-1.7B")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setenv("TRL_EXPERIMENTAL_SILENCE", "1")  # environment_factory is flagged experimental


def tiny_qwen3(tokenizer):
    from transformers import Qwen3Config, Qwen3ForCausalLM

    config = Qwen3Config(
        vocab_size=len(tokenizer),
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=1,
        num_attention_heads=2,
        num_key_value_heads=1,
        head_dim=8,
        tie_word_embeddings=True,
        eos_token_id=tokenizer.eos_token_id,
        pad_token_id=tokenizer.pad_token_id,
    )
    return Qwen3ForCausalLM(config)


def trainer(tokenizer, out, phase="train", run="smoke-pure_strict-s0-A", grpo=None, top_up=None, **changes):
    cfg = load_config(CONFIGS / "smoke.yaml")
    cfg = cfg.model_copy(update={"grpo": {**cfg.grpo, **CPU, **(grpo or {})}, **changes})
    world = toy_world()
    return build_trainer(
        cfg, world, build_manifest(cfg, world), 0, out, phase, run=run, arm="pure_strict", manifest_digest="d",
        model=tiny_qwen3(tokenizer), processing_class=tokenizer, top_up=top_up,
    )  # fmt: skip


def test_grpo_trainer_trains_every_step_and_records_every_rollout(tokenizer, tmp_path):
    t = trainer(tokenizer, tmp_path)
    store = RolloutStore(tmp_path / "rollouts.jsonl")
    assert training_problem(t, store) == "trained 0 of 2 steps"  # what the old empty sampler left behind
    t.train()
    records = store.read()
    assert t.state.global_step == 2 and training_problem(t, store) is None
    assert [r.step for r in records] == [0] * 4 + [1] * 4
    assert {(r.arm, r.run_id, r.manifest_digest) for r in records} == {("pure_strict", "smoke-pure_strict-s0-A", "d")}
    logged = t.state.log_history[0]
    for key in ("rewards/unparsed_tool_call/mean", "rewards/cut_off/mean", "tools/call_frequency", "step_time"):
        assert key in logged, key


def test_nothing_recorded_blocks_the_final_save(tokenizer, tmp_path):
    t = trainer(tokenizer, tmp_path)
    t.state.global_step = t.args.max_steps  # as if every step ran, but the recorder never saw a rollout
    assert training_problem(t, RolloutStore(tmp_path / "rollouts.jsonl")).startswith("no rollouts were recorded")


def test_an_empty_sampler_is_refused_before_training(tokenizer, tmp_path):
    with pytest.raises(ValueError, match="2 training rows, but each generation batch needs 4 distinct prompts"):
        trainer(tokenizer, tmp_path, grpo={"per_device_train_batch_size": 4})


def test_a_tripped_gate_stops_training_and_blocks_the_final_save(tokenizer, tmp_path):
    """Finding #37: the random model never calls a tool, so the zero-tool-calls rule fires at the first step."""
    t = trainer(tokenizer, tmp_path, gates=GateSettings(no_tool_call_steps=1))
    t.train()
    assert t.state.global_step == 1
    problem = training_problem(t, RolloutStore(tmp_path / "rollouts.jsonl"))
    assert problem == "stopped by a gate at step 1: tools/call_frequency was 0 for 1 logged steps"


def test_the_base_sweep_samples_every_template_without_a_loss_pass(tokenizer, tmp_path):
    """Findings #7/#40: base_samples per template (2 rows x G=2 here), and evaluate() runs no loss forward."""
    t = trainer(tokenizer, tmp_path, phase="base", run="smoke-base-A", base_samples=4)
    metrics = t.evaluate()
    records = RolloutStore(tmp_path / "rollouts.jsonl").read()
    assert Counter(r.template for r in records) == {"close_ticket": 4, "refund_approved": 4, "refund_email": 4}
    assert {(r.phase, r.cell_id.split("::")[1], r.variant) for r in records} == {("base", "base", "")}
    assert "eval_loss" not in metrics and t.args.vllm_importance_sampling_correction is False


def test_a_base_top_up_is_pooled_with_the_first_sweep(tokenizer, tmp_path):
    """A second base sweep of one template lands in its own directory with its own run id, so collect() pools
    both sweeps instead of refusing duplicate episode ids."""
    trainer(tokenizer, tmp_path / "base", phase="base", run="smoke-base-A", base_samples=4).evaluate()
    top_up = BaseTopUp(("close_ticket",), 6, 12345)
    t = trainer(tokenizer, tmp_path / "base" / "topup-B", phase="base", run="smoke-base-B-topup", top_up=top_up)
    t.evaluate()
    assert t.args.seed == 12345
    records = collect(tmp_path)
    assert Counter((r.run_id, r.template) for r in records) == {
        ("smoke-base-A", "close_ticket"): 4,
        ("smoke-base-A", "refund_approved"): 4,
        ("smoke-base-A", "refund_email"): 4,
        ("smoke-base-B-topup", "close_ticket"): 6,
    }


def test_resume_continues_from_the_checkpoint_under_a_new_attempt(tokenizer, tmp_path):
    """Finding #36: a crashed run restarts from its last checkpoint; the records of the steps it repeats are
    dropped, and the new attempt's episode ids cannot collide with the old ones."""
    saving = {"save_strategy": "steps", "save_steps": 1}
    first = trainer(tokenizer, tmp_path, grpo={**saving, "max_steps": 1})
    first.train()
    store = RolloutStore(tmp_path / "rollouts.jsonl")
    store.append([store.read()[0].model_copy(update={"step": 1, "sample": 99})])  # step 1 began, then a crash
    resumed = prepare_run_dir(tmp_path.parent, tmp_path, attempt="B", resume=True, overwrite=False)
    assert resumed is not None and (resumed.checkpoint.name, resumed.dropped) == ("checkpoint-1", 1)
    second = trainer(tokenizer, tmp_path, run="smoke-pure_strict-s0-B", grpo=saving)
    second.train(resume_from_checkpoint=str(resumed.checkpoint))
    assert second.state.global_step == 2 and training_problem(second, store) is None
    assert [(r.run_id[-1], r.step) for r in store.read()] == [("A", 0)] * 4 + [("B", 1)] * 4
