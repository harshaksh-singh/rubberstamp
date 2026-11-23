"""Pre-flight for the GPU runbook with the real Qwen tokenizers, offline from the local Hugging Face cache
(tokenizer files only, no weights; skipped when they are not cached):

- the chat templates render the environment's tool schemas, TRL's `parse_response` turns a Qwen tool call
  back into an environment call, and a malformed call ends the rollout as a parse failure (toy world);
- the helpdesk world's real prompts and golden paths fit the configured context (rs-world's 20 tasks and
  oracles through preflight.py), and the old sizes did not.
"""

import pytest
from rs_train.config import load_config
from rs_train.env import EnvOptions, episode_for, make_env_factory
from rs_train.preflight import Limits, measure, problems, summary
from rs_train.scripted import Script, play, tool_names
from rs_train.toy import toy_world
from rs_train.train import grpo_kwargs
from rstrain_helpers import CONFIGS, load_cached_tokenizer
from trl.trainer.grpo_config import GRPOConfig

MODELS = {"Qwen/Qwen3-1.7B": {"enable_thinking": False}, "Qwen/Qwen3-4B-Instruct-2507": {}}


@pytest.mark.slow
@pytest.mark.parametrize("model", sorted(MODELS))
def test_qwen_template_renders_the_tools_and_round_trips_a_call(model):
    from trl.chat_template_utils import (
        add_response_schema,
        get_training_chat_template,
        is_chat_template_prefix_preserving,
        parse_response,
        supports_tool_calling,
    )

    tokenizer = add_response_schema(load_cached_tokenizer(model))
    assert supports_tool_calling(tokenizer)
    if not is_chat_template_prefix_preserving(tokenizer):  # Qwen3-1.7B: TRL swaps in its training template
        assert get_training_chat_template(tokenizer)

    world, cfg = toy_world(), load_config(CONFIGS / "smoke.yaml")
    env = make_env_factory(world.executor, world.tasks.values())()
    tools = [getattr(env, name) for name in tool_names(env)]
    messages = [
        {"role": "system", "content": cfg.system_prompt},
        {"role": "user", "content": world.tasks["refund_approved"].prompt},
    ]
    text = tokenizer.apply_chat_template(
        messages, tools=tools, add_generation_prompt=True, tokenize=False, **MODELS[model]
    )
    assert all(f'"name": "{name}"' in text for name in tool_names(env))
    encoded = tokenizer.apply_chat_template(messages, tools=tools, add_generation_prompt=True, **MODELS[model])
    prompt_ids = list(encoded["input_ids"])

    def parse(emitted: str):
        return parse_response(
            tokenizer, tokenizer.encode(emitted, add_special_tokens=False), prefix=prompt_ids, tools=tools
        )

    message = parse(
        '<tool_call>\n{"name": "request_approval", "arguments": {"order_id": "o2"}}\n</tool_call><|im_end|>'
    )
    call = message["tool_calls"][0]["function"]
    env.reset(task_id="refund_approved")
    assert getattr(env, call["name"])(**call["arguments"]) == '{"approval_id": "ap1"}'
    assert [(c.tool, c.ok) for c in env.trajectory.calls] == [("request_approval", True)]

    # Finding #1: one missing brace and TRL falls back to plain text; the rollout must not end on an "answer".
    broken = parse('<tool_call>\n{"name": "request_approval", "arguments": {"order_id": "o2"}\n</tool_call><|im_end|>')
    assert not broken.get("tool_calls") and "<tool_call>" in broken["content"]
    env.reset(task_id="refund_approved")
    episode = episode_for(env, [broken])
    assert episode.trajectory.stop == "error" and episode.trajectory.final_answer is None


def helpdesk():
    """rs-world's 20 tasks and their oracles' golden plans (the summary doubles as the final answer)."""
    from rs_world.executor import WorldExecutor
    from rs_world.tasks import golden, load_task, task_ids

    tasks = [load_task(task_id) for task_id in task_ids()]
    plans = {task.id: golden(task) for task in tasks}
    scripts = {
        task_id: Script(
            name="golden", calls=tuple((tool, dict(args)) for tool, args in plan.calls), final_answer=plan.answer
        )
        for task_id, plan in plans.items()
    }
    return WorldExecutor(), tasks, scripts


# Before this change: research/64's "prompt <= 1.5k" sizes, and the 300 x 4-character tool-response cap.
OLD = {
    "smoke": Limits(max_completion_length=1024, max_tool_calling_iterations=6, vllm_max_model_length=2560),
    "main": Limits(max_completion_length=3072, max_tool_calling_iterations=12, vllm_max_model_length=4608),
}


@pytest.mark.slow
@pytest.mark.parametrize("name", ["smoke", "main"])
def test_helpdesk_prompts_and_golden_paths_fit_the_configured_context(name, tmp_path):
    """Findings #3/#33/#34: every helpdesk prompt (2.56-2.65k tokens) was over smoke's whole 2,560-token
    context, and main left honest paths about 2k tokens."""
    cfg = load_config(CONFIGS / f"{name}.yaml")
    tokenizer = load_cached_tokenizer(cfg.model)
    executor, tasks, scripts = helpdesk()
    args = GRPOConfig(**grpo_kwargs(cfg, tmp_path, 0, [1.0]))
    fits = measure(
        tokenizer,
        executor=executor,
        tasks=tasks,
        system_prompt=cfg.system_prompt,
        golden=scripts,
        options=cfg.env_options(),
        chat_template_kwargs=args.chat_template_kwargs,
    )
    limits = Limits(
        max_completion_length=args.max_completion_length,
        max_tool_calling_iterations=args.max_tool_calling_iterations,
        vllm_max_model_length=args.vllm_max_model_length,
        margin=cfg.golden_margin,
    )
    assert problems(fits, limits) == []
    measured = summary(fits)
    assert measured["tasks"] == 20 and 2500 < measured["prompt_tokens_min_median_max"][2] < 2700
    assert measured["golden_calls_max"] == 7 and measured["longest_tool_tokens"] < cfg.env["max_response_tokens"]
    assert problems(fits, OLD[name].model_copy(update={"margin": cfg.golden_margin}))


def test_the_old_response_cap_cut_a_golden_helpdesk_response():
    """Finding #34: 300 tokens x 4 characters = 1,200 characters cut t05's list_tickets (1,329 characters)."""
    executor, tasks, scripts = helpdesk()
    cut = {}
    for options in (
        EnvOptions(max_response_tokens=300, chars_per_token=4),
        load_config(CONFIGS / "main.yaml").env_options(),
    ):
        env = make_env_factory(executor, tasks, options)()
        cut[options.max_response_tokens] = []
        for task in tasks:
            env.reset(task_id=task.id)
            replies = [m["content"] for m in play(env, scripts[task.id]) if m["role"] == "tool"]
            cut[options.max_response_tokens] += [task.id for r in replies if "...[truncated" in r]
    assert cut == {300: ["t05_sla_breach_escalation"], 768: []}
