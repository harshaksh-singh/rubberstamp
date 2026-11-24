"""LoopholeEnv: a TRL `environment_factory` environment over any rs_core Executor.

The contract, read from the installed trl 1.18.2 (`trl/trainer/grpo_trainer.py`):

- `GRPOTrainer.__init__` calls the zero-argument factory once to probe the class. It requires a callable
  `reset`; every other public *function on the class* becomes a tool
  (`inspect.getmembers(type(env), inspect.isfunction)`, names starting with `_` and `reset`/`get_reward`
  excluded). Helpers here are therefore private methods or properties, which are never exposed.
- `_generate_and_score_completions` draws one pooled instance per rollout (instances are reused, so
  `reset` must restore everything) and calls `reset(**row)` with the whole dataset row, `prompt` included.
  A returned string is appended to the last user message; we return None because the row holds the prompt.
- `_tool_call_loop` calls `method(**arguments)` and sends `str(result)` back as the tool message. An
  exception becomes `{"error": str(e)}` and counts in the `tools/failure_frequency` metric.
- A tool call TRL cannot parse (`parse_response` falls back to plain text, chat_template_utils.py) never
  reaches the loop and is counted nowhere: the turn just ends the rollout with the markup as its text.
  `ending` therefore treats a last turn that still holds tool-call markup as a parse failure.
- The loop also ends with a turn whose tool calls never ran (`max_tool_calling_iterations`, or the next
  tool output would overflow the token budget): that turn is narration, not an answer.
- `_calculate_rewards` passes the instances to every reward function as `environments=`, aligned with
  `completions`; `episode_for(env, completion)` turns one of them into a rs_core Episode.

The Executor protocol only offers `run(task, calls)` from a fresh reset, so each tool call replays the
calls so far plus the new one. That is O(n^2) replays for n calls, which is nothing at n <= 12, and it
makes every state the agent saw reproducible from the trajectory alone.
"""

from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from rs_core.diff import digest, snapshot_digest
from rs_core.models import Episode, Snapshot, Task, ToolCall, ToolSpec, Trajectory
from rs_core.protocols import Executor
from rs_suite.detectors import AnswerChannel as AnswerChannel  # moved to rs-suite, re-exported here
from rs_suite.detectors import channel_answer as channel_answer

RESERVED = frozenset({"reset", "get_reward"})
REJECTED = "rejected: "  # error prefix of malformed calls, which never reached the world
TOOL_CALL_MARKER = "<tool_call>"  # Qwen's tag; TRL's parse_response leaves it in the text of a malformed call
TRUNCATED = " ...[truncated "  # what `_cap` appends to a tool response it cut
Stop = Literal["done", "max_steps", "error", "budget"]
JSON_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass(frozen=True)
class EnvOptions:
    max_response_tokens: int = 300  # recipe: cap each tool response at about 300 tokens
    # Characters per token of the densest tool output measured: 1.88 for the helpdesk world's JSON with the
    # Qwen3 tokenizers (rs-world golden runs). Below that, the cap holds in tokens without a tokenizer here.
    chars_per_token: float = 1.8
    task_key: str = "task_id"  # dataset column that names the task
    agent_id: str = "policy"
    raise_on_world_error: bool = False  # True: world errors also count in tools/failure_frequency
    tool_call_marker: str = TOOL_CALL_MARKER
    answer: AnswerChannel | None = None  # set from World.answer by train.env_factory


def tool_params(spec: ToolSpec) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    """(properties, required) from a ToolSpec, whose `params` is either a JSON object schema or a
    plain {argument: schema} map (the toy world's form, every argument required)."""
    params = spec.params
    if params.get("type") == "object" and isinstance(params.get("properties"), dict):
        return dict(params["properties"]), tuple(params.get("required", ()))
    return dict(params), tuple(params)


def argument_problem(spec: ToolSpec, args: dict[str, Any]) -> str | None:
    """Why `args` do not fit the tool's schema, or None. Malformed calls are format failures."""
    props, required = tool_params(spec)
    if missing := sorted(set(required) - set(args)):
        return f"missing argument(s) {missing}"
    if unknown := sorted(set(args) - set(props)):
        return f"unknown argument(s) {unknown}"
    for name, value in args.items():
        kind = props[name].get("type")
        want = JSON_TYPES.get(kind) if isinstance(kind, str) else None
        if want is None or value is None:
            continue
        if want is float and isinstance(value, int) and not isinstance(value, bool):
            continue
        if not isinstance(value, want) or (want is int and isinstance(value, bool)):
            return f"argument {name!r} should be a JSON {kind}"
    return None


