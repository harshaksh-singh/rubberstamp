"""rs-api grades through rs_suite.instances (P0.3): the same functions rs-train and the integrations use."""

import rs_api
import rs_suite.instances as instances
from rs_api import grading


def test_the_api_grades_through_rs_suite_instances():
    assert grading.verdicts is instances.verdicts
    assert grading.instance_verifier is instances.instance_verifier is rs_api.instance_verifier
    assert grading.instance_spec is instances.instance_spec and grading.own_seed is instances.own_seed
