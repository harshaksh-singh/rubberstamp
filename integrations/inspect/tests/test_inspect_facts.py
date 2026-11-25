"""Facts about inspect-ai 0.3.292 this integration relies on (the plan's UNVERIFIED items), checked on the pinned
package rather than assumed."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, ModelUsage, get_model
from inspect_ai.scorer import Score, Scorer, Target, grouped, mean, scorer
from inspect_ai.solver import TaskState


def test_the_pinned_version() -> None:
    assert version("inspect-ai") == "0.3.292"


@scorer(metrics={"a": [grouped(mean(), "group")], "b": [mean()]})
def _dict_scorer() -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        group = state.metadata["group"]
        return Score(value={"a": 1.0 if group == "x" else 0.0, "b": 0.5})

    return score


def test_grouped_works_inside_dict_metrics(tmp_path: Path) -> None:
    """UNVERIFIED item: `grouped()` inside a dict of metrics. Inspect hands each key's metrics SampleScores whose
    value is that key's scalar and whose sample metadata is intact, so grouped() splits by metadata as usual."""
    dataset = [Sample(id=i, input="hi", metadata={"group": g}) for i, g in enumerate(["x", "x", "y"])]
    task = Task(dataset=dataset, scorer=_dict_scorer())

    def reply(*args: object) -> ModelOutput:  # with usage, so mockllm does not estimate it with tiktoken
        out = ModelOutput.from_content("mockllm/model", "hello")
        out.usage = ModelUsage(input_tokens=0, output_tokens=0, total_tokens=0)
        return out

    model = get_model("mockllm/model", custom_outputs=reply, memoize=False)
    log = inspect_eval(task, model=model, log_dir=str(tmp_path), display="none")[0]
    assert log.results is not None
    by_name = {s.name: {k: m.value for k, m in s.metrics.items()} for s in log.results.scores}
    assert by_name["a"] == {"grouped_x": 1.0, "grouped_y": 0.0, "grouped_all": 2 / 3}
    assert by_name["b"] == {"mean": 0.5}


def test_vllm_server_args_pass_through_as_flags() -> None:
    """UNVERIFIED item: the vLLM tool-parser `-M` names. Inspect's vllm provider does not define them: every `-M` it
    does not know is passed to `vllm serve` as `--<name with dashes>` (a true bool as a bare flag), so the names are
    vLLM's own: `-M enable_auto_tool_choice=true -M tool_call_parser=<parser>`."""
    from inspect_ai._util.local_server import _server_arg_to_cli_key  # the conversion the provider uses

    assert _server_arg_to_cli_key("tool_call_parser") == "tool-call-parser"
    assert _server_arg_to_cli_key("enable_auto_tool_choice") == "enable-auto-tool-choice"