class LoopholeEnv:
    """One rollout's view of a world. Subclasses built by `env_class` add one public method per tool."""

    def __init__(self, executor: Executor, tasks: Iterable[Task], options: EnvOptions | None = None) -> None:
        self._executor = executor
        self._tasks = {task.id: task for task in tasks}
        self._options = options or EnvOptions()
        self._specs = {spec.name: spec for spec in executor.tools}
        self._world_calls: list[ToolCall] = []  # well-formed calls, replayed on the world
        self._records: list[ToolCall] = []  # every call as the agent made it (the trajectory)
        self._last: Episode | None = None  # replay of `_world_calls`
        self._replay_ok = True

    def reset(self, **row: Any) -> str | None:
        """Start a fresh episode for the task named in the dataset row."""
        task_id = row.get(self._options.task_key)
        if task_id not in self._tasks:
            raise KeyError(f"reset: unknown task {task_id!r} (column {self._options.task_key!r})")
        task = self._tasks[task_id]
        if missing := sorted(set(task.tools) - set(self._own_tools())):
            raise ValueError(f"reset: task {task_id!r} needs tools this environment lacks: {missing}")
        self._world_calls, self._records, self._replay_ok = [], [], True
        self.post_claim_calls = 0
        self._last = self._executor.run(task, [])
        return None

    # ------------------------------------------------------------ read-only views (never tools)

    @property
    def task(self) -> Task:
        return self._require().task

    @property
    def state(self) -> Snapshot:
        return self._require().final

    @property
    def initial_state(self) -> Snapshot:
        return self._require().initial

    @property
    def state_digest(self) -> str:
        return snapshot_digest(self.state, self._executor.schema)

    @property
    def trajectory(self) -> Trajectory:
        return self._trajectory(None, "done")

    @property
    def replay_consistent(self) -> bool:
        """False if replaying earlier calls ever gave different results (a non-deterministic world)."""
        return self._replay_ok

    @property
    def options(self) -> EnvOptions:
        return self._options

    # ------------------------------------------------------------ internals

    def _require(self) -> Episode:
        if self._last is None:
            raise RuntimeError("call reset() before using the environment")
        return self._last

    def _own_tools(self) -> list[str]:
        return [name for name, _ in inspect.getmembers(type(self), inspect.isfunction) if name in self._specs]

    def _claimed(self) -> str | None:
        """The answer channel's tool once a call to it has succeeded (the episode is over), else None."""
        channel = self._options.answer
        if channel is not None and any(r.ok and r.tool == channel.tool for r in self._records):
            return channel.tool
        return None

    def _call(self, tool: str, args: dict[str, Any]) -> str:
        last = self._require()
        if claim := self._claimed():
            # The episode ends at the claim, as the pre-training audit assumes (post_claim_steps=False). TRL keeps
            # the tool loop running while the model calls tools, so later calls are refused here: they never reach
            # the world and stay out of the graded trajectory; TRL counts them as tool failures.
            self.post_claim_calls += 1
            raise TypeError(f"{tool}: the episode ended at {claim}; this call was not run")
        if tool not in last.task.tools:
            problem: str | None = f"tool {tool!r} is not available for this task"
        else:
            problem = argument_problem(self._specs[tool], args)
        if problem is not None:  # never touches the world; TRL reports it as a tool failure
            rejected = ToolCall(idx=len(self._records), tool=tool, args=args, ok=False, error=REJECTED + problem)
            self._records.append(rejected)
            raise TypeError(f"{tool}: {problem}")
        request = ToolCall(idx=len(self._world_calls), tool=tool, args=args)
        episode = self._executor.run(last.task, [*self._world_calls, request])
        self._replay_ok &= episode.trajectory.calls[:-1] == last.trajectory.calls
        self._world_calls.append(request)
        self._last = episode
        record = episode.trajectory.calls[-1]
        self._records.append(record.model_copy(update={"idx": len(self._records)}))
        if not record.ok and self._options.raise_on_world_error:
            raise RuntimeError(record.error)
        payload = record.result if record.ok else {"error": record.error}
        return self._cap(json.dumps(payload, ensure_ascii=False, default=str))

    def _cap(self, text: str) -> str:
        limit = int(self._options.max_response_tokens * self._options.chars_per_token)
        if len(text) <= limit:
            return text
        return f"{text[:limit]}{TRUNCATED}{len(text) - limit} chars]"

    def _trajectory(self, final_answer: str | None, stop: Stop) -> Trajectory:
        task = self._require().task
        return Trajectory(
            task_id=task.id,
            agent_id=self._options.agent_id,
            calls=tuple(self._records),
            final_answer=final_answer,
            stop=stop,
        )

    def _episode(self, final_answer: str | None, stop: Stop) -> Episode:
        last = self._require()
        return last.model_copy(update={"trajectory": self._trajectory(final_answer, stop)})


# ---------------------------------------------------------------- tools as methods


def _docstring(spec: ToolSpec) -> str:
    """Google style, which `transformers.utils.get_json_schema` parses into the tool schema."""
    props, _ = tool_params(spec)
    lines = [spec.description.strip() or spec.name, "", "Args:"]
    for name, schema in props.items():
        text = str(schema.get("description") or f"The {name.replace('_', ' ')}.").replace("\n", " ")
        if "enum" in schema:
            text += f" (choices: {json.dumps(schema['enum'])})"
        lines.append(f"    {name}: {text}")
    lines += ["", "Returns:", "    The result as JSON, or an error message."]
    return "\n".join(lines if props else [lines[0], *lines[-3:]])


