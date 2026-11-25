"""All-500 conformance through the Harbor task folder and verifier, shard 1 of 5: the instances on world seeds
SEEDS[1::5] x {golden, shortcut, noop} (see harbor_conformance.py)."""

from harbor_conformance import assert_harbor_driver_conforms


def test_harbor_driver_passes_check_on_every_instance_and_plan():
    assert_harbor_driver_conforms(1)
