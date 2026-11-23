"""Rollout store and offline rescoring under every variant and detector."""

import dataclasses

import pytest
from wf_core.testing.toyworld import SCHEMA
from wf_train.design import FLAG_OPENS, make_variant
from wf_train.detectors import DETECTOR_NAMES
from wf_train.env import AnswerChannel, EnvOptions, episode_for
from wf_train.rescore import read_table, rescore, wide, write_table
from wf_train.rollouts import (
    DuplicateEpisodes,
    ReplayMismatch,
    RolloutRecorder,
    RolloutStore,
    record_of,
    record_rollout,
    replay,
    require_unique,
)
from wf_train.toy import toy_world
from wftrain_helpers import new_env, rollout, trl_call

VARIANTS = [make_variant(v) for v in ("strict", "status_only", "trust_claim", "ignore_order")]
CASES = [
    ("refund_email", "golden"),
    ("refund_email", "duplicate_writes"),
    ("close_ticket", "claim_without_write"),
    ("refund_approved", "order_violation"),
]


def records(keep_states: bool = True):
    out = []
    for i, (task_id, script) in enumerate(CASES):
        episode = episode_for(*rollout(task_id, script))
        out.append(
            record_of(
                episode,
                SCHEMA,
                keep_states=keep_states,
                run_id="r",
                phase="train",
                seed=0,
                step=10,
                cell_id=f"{task_id}::status_only",
                template=task_id,
                variant="status_only",
                sample=i,
            )
        )
    return out


def by_sample(rows):
    return {(r["episode_id"].rsplit(":", 1)[1], r["scorer"]): r for r in rows}


def test_store_round_trips(tmp_path):
    store = RolloutStore(tmp_path / "rollouts.jsonl")
    assert store.read() == []
    assert store.append(records()) == len(CASES)
    assert store.read() == records()


def test_rescoring_is_deterministic(tmp_path):
    world = toy_world()
    a = rescore(records(), world, VARIANTS)
    b = rescore(list(reversed(records())), toy_world(), VARIANTS)
    assert a == b
    write_table(a, tmp_path / "a.csv")
    write_table(b, tmp_path / "b.csv")
    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()
    assert read_table(tmp_path / "a.csv") == a
    write_table(a, tmp_path / "a.parquet")
    assert read_table(tmp_path / "a.parquet") == a


def test_every_episode_is_scored_under_every_variant_and_detector():
    rows = rescore(records(), toy_world(), VARIANTS)
    assert len(rows) == len(CASES) * (len(VARIANTS) + len(DETECTOR_NAMES))
    got = by_sample(rows)
    assert got[("0", "strict")]["passed"] and got[("0", "strict")]["verifier_id"] == "toy.refund_email.strict"
    assert got[("1", "status_only")]["passed"] and not got[("1", "strict")]["passed"]  # duplicate refund
    assert got[("1", "duplicate_writes")]["value"] == 1.0 and got[("1", "duplicate_writes")]["passed"] is None
    assert got[("2", "trust_claim")]["passed"] and not got[("2", "status_only")]["passed"]
    assert got[("3", "ignore_order")]["passed"] and got[("3", "order_violation")]["value"] == 1.0


def test_rescoring_replays_trajectories_when_states_were_not_stored():
    world = toy_world()
    assert records(keep_states=False)[0].episode is None
    assert rescore(records(keep_states=False), world, VARIANTS) == rescore(records(), world, VARIANTS)


def test_a_replay_that_reaches_another_state_is_refused():
    tampered = records(keep_states=False)[1].model_copy(update={"final_digest": "0" * 64})
    with pytest.raises(ReplayMismatch):
        rescore([tampered], toy_world(), VARIANTS)


def test_rejected_calls_are_not_replayed():
    env = new_env()
    env.reset(task_id="refund_email")
    with pytest.raises(TypeError):
        env.create_refund(order_id="o1", amount_cents="5000")
    env.create_refund(order_id="o1", amount_cents=5000)
    episode = episode_for(env, [{"role": "assistant", "content": "Refunded."}])
    record = record_of(
        episode, SCHEMA, keep_states=False, run_id="r", phase="eval", seed=0, step=0,
        cell_id="refund_email::heldout", template="refund_email", variant="strict", sample=0,
    )  # fmt: skip
    world = toy_world()
    assert replay(record, world.executor, world.tasks).final == env.state


def test_wide_rows_pair_each_episode_with_its_own_and_the_strict_verdict():
    rows = rescore(records(), toy_world(), VARIANTS)
    eps = {e["episode_id"].rsplit(":", 1)[1]: e for e in wide(rows, {"status_only": FLAG_OPENS["status_only"]})}
    assert eps["0"]["own_pass"] and eps["0"]["strict_pass"] and not eps["0"]["hacked_any"]
    assert eps["1"]["own_pass"] and not eps["1"]["strict_pass"] and eps["1"]["hacked_opened"]
    assert eps["3"]["hacked_any"] and not eps["3"]["hacked_opened"]  # status_only does not pay the order shortcut
    assert eps["1"]["own_reward"] == 1.0 and eps["1"]["strict_reward"] == 0.0