def _tool_method(spec: ToolSpec) -> Callable[..., str]:
    """A method named after the tool, with a real signature and docstring for the schema.

    The body takes **kwargs so that malformed calls reach `_call` and get recorded; the
    `__signature__` is only metadata, so Python does not enforce it at call time.
    """
    props, required = tool_params(spec)
    param = inspect.Parameter
    params = [param("self", param.POSITIONAL_OR_KEYWORD)]
    annotations: dict[str, Any] = {}
    for name, schema in props.items():
        kind = schema.get("type")
        hint: Any = JSON_TYPES.get(kind, str) if isinstance(kind, str) else str
        if name not in required:
            hint = hint | None
        annotations[name] = hint
        default = param.empty if name in required else None
        params.append(param(name, param.KEYWORD_ONLY, annotation=hint, default=default))
    annotations["return"] = str

    def method(self: LoopholeEnv, **kwargs: Any) -> str:
        return self._call(spec.name, kwargs)

    method.__name__ = method.__qualname__ = spec.name
    method.__doc__ = _docstring(spec)
    method.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    method.__annotations__ = annotations
    return method


_CLASSES: dict[str, type[LoopholeEnv]] = {}


def env_class(tools: Sequence[ToolSpec]) -> type[LoopholeEnv]:
    """The LoopholeEnv subclass whose public methods are exactly `tools` (cached per tool set)."""
    key = digest([spec.model_dump(mode="json") for spec in tools])
    if key not in _CLASSES:
        names = [spec.name for spec in tools]
        taken = RESERVED | {name for name in dir(LoopholeEnv) if not name.startswith("__")}
        bad = [n for n in names if not n.isidentifier() or n.startswith("_") or n in taken or names.count(n) > 1]
        if bad:
            raise ValueError(f"tool names cannot become methods: {bad}")
        _CLASSES[key] = type("LoopholeToolEnv", (LoopholeEnv,), {spec.name: _tool_method(spec) for spec in tools})
    return _CLASSES[key]


def environment_name(task: Task) -> str:
    """Name of the environment exposing exactly this task's tools (multi-environment mode)."""
    return "tools_" + digest(sorted(task.tools))[:10]


def build_env(
    executor: Executor, tasks: Sequence[Task], options: EnvOptions | None = None, tools: Sequence[str] | None = None
) -> LoopholeEnv:
    wanted = set(tools) if tools is not None else {name for task in tasks for name in task.tools}
    if unknown := sorted(wanted - {spec.name for spec in executor.tools}):
        raise ValueError(f"tasks use tools the executor does not have: {unknown}")
    return env_class([spec for spec in executor.tools if spec.name in wanted])(executor, tasks, options)


def make_env_factory(
    executor: Executor, tasks: Iterable[Task], options: EnvOptions | None = None
) -> Callable[[], LoopholeEnv] | dict[str, Callable[[], LoopholeEnv]]:
    """`environment_factory` for GRPOTrainer.

    One zero-argument factory when every task uses the same tools. Otherwise a dict keyed by
    `environment_name(task)`, so TRL shows each prompt only its task's tools; the dataset then needs
    an `environment` column (train.build_dataset adds it).
    """
    tasks = tuple(tasks)
    groups = {environment_name(t): tuple(sorted(t.tools)) for t in tasks}
    if len(groups) == 1:
        return functools.partial(build_env, executor, tasks, options, None)
    return {name: functools.partial(build_env, executor, tasks, options, tools) for name, tools in groups.items()}


# ---------------------------------------------------------------- completions -> episodes


def _text(content: Any) -> str | None:
    if content is None or isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


@dataclass(frozen=True)
class Ending:
    """How a rollout ended, read from its last assistant turn (a TRL completion is a list of messages)."""

    stop: Stop  # "done", "max_steps" (cut off with tool calls pending) or "error" (unparsed tool call)
    text: str | None  # that turn's text

    @property
    def answer(self) -> str | None:
        """The turn's text when it ended the rollout cleanly; a cut-off or unparsed turn is not an answer."""
        return self.text if self.stop == "done" else None


def ending(completion: Any, marker: str = TOOL_CALL_MARKER) -> Ending:
    """How the rollout ended (see the module docstring for why each case is what it is)."""
    if isinstance(completion, str):
        last: dict[str, Any] | None = {"role": "assistant", "content": completion}
    else:
        last = next((m for m in reversed(completion or []) if m.get("role") == "assistant"), None)
    if last is None:
        return Ending("done", None)
    text = _text(last.get("content"))
    if last.get("tool_calls"):
        return Ending("max_steps", text)
    if marker and marker in (text or ""):
        return Ending("error", text)
    return Ending("done", text)


def episode_for(env: LoopholeEnv, completion: Any) -> Episode:
    """The rs_core Episode of one finished rollout, as reward functions see it."""
    end = ending(completion, env.options.tool_call_marker)
    channel = env.options.answer
    answer = channel_answer(env._records, channel) if channel is not None else end.answer
    return env._episode(answer, end.stop)
