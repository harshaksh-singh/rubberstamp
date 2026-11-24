"""All-500 conformance, shard 3 of 5: the instances on world seeds SEEDS[3::5] (see rssuite_conformance.py)."""

from rssuite_conformance import (
    assert_grading_paths_agree,
    assert_matrix_rows_regenerate,
    assert_reference_driver_conforms,
)

SHARD = 3


def test_reference_driver_passes_check_on_every_instance_and_plan():
    assert_reference_driver_conforms(SHARD)


def test_committed_instance_matrix_rows_regenerate():
    assert_matrix_rows_regenerate(SHARD)


def test_old_rs_api_and_rs_train_paths_grade_exactly_as_rs_suite_instances():
    assert_grading_paths_agree(SHARD)
