"""LoopholeEnv: isolation, reset, call recording, response caps, how rollouts end, the answer channel and the
TRL discovery contract."""

import ast
import inspect
import json

import pytest
from rs_core.diff import snapshot_digest
from rs_core.models import Task, ToolSpec
from rs_core.testing.toyworld import INITIAL, SCHEMA, TASKS, TOOLS, ToyExecutor, golden_calls
from rs_train.design import make_variant
from rs_train.detectors import detect
from rs_train.env import (
    REJECTED,
    TRUNCATED,
    AnswerChannel,
    EnvOptions,
    ending,
    env_class,
    environment_name,
    episode_for,
    make_env_factory,
)
from rs_train.scripted import Script, play, tool_names
from rs_train.toy import SCRIPTS, toy_world
from rstrain_helpers import new_env
from transformers.utils import get_json_schema


def initial_digest(task_id: str) -> str:
    return snapshot_digest(ToyExecutor().initial(TASKS[task_id]), SCHEMA)


def test_two_instances_do_not_share_state():
    factory = make_env_factory(ToyExecutor(), TASKS.values())
    a, b = factory(), factory()
    a.reset(task_id="refund_email")
    b.reset(task_id="refund_email")
    a.create_refund(order_id="o1", amount_cents=5000)
    assert b.state_digest == initial_digest("refund_email") != a.state_digest
    assert len(a.trajectory.calls) == 1 and b.trajectory.calls == ()
    assert type(a) is type(b)  # one class per tool set


def test_reset_restores_the_initial_digest_and_is_reusable_across_tasks():
    env = new_env()
    row = {"task_id": "refund_approved", "prompt": [{"role": "user", "content": "..."}], "cell_id": "x"}
    assert env.reset(**row) is None  # TRL passes the whole row; None leaves the prompt alone
    play(env, SCRIPTS["refund_approved"]["golden"])
    assert env.state_digest != initial_digest("refund_approved")
    env.reset(task_id="refund_approved")
    assert env.state_digest == initial_digest("refund_approved") and env.trajectory.calls == ()
    env.reset(task_id="close_ticket")  # TRL pools instances and reuses them for other rows
    assert env.task.id == "close_ticket" and env.state_digest == initial_digest("close_ticket")
    with pytest.raises(KeyError, match="unknown task"):
        env.reset(task_id="nope")


def test_using_an_environment_before_reset_is_an_error():
    with pytest.raises(RuntimeError, match="reset"):
        new_env().create_refund(order_id="o1", amount_cents=1)


def test_every_call_is_recorded_with_its_real_result():
    env = new_env()
    env.reset(task_id="refund_email")
    completion = play(env, SCRIPTS["refund_email"]["golden"])
    episode = episode_for(env, completion)
    calls = episode.trajectory.calls
    assert [(c.idx, c.tool, c.ok) for c in calls] == [(0, "create_refund", True), (1, "send_email", True)]
    assert calls[0].result == {"refund_id": "r1"} and calls[1].args == {
        "customer_id": "c1",
        "subject": "Refund confirmed",
    }
    assert episode.trajectory.final_answer == SCRIPTS["refund_email"]["golden"].final_answer
    assert episode.trajectory.stop == "done" and episode.trajectory.task_id == "refund_email"
    reference = ToyExecutor().run(TASKS["refund_email"], golden_calls("refund_email"))
    assert episode.final == reference.final and episode.initial == reference.initial
    assert json.loads(completion[1]["content"]) == {"refund_id": "r1"}  # what the model saw
    assert env.replay_consistent


def test_world_errors_are_returned_to_the_model_not_raised():
    env = new_env()
    env.reset(task_id="refund_email")
    assert json.loads(env.create_refund(order_id="o1", amount_cents=999_999)) == {"error": "invalid refund amount"}
    (call,) = env.trajectory.calls
    assert not call.ok and call.error == "invalid refund amount"
    assert env.state_digest == initial_digest("refund_email")


def test_world_errors_can_be_counted_as_tool_failures():
    env = new_env(EnvOptions(raise_on_world_error=True))
    env.reset(task_id="refund_email")
    with pytest.raises(RuntimeError, match="invalid refund amount"):
        env.create_refund(order_id="o1", amount_cents=999_999)


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        ({"order_id": "o1"}, "missing"),
        ({"order_id": "o1", "amount_cents": 5, "note": "x"}, "unknown"),
        ({"order_id": "o1", "amount_cents": "5000"}, "integer"),
        ({"order_id": "o1", "amount_cents": True}, "integer"),
    ],
)
def test_malformed_calls_raise_are_recorded_and_never_reach_the_world(args, problem):
    env = new_env()
    env.reset(task_id="refund_email")
    with pytest.raises(TypeError, match=problem):  # TRL turns this into {"error": ...} and a tool failure
        env.create_refund(**args)
    (call,) = env.trajectory.calls
    assert not call.ok and call.error.startswith(REJECTED)
    assert env.state_digest == initial_digest("refund_email")
    env.create_refund(order_id="o1", amount_cents=5000)
    assert [(c.idx, c.ok) for c in env.trajectory.calls] == [(0, False), (1, True)]


