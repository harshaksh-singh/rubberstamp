"""Opt-in pre-flight for the GPU runbook: the real Qwen chat templates render the environment's tool
schemas, and TRL's `parse_response` turns a Qwen-format tool call back into an environment call.

Runs offline from the local Hugging Face cache (tokenizer files only, no weights) and is skipped when
the tokenizers are not cached. To enable it once: `AutoTokenizer.from_pretrained("<model>")`.
"""

import pytest
from wf_train.config import load_config
from wf_train.env import make_env_factory
from wf_train.scripted import tool_names
from wf_train.toy import toy_world
from wftrain_helpers import CONFIGS

MODELS = {"Qwen/Qwen3-1.7B": {"enable_thinking": False}, "Qwen/Qwen3-4B-Instruct-2507": {}}


def load_cached_tokenizer(model: str):
    from huggingface_hub import try_to_load_from_cache
    from transformers import AutoTokenizer

    if not isinstance(try_to_load_from_cache(model, "tokenizer_config.json"), str):
        pytest.skip(f"{model} tokenizer is not in the local Hugging Face cache")
    return AutoTokenizer.from_pretrained(model, local_files_only=True)


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
    assert len(prompt_ids) < 1536  # recipe: system + tool schemas + task <= 1.5k tokens

    emitted = '<tool_call>\n{"name": "request_approval", "arguments": {"order_id": "o2"}}\n</tool_call><|im_end|>'
    message = parse_response(
        tokenizer, tokenizer.encode(emitted, add_special_tokens=False), prefix=prompt_ids, tools=tools
    )
    call = message["tool_calls"][0]["function"]
    env.reset(task_id="refund_approved")
    assert getattr(env, call["name"])(**call["arguments"]) == '{"approval_id": "ap1"}'
    assert [(c.tool, c.ok) for c in env.trajectory.calls] == [("request_approval", True)]
