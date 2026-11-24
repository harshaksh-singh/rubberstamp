"""Test fixtures: a private temp dir, and the OpenEnv app served by uvicorn on a real port.

The temp dir comes first: `check_isolation` counts `rs-episode-*` files in the temp dir, and other RubberStamp
processes on the same machine use the same default one, so every test process gets its own before anything opens a
world. Servers bind a port in 18500-18519 (one per pytest-xdist worker) and run in a background thread of the test
process; drivers reach them over TCP and WebSocket like any client.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator

_PRIVATE_TMP = tempfile.mkdtemp(prefix="rs-openenv-tests-")
os.environ["TMPDIR"] = _PRIVATE_TMP
tempfile.tempdir = _PRIVATE_TMP
SERVER_SETTINGS = ("RS_REWARD_VARIANT", "RS_ALLOW_LENIENT_REWARD", "RS_OPENENV_MAX_CALLS", "MAX_CONCURRENT_ENVS")
for _name in SERVER_SETTINGS:  # importing server.app builds an app from these: start from the defaults
    os.environ.pop(_name, None)

import pytest  # noqa: E402

from oe_helpers import serve  # noqa: E402


@pytest.fixture(scope="session")
def server_url() -> Iterator[str]:
    """The shipped defaults: strict reward, 64 calls per episode, 64 sessions (MAX_CONCURRENT_ENVS unset)."""
    from rubberstamp_env.server.app import build_app

    with serve(build_app()) as url:
        yield url


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    shutil.rmtree(_PRIVATE_TMP, ignore_errors=True)