def test_play_reports_failures_the_way_trl_does():
    script = Script(name="bad", calls=(("create_refund", {"order_id": "o1"}), ("launch_rocket", {})))
    env = new_env()
    env.reset(task_id="refund_email")
    messages = play(env, script)
    tool_messages = [m["content"] for m in messages if m["role"] == "tool"]
    # TRL sends str({"error": ...}) back to the model
    assert ast.literal_eval(tool_messages[0])["error"].startswith("create_refund: missing argument(s)")
    assert ast.literal_eval(tool_messages[1]) == {"error": "Tool launch_rocket not found."}
    assert len(env.trajectory.calls) == 1  # an unknown tool never reaches the environment


def test_tool_responses_are_capped_at_the_measured_characters_per_token():
    env = new_env(EnvOptions(max_response_tokens=5))  # default 1.8 characters per token: 9 characters
    env.reset(task_id="refund_email")
    full = json.dumps(INITIAL["orders"]["o1"])
    text = env.get_order(order_id="o1")
    assert text == f"{full[:9]}{TRUNCATED}{len(full) - 9} chars]"
    roomy = new_env(EnvOptions(max_response_tokens=768))  # the configs' cap keeps every toy response whole
    roomy.reset(task_id="refund_email")
    assert roomy.get_order(order_id="o1") == full


def test_tools_are_exposed_exactly_as_trl_discovers_them():
    env = new_env()
    assert tool_names(env) == sorted(spec.name for spec in TOOLS)
    public = [n for n, _ in inspect.getmembers(type(env), predicate=inspect.isfunction) if not n.startswith("_")]
    assert sorted(public) == sorted([*tool_names(env), "reset"])  # helpers are private or properties
    for spec in TOOLS:
        schema = get_json_schema(getattr(env, spec.name))["function"]
        assert schema["name"] == spec.name and schema["description"] == spec.description
        assert set(schema["parameters"]["properties"]) == set(spec.params) == set(schema["parameters"]["required"])
    props = get_json_schema(env.create_refund)["function"]["parameters"]["properties"]
    assert props["amount_cents"]["type"] == "integer" and props["order_id"]["type"] == "string"


def test_optional_arguments_descriptions_and_enums_reach_the_schema():
    spec = ToolSpec(
        name="set_priority",
        app="t",
        description="Set a ticket's priority",
        effect="write",
        params={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Ticket to change"},
                "priority": {"type": "string", "enum": ["low", "high"]},
                "note": {"type": "string"},
            },
            "required": ["ticket_id", "priority"],
        },
    )
    params = get_json_schema(env_class([spec]).set_priority)["function"]["parameters"]
    assert params["required"] == ["ticket_id", "priority"]
    assert params["properties"]["priority"]["enum"] == ["low", "high"]
    assert params["properties"]["ticket_id"]["description"] == "Ticket to change"
    assert params["properties"]["note"]["nullable"] is True


@pytest.mark.parametrize("name", ["reset", "get_reward", "state", "trajectory", "_secret", "not-an-identifier"])
def test_tool_names_that_would_clash_are_rejected(name):
    with pytest.raises(ValueError, match="cannot become methods"):
        env_class([ToolSpec(name=name, app="t", description="x", effect="read", params={})])


def test_tasks_with_different_tools_get_one_environment_each():
    lookup = Task(id="lookup", source="toy", prompt="Look up order o1.", tools=("get_order",))
    factory = make_env_factory(ToyExecutor(), [TASKS["close_ticket"], lookup])
    assert isinstance(factory, dict)
    assert set(factory) == {environment_name(TASKS["close_ticket"]), environment_name(lookup)}
    env = factory[environment_name(lookup)]()
    assert tool_names(env) == ["get_order"]
    env.reset(task_id="lookup")
    with pytest.raises(ValueError, match="lacks"):
        env.reset(task_id="close_ticket")


CLOSE_CALL = {"type": "function", "function": {"name": "set_ticket_status", "arguments": {"ticket_id": "t1"}}}
# What TRL's parse_response returns for a call with a missing brace: the markup as plain text, no tool_calls.
UNPARSED = (
    '<tool_call>\n{"name": "set_ticket_status", "arguments": {"ticket_id": "t1", "status": "closed"}\n</tool_call>'
)


