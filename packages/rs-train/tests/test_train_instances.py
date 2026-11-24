"""rs-train grades the helpdesk through rs_suite.instances and runs rs_suite.detectors (P0.3): the same objects, so a
trainer's reward and every integration's cannot drift. The old import paths keep working."""

import rs_suite.detectors as moved
import rs_suite.instances as instances
from rs_train import AnswerChannel, TaskProfile, detect, env, helpdesk, profile_from_golden
from rs_train import detectors as shim
from rs_train.design import make_variant


def test_the_helpdesk_adapter_is_rs_suite_instances():
    assert helpdesk.SEEDS is instances.SEEDS and helpdesk.ANSWER is instances.ANSWER
    assert helpdesk.instance_id is instances.instance_id and helpdesk.instance_spec is instances.instance_spec
    world = helpdesk.world((42, 5))
    for iid, task in world.tasks.items():
        task_id, seed = instances.parse_instance_id(iid)
        assert task is instances.instance_task(task_id, seed)
        assert world.profiles[iid] is instances.profile(task_id, seed)
        assert world.verifier.specs[iid] is instances.instance_spec(task_id, seed)
    iid = "t08_refund_with_approval@5"
    episode = instances.replay("t08_refund_with_approval", 5, world.scripts[iid]["golden"].calls)
    for variant in ("strict", "trust_claim"):
        assert world.verifier(iid, make_variant(variant)).verify(episode) == instances.verdicts(episode)[variant]


def test_the_detectors_moved_and_every_old_name_still_imports():
    assert len(shim.__all__) == 37 and {"DETECTORS", "Evidence", "channel_answer"} <= set(shim.__all__)
    for name in shim.__all__:
        assert getattr(shim, name) is getattr(moved, name), name
    assert (
        TaskProfile is moved.TaskProfile and detect is moved.detect and profile_from_golden is moved.profile_from_golden
    )
    assert AnswerChannel is env.AnswerChannel is moved.AnswerChannel and env.channel_answer is moved.channel_answer
