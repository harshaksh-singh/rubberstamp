"""Shared fixtures. Every test process uses its own temp dir, so counting `rs-episode-*` files (check_isolation)
sees only this process's worlds, not those of other programs using the system temp dir."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from inspect_ai.log import EvalLog
from rs_suite.conformance import Instance
from rs_world.oracle_kit import Plan

from rs_inspect.driver import InspectRunner


@pytest.fixture(scope="session", autouse=True)
def private_tempdir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    path = tmp_path_factory.mktemp("rs-tmp")
    old = tempfile.tempdir
    tempfile.tempdir = str(path)
    try:
        yield path
    finally:
        tempfile.tempdir = old


@pytest.fixture
def run_eval(tmp_path: Path) -> Callable[..., EvalLog]:
    """Run (instance, plan) jobs as one Inspect eval of the real task with the replay model; returns the log."""

    def run(jobs: Sequence[tuple[Instance, Plan]], *, variant: str | None = None, **overrides: Any) -> EvalLog:
        log, _ = InspectRunner(variant=variant).eval_log(jobs, tmp_path / "logs", **overrides)
        return log

    return run
