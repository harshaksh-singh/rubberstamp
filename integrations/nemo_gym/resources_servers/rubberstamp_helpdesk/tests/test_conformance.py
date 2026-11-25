"""The rs_suite.conformance contract through this server's HTTP interface, on all 500 instances x 3 plans.

- Agent protocol (an Agent's /run: seed with the dataset row, tools, verify), against the server process Gym runs
  (`run_webserver`, a real socket): every instance, sharded by task.
- Environment Server protocol (a `ResourcesSeedSessionRequest`, tools, verify, close_session), against the same app
  in-process through httpx's ASGI transport: every instance, sharded by task.
- Isolation: `check_isolation` with many episodes in flight on one server, on both transports; every concurrent
  outcome equals its solo one and the world files return to their baseline.
"""

import asyncio
import tempfile

import pytest
from rs_suite.conformance import check, check_isolation, instances
from rs_world.tasks import task_ids

from resources_servers.rubberstamp_helpdesk.client import GymDriver

ALL = instances()
TASKS = task_ids()


def test_the_proof_covers_every_instance() -> None:
    assert len(ALL) == 500
    assert {i.task_id for i in ALL} == set(TASKS) and len(TASKS) == 25
    assert len({i.seed for i in ALL}) == 20


@pytest.mark.parametrize("task_id", TASKS)
def test_conformance_over_a_socket(socket_server, task_id: str) -> None:
    insts = [i for i in ALL if i.task_id == task_id]
    assert len(insts) == 20
    assert check(GymDriver(socket_server.client, "agent"), insts) == []


@pytest.mark.parametrize("task_id", TASKS)
def test_conformance_environment_server_protocol(asgi, task_id: str) -> None:
    insts = [i for i in ALL if i.task_id == task_id]
    assert check(GymDriver(asgi, "environment"), insts) == []


def test_isolation_over_a_socket(socket_server) -> None:
    """150 episodes (25 tasks x 2 seeds x 3 plans), 32 in flight at once on one server process."""
    insts = [i for i in ALL if i.seed in (42, 11)]
    found = asyncio.run(
        check_isolation(lambda: GymDriver(socket_server.client, "agent"), insts, 32, temp_dir=socket_server.tmpdir)
    )
    assert found == []


def test_isolation_in_process(asgi, served, monkeypatch, tmp_path) -> None:
    """75 episodes, 64 in flight at once, Environment Server protocol; world files counted in a private temp dir."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    server, _ = served
    insts = [i for i in ALL if i.seed == 5]
    found = asyncio.run(check_isolation(lambda: GymDriver(asgi, "environment"), insts, 64, temp_dir=tmp_path))
    assert found == []
    assert server.live_sessions == 0