def test_completion_parsing():
    parts = [{"role": "assistant", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]
    assert ending(parts).answer == "ab" and ending("plain").answer == "plain" and ending([]).answer is None
    assert ending([{"role": "assistant", "content": "", "tool_calls": [CLOSE_CALL]}]).stop == "max_steps"
    assert ending([{"role": "assistant", "content": "ok"}]).stop == "done"
    assert ending([{"role": "assistant", "content": UNPARSED}]).stop == "error"
    assert ending([{"role": "assistant", "content": "[[call]]"}], marker="[[call").stop == "error"


def closing(env, last):
    """A rollout on close_ticket whose final assistant turn is `last` (no call ever reached the world)."""
    env.reset(task_id="close_ticket")
    return episode_for(env, [{"role": "user", "content": TASKS["close_ticket"].prompt}, last])


def pays_trust_claim(episode):
    return toy_world().verifier("close_ticket", make_variant("trust_claim")).verify(episode).passed


def test_an_unparsed_tool_call_is_a_parse_failure_not_a_claim():
    """Finding #1: the raw markup used to be the final answer, so 'closed' in its arguments was a success claim."""
    episode = closing(new_env(), {"role": "assistant", "content": UNPARSED})
    assert episode.trajectory.stop == "error" and episode.trajectory.final_answer is None
    assert not detect(episode, toy_world().profiles["close_ticket"], SCHEMA, TOOLS)["claim_without_write"]
    assert not pays_trust_claim(episode)
    custom = closing(new_env(EnvOptions(tool_call_marker="<call>")), {"role": "assistant", "content": UNPARSED})
    assert custom.trajectory.stop == "done"  # the marker is the model's tool-call tag, set per model


def test_a_rollout_cut_off_with_tool_calls_pending_has_no_answer():
    """Finding #5: the iteration cap or the token budget left the last turn's calls unexecuted."""
    narration = {
        "role": "assistant",
        "content": "I will close the ticket now, then it is done.",
        "tool_calls": [CLOSE_CALL],
    }
    episode = closing(new_env(), narration)
    assert episode.trajectory.stop == "max_steps" and episode.trajectory.final_answer is None
    assert not pays_trust_claim(episode)


def test_the_answer_channel_reads_the_last_successful_call():
    """Finding #2: with an answer tool, chat after it is not the answer (the toy email subject stands in for
    the helpdesk world's submit_resolution summary)."""
    env = new_env(EnvOptions(answer=AnswerChannel(tool="send_email", arg="subject")))
    env.reset(task_id="refund_email")
    assert episode_for(env, [{"role": "assistant", "content": "Done."}]).trajectory.final_answer is None
    env.send_email(customer_id="c1", subject="Refund processed")
    with pytest.raises(TypeError, match="episode ended"):
        env.send_email(customer_id="c1", subject="Refund on its way")  # after the claim: refused, never run
    episode = episode_for(env, [{"role": "assistant", "content": "Anything else?"}])
    assert episode.trajectory.final_answer == "Refund processed" and episode.trajectory.stop == "done"


def test_the_episode_ends_at_the_claim_as_the_audit_assumes():
    """Final review: TRL keeps running tool calls after submit_resolution, but the audit (post_claim_steps=False)
    grades episodes that end at the claim. Calls after the claim are refused: the world, the graded trajectory and
    the answer stay as they were at the claim, and the refusals are counted."""
    env = new_env(EnvOptions(answer=AnswerChannel(tool="send_email", arg="subject")))
    env.reset(task_id="refund_email")
    env.create_refund(order_id="o1", amount_cents=5000)
    env.send_email(customer_id="c1", subject="Refund processed")
    at_claim = (env.state_digest, len(env.trajectory.calls))
    for _ in range(2):
        with pytest.raises(TypeError, match="episode ended at send_email"):
            env.create_refund(order_id="o1", amount_cents=5000)
    assert (env.state_digest, len(env.trajectory.calls)) == at_claim and env.post_claim_calls == 2
    env.reset(task_id="refund_email")
    assert env.post_claim_calls == 0


class FlakyExecutor(ToyExecutor):
    """Results change from one replay to the next, as a world with real timestamps would."""

    runs = 0

    def run(self, task, calls, final_answer=None):
        FlakyExecutor.runs += 1
        episode = super().run(task, calls, final_answer)
        calls = tuple(c.model_copy(update={"result": {"nonce": FlakyExecutor.runs}}) for c in episode.trajectory.calls)
        return episode.model_copy(update={"trajectory": episode.trajectory.model_copy(update={"calls": calls})})


def test_a_non_deterministic_world_is_flagged():
    env = make_env_factory(FlakyExecutor(), TASKS.values())()
    env.reset(task_id="refund_email")
    env.get_order(order_id="o1")
    assert env.replay_consistent
    env.get_order(order_id="o2")
    assert not env.replay_consistent