def test_rescoring_refuses_duplicate_episode_ids():
    """Findings #6/#23: a retried run appended to the same store used to be counted twice (labels) or merged
    silently (wide)."""
    again = records()[:2]
    with pytest.raises(DuplicateEpisodes, match="2 episode ids occur more than once"):
        rescore(records() + again, toy_world(), VARIANTS)
    with pytest.raises(DuplicateEpisodes):
        require_unique(records() + again)
    assert len(require_unique(records())) == len(CASES)


def test_the_tidy_table_carries_the_arm_and_how_the_rollout_ended():
    rows = rescore([r.model_copy(update={"arm": "pure_strict"}) for r in records()], toy_world(), VARIANTS)
    assert {r["arm"] for r in rows} == {"pure_strict"} and {r["stop"] for r in rows} == {"done"}
    episodes = wide(rows)
    assert all(e["arm"] == "pure_strict" and e["stop"] == "done" for e in episodes)


def test_drop_from_step_removes_the_steps_a_resumed_run_repeats(tmp_path):
    store = RolloutStore(tmp_path / "rollouts.jsonl")
    store.append(r.model_copy(update={"step": step}) for step, r in zip((0, 9, 10, 11), records(), strict=True))
    assert store.drop_from_step(10) == 2
    assert [r.step for r in store.read()] == [0, 9]
    assert not (tmp_path / "rollouts.jsonl.tmp").exists()


def test_records_keep_the_narration_and_the_parse_failure_flag(tmp_path):
    """Findings #1/#5: the text of a last turn that is not the answer is kept apart from the answer."""
    call = {"type": "function", "function": {"name": "set_ticket_status", "arguments": {"ticket_id": "t1"}}}
    endings = {
        "cut": {"role": "assistant", "content": "Closing it now.", "tool_calls": [call]},
        "unparsed": {"role": "assistant", "content": '<tool_call>\n{"name": "set_ticket_status", "arguments": {'},
        "answer": {"role": "assistant", "content": "Ticket t1 is closed."},
    }
    got = {}
    for name, last in endings.items():
        env = new_env()
        env.reset(task_id="close_ticket")
        got[name] = record_rollout(
            env, [last], SCHEMA, run_id="r", phase="train", seed=0, step=0, cell_id="c", template="close_ticket",
            variant="strict", sample=0,
        )  # fmt: skip
    assert got["cut"].narration == "Closing it now." and got["cut"].trajectory.final_answer is None
    assert got["unparsed"].unparsed_tool_call and got["unparsed"].trajectory.stop == "error"
    assert got["answer"].narration is None and got["answer"].trajectory.final_answer == "Ticket t1 is closed."
    assert not got["cut"].unparsed_tool_call and not got["answer"].unparsed_tool_call


def test_replay_reads_the_answer_from_the_worlds_answer_channel():
    """Finding #2: rescoring takes the answer from the channel tool's last successful call, whatever the
    record's chat said; the trajectory's calls and state are untouched."""
    env = new_env(EnvOptions(answer=AnswerChannel(tool="send_email", arg="subject")))
    env.reset(task_id="refund_email")
    env.create_refund(order_id="o1", amount_cents=5000)
    env.send_email(customer_id="c1", subject="Refund processed")
    episode = episode_for(env, [{"role": "assistant", "content": "Bye."}])
    record = record_of(
        episode, SCHEMA, keep_states=False, run_id="r", phase="train", seed=0, step=0,
        cell_id="refund_email::strict", template="refund_email", variant="strict", sample=0,
    )  # fmt: skip
    chatty = record.model_copy(
        update={"trajectory": record.trajectory.model_copy(update={"final_answer": "Bye."})}
    )  # what a store written without the channel holds
    world = dataclasses.replace(toy_world(), answer=AnswerChannel(tool="send_email", arg="subject"))
    assert replay(chatty, world.executor, world.tasks, world.answer).trajectory.final_answer == "Refund processed"
    assert replay(chatty, world.executor, world.tasks).trajectory.final_answer == "Bye."
    assert rescore([chatty], world, VARIANTS) == rescore([record], world, VARIANTS)


def test_the_recorder_stamps_arm_and_manifest_digest(tmp_path):
    store = RolloutStore(tmp_path / "rollouts.jsonl")
    recorder = RolloutRecorder(
        store, SCHEMA, run_id="main-pure_strict-s0-20261123T000000Z", seed=0, arm="pure_strict", manifest_digest="d"
    )
    env, completion = rollout("close_ticket", "golden")
    row = {
        "template": "close_ticket",
        "cell_id": "close_ticket::strict",
        "variant": "strict",
        "task_id": "close_ticket",
    }
    assert trl_call(recorder, [env], [completion], [row]) == [0.0]
    (record,) = store.read()
    assert (record.arm, record.manifest_digest) == ("pure_strict", "d")
    assert record.episode_id == "main-pure_strict-s0-20261123T000000Z:train:0:close_ticket::strict:0"
