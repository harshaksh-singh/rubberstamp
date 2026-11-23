"""Pre-flight: does every task fit the context the config pays for? Plain inputs, so it runs on any world.

TRL renders a prompt (grpo_trainer.py, `_tokenize_prompts`) as `apply_chat_template([system, user],
tools=<the environment's tool methods>, chat_template=<TRL's training template when the tokenizer's own is
not prefix-preserving>, add_generation_prompt=True, **chat_template_kwargs)`. The completion is every
assistant turn plus every tool message, and the tool loop gives up on a rollout when prompt + completion
reaches `vllm_max_model_length`, when the completion passes `max_completion_length`, or after
`max_tool_calling_iterations` rounds of calls. So a task whose honest path does not fit is lost for
reasons that have nothing to do with the verifier, while its shorter shortcuts still fit.

`measure` renders each task's real prompt and its golden trajectory (played through the environment, so the
tool messages are the capped strings the model would read) with the model's tokenizer. `problems` checks:

- longest prompt + max_completion_length < vllm_max_model_length (TRL calls a sequence overlong at >=);
- golden completion tokens x margin <= max_completion_length, golden calls x margin <= the iteration cap;
- no golden tool response is cut by the environment's response cap (an honest path must see all its data).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from rs_core.models import Frozen, Task
from rs_core.protocols import Executor

from .env import TRUNCATED, EnvOptions, environment_name, make_env_factory
from .scripted import Script, play, tool_names


class TaskFit(Frozen):
    task_id: str
    prompt_tokens: int
    golden_tokens: int | None = None  # completion tokens of the golden trajectory (no narration)
    golden_calls: int | None = None
    longest_tool_tokens: int | None = None
    cut_tool_responses: int = 0  # golden tool responses the environment truncated


class Limits(Frozen):
    max_completion_length: int
    max_tool_calling_iterations: int | None = None  # None: unlimited
    vllm_max_model_length: int | None = None  # None: not using vLLM, or vLLM's model default
    margin: float = 1.5


def training_template(tokenizer: Any) -> tuple[Any, str | None]:
    """The tokenizer and chat template GRPOTrainer uses when it has tools."""
    from trl.chat_template_utils import (
        add_response_schema,
        get_training_chat_template,
        is_chat_template_prefix_preserving,
    )

    tokenizer = add_response_schema(tokenizer)
    return tokenizer, None if is_chat_template_prefix_preserving(tokenizer) else get_training_chat_template(tokenizer)


def measure(
    tokenizer: Any,
    *,
    executor: Executor,
    tasks: Sequence[Task],
    system_prompt: str,
    golden: Mapping[str, Script],
    options: EnvOptions | None = None,
    chat_template_kwargs: Mapping[str, Any] | None = None,
) -> list[TaskFit]:
    tokenizer, template = training_template(tokenizer)
    kwargs = dict(chat_template_kwargs or {})
    factory = make_env_factory(executor, tasks, options)

    def tokens(messages: list[dict[str, Any]], tools: list[Any], prompt: bool) -> int:
        rendered = tokenizer.apply_chat_template(
            conversation=[messages],
            tools=tools or None,
            chat_template=template,
            add_generation_prompt=prompt,
            tokenize=True,
            return_dict=True,
            **kwargs,
        )
        return len(rendered["input_ids"][0])

    fits = []
    for task in tasks:
        env = factory[environment_name(task)]() if isinstance(factory, dict) else factory()
        tools = [getattr(env, name) for name in tool_names(env)]  # what TRL puts in this task's prompt
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": task.prompt}]
        fit = TaskFit(task_id=task.id, prompt_tokens=tokens(messages, tools, True))
        if (script := golden.get(task.id)) is not None:
            env.reset(task_id=task.id)
            completion = play(env, script)
            replies = [m["content"] for m in completion if m["role"] == "tool"]
            sizes = [len(tokenizer.encode(r, add_special_tokens=False)) for r in replies]
            fit = fit.model_copy(
                update={
                    "golden_tokens": tokens(messages + completion, tools, False) - fit.prompt_tokens,
                    "golden_calls": len(script.calls),
                    "longest_tool_tokens": max(sizes, default=0),
                    "cut_tool_responses": sum(TRUNCATED in r for r in replies),
                }
            )
        fits.append(fit)
    return fits


def problems(fits: Sequence[TaskFit], limits: Limits) -> list[str]:
    found = []
    budget, rounds = limits.max_completion_length, limits.max_tool_calling_iterations
    context = limits.vllm_max_model_length
    longest = max((f.prompt_tokens for f in fits), default=0)
    if context is not None and longest + budget >= context:
        found.append(
            f"prompts reach {longest} tokens, and {longest} + max_completion_length {budget} does not fit "
            f"vllm_max_model_length {context}"
        )
    for f in fits:
        if f.golden_tokens is not None and math.ceil(f.golden_tokens * limits.margin) > budget:
            found.append(
                f"{f.task_id}: golden path of {f.golden_tokens} tokens x {limits.margin} > max_completion_length "
                f"{budget}"
            )
        if f.golden_calls is not None and rounds is not None and math.ceil(f.golden_calls * limits.margin) > rounds:
            found.append(
                f"{f.task_id}: golden path of {f.golden_calls} calls x {limits.margin} > max_tool_calling_iterations "
                f"{rounds}"
            )
        if f.cut_tool_responses:
            found.append(f"{f.task_id}: {f.cut_tool_responses} golden tool response(s) cut by the response cap")
    return found


def summary(fits: Sequence[TaskFit]) -> dict[str, Any]:
    def spread(values: list[int]) -> list[int] | None:
        return [min(values), sorted(values)[len(values) // 2], max(values)] if values else None

    return {
        "tasks": len(fits),
        "prompt_tokens_min_median_max": spread([f.prompt_tokens for f in fits]),
        "golden_tokens_min_median_max": spread([f.golden_tokens for f in fits if f.golden_tokens is not None]),
        "golden_calls_max": max((f.golden_calls or 0 for f in fits), default=0),
        "longest_tool_tokens": max((f.longest_tool_tokens or 0 for f in fits), default=0),
    }
