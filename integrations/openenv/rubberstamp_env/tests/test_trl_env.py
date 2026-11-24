"""TRL 1.18.2 drives the server through `environment_factory` (the `trl` extra; no GPU, no training step).

- TRL's probe of the class (public functions are tools, `reset` and `get_reward` are special) finds the 16 tools, and
  transformers' `get_json_schema` (what TRL's chat template rendering calls) reads each tool method.
- Scripted golden, shortcut and no-op episodes on 25 instances (every task, one seed) through the tool methods earn
  the in-process reference reward (`rs_suite.conformance.expected`: `WorldExecutor.run` plus `verdicts`).
- A call after the claim raises (TRL counts a tool failure), an unfinished rollout is regraded by the server, and an
  infrastructure failure makes `get_reward` return None (TRL masks the rollout).
- A real `GRPOTrainer` built with `environment_factory=RemoteHelpdeskEnv` on a tiny random Qwen3 (the Qwen3-1.7B
  tokenizer from the local Hugging Face cache, else skipped) registers the environment's reward and tools, and its
  registered reward function scores a scripted episode on a pooled instance.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

pytest.importorskip("trl", reason="needs the trl extra: uv sync --extra trl")

from rs_suite.conformance import PLANS, expected, instance, instances, plan_for  # noqa: E402
from rs_suite.instances import replay  # noqa: E402
from rs_world import REGISTRY  # noqa: E402

import rubberstamp_env.server.grading as grading  # noqa: E402
from rubberstamp_env.trl_env import URL_ENV, RemoteHelpdeskEnv, helpdesk_rows  # noqa: E402

pytestmark = pytest.mark.trl
TOOL_NAMES = sorted(REGISTRY.names())
T01 = instance("t01_order_status@42")


@pytest.fixture
def remote(server_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(URL_ENV, server_url)
    monkeypatch.setenv("TRL_EXPERIMENTAL_SILENCE", "1")
    return server_url


def play(env, plan) -> list[str]:
    return [getattr(env, tool)(**args) for tool, args in plan.calls]


def test_trl_probe_finds_reset_get_reward_and_the_16_tools(remote: str) -> None:
    from transformers.utils.chat_template_utils import get_json_schema

    members = dict(inspect.getmembers(RemoteHelpdeskEnv, predicate=inspect.isfunction))
    tools = sorted(name for name in members if name not in ("reset", "get_reward") and not name.startswith("_"))
    assert "reset" in members and "get_reward" in members and tools == TOOL_NAMES
    env = RemoteHelpdeskEnv()
    for spec in REGISTRY.specs():
        schema = get_json_schema(getattr(env, spec.name))["function"]
        assert schema["name"] == spec.name
        assert set(schema["parameters"]["properties"]) == set(spec.params.get("properties", {}))


def test_scripted_episodes_earn_the_reference_reward(remote: str) -> None:
    env = RemoteHelpdeskEnv()
    for inst in instances()[5::20]:  # the 25 tasks on world seed 5
        for name in PLANS:
            prompt = env.reset(instance_id=inst.id)  # no prompt column: reset returns the server's prompt
            assert isinstance(prompt, str) and prompt
            outputs = play(env, plan_for(inst, name))
            assert all(isinstance(text, str) for text in outputs)
            assert env.get_reward() == expected(inst, name).rewards["strict"], (inst.id, name)
            assert env.last_metadata["reward_variant"] == "strict"
    assert env.reset(instance_id=T01.id, prompt=[{"role": "user", "content": "..."}]) is None  # row has a prompt


def test_calls_after_the_claim_raise_and_unfinished_rollouts_are_regraded(remote: str) -> None:
    env = RemoteHelpdeskEnv()
    golden = plan_for(T01, "golden")
    env.reset(instance_id=T01.id)
    play(env, golden)
    with pytest.raises(RuntimeError, match="not run"):
        env.get_order(order_id="ORD-00001")  # pyright: ignore[reportAttributeAccessIssue]
    assert env.get_reward() == 1.0
    env.reset(instance_id=T01.id)
    for tool, args in golden.calls[:-1]:  # stop before the claim
        getattr(env, tool)(**args)
    assert env.get_reward() == 0.0 and env.last_metadata["submitted"] is False


def test_an_infrastructure_failure_masks_the_rollout(remote: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def tampered(task_id, seed, calls, final_answer=None, *, live_digest=None):
        return replay(task_id, seed, calls, final_answer, live_digest="0" * 64 if live_digest else None)

    monkeypatch.setattr(grading, "replay", tampered)
    env = RemoteHelpdeskEnv()
    env.reset(instance_id=T01.id)
    with pytest.raises(RuntimeError, match="replay ends at"):
        play(env, plan_for(T01, "golden"))
    assert env.get_reward() is None  # TRL turns None into NaN: the rollout is masked, not scored 0


def test_helpdesk_rows() -> None:
    rows = helpdesk_rows("dev")
    assert len(rows) == 80 and rows[0] == {"instance_id": instances("dev")[0].id}
    assert helpdesk_rows("dev", with_prompt=True)[0]["prompt"][0]["role"] == "user"


def test_grpo_trainer_registers_and_scores_the_environment(remote: str, tmp_path) -> None:
    from huggingface_hub import try_to_load_from_cache

    if not isinstance(try_to_load_from_cache("Qwen/Qwen3-1.7B", "tokenizer_config.json"), str):
        pytest.skip("the Qwen/Qwen3-1.7B tokenizer is not in the local Hugging Face cache")
    from datasets import Dataset
    from transformers import AutoTokenizer, Qwen3Config, Qwen3ForCausalLM
    from trl.trainer.grpo_config import GRPOConfig
    from trl.trainer.grpo_trainer import GRPOTrainer

    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-1.7B", local_files_only=True)
    model = Qwen3ForCausalLM(
        Qwen3Config(
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
    )
    args = GRPOConfig(
        output_dir=str(tmp_path),
        use_cpu=True,
        bf16=False,
        report_to=[],
        num_generations=2,
        per_device_train_batch_size=2,
        max_completion_length=16,
        max_steps=1,
        save_strategy="no",
    )
    trainer = GRPOTrainer(
        model=model,
        args=args,
        train_dataset=Dataset.from_list(helpdesk_rows("train")[:4]),
        processing_class=tokenizer,
        environment_factory=RemoteHelpdeskEnv,
    )
    assert trainer.reward_func_names == ["RemoteHelpdeskEnv"]  # get_reward is the only reward source
    assert sorted(tool.__name__ for tool in trainer._env_tools[None]) == TOOL_NAMES
    env = trainer._environment_pool[None][0]
    reward: Any = trainer.reward_funcs[0]
    for name, want in (("golden", 1.0), ("shortcut", 0.0)):
        env.reset(instance_id=T01.id)
        play(env, plan_for(T01, name))
        assert reward(environments=[env], prompts=[""], completions=[""], completion_ids=[[]]) == [want]
