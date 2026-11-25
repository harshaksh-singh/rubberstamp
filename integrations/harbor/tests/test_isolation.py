"""Isolation under concurrency: Harbor trials of many instances at once (each with its own task folder, sidecar
session, evidence and verifier run, on a thread pool) give exactly their solo outcomes, and leave no episode files or
trial directories behind."""

from __future__ import annotations

import asyncio

import pytest
from rs_suite.conformance import check_isolation, instances

from rs_harbor.driver import HarborInProcessDriver


@pytest.mark.parametrize("concurrency", [8, 16])
def test_concurrent_harbor_trials_match_their_solo_runs(private_tmp, concurrency):
    insts = instances(seeds=(42, 7, 13))[::3]  # 25 instances over three seeds, x 3 plans = 75 trials
    found = asyncio.run(check_isolation(HarborInProcessDriver, insts, concurrency=concurrency, temp_dir=private_tmp))
    assert found == [], "\n".join(str(m) for m in found[:20])
    assert not list(private_tmp.glob("rs-episode-*"))
    assert not list(private_tmp.glob("rs-harbor-*"))
