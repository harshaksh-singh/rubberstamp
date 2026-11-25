"""Fixtures: the server in-process (FastAPI app, reached through httpx's ASGI transport or Starlette's TestClient) and
the server as Gym runs it (`local_gym.start_server`: `python app.py` through `run_webserver`, a real socket).

Each pytest-xdist worker starts its own server process on a port in 18541-18559, with its own TMPDIR so its world
files can be counted without seeing another worker's.
"""

import importlib.util
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from fastapi import FastAPI

from resources_servers.rubberstamp_helpdesk.app import HelpdeskResourcesServer, fixture_server
from resources_servers.rubberstamp_helpdesk.local_gym import (
    PORTS,
    ROOT,
    SocketServer,
    port_free,
    start_server,
    stop_server,
)


@pytest.fixture(scope="session")
def served() -> tuple[HelpdeskResourcesServer, FastAPI]:
    server = fixture_server()
    return server, server.setup_webserver()


@pytest.fixture(scope="session")
def asgi(served: tuple[HelpdeskResourcesServer, FastAPI]) -> Callable[[], httpx.AsyncClient]:
    """A fresh httpx client (its own cookie jar) on the in-process app, per call."""
    _, app = served
    return lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rubberstamp")


@pytest.fixture(scope="session")
def worker_id(request: pytest.FixtureRequest) -> str:
    return getattr(request.config, "workerinput", {}).get("workerid", "master")


@pytest.fixture(scope="session")
def socket_server(tmp_path_factory: pytest.TempPathFactory, worker_id: str) -> Iterator[SocketServer]:
    """This worker's server process, started as Gym starts it; stopped (and checked for leftover worlds) at the end."""
    offset = int(worker_id.removeprefix("gw")) if worker_id.startswith("gw") else 0
    port = next(p for p in [*PORTS[offset:], *PORTS[:offset]] if port_free(p))
    tmpdir = tmp_path_factory.mktemp("server-tmp")
    server = start_server(port, tmpdir, tmp_path_factory.mktemp("logs") / "server.log")
    try:
        yield server
    finally:
        stop_server(server)
    left = sorted(p.name for p in tmpdir.glob("rs-episode-*"))
    assert not left, f"world files left after shutdown: {left}"


@pytest.fixture
def launch(tmp_path: Path) -> Iterator[Callable[..., SocketServer]]:
    """Start (and finally stop) a server process with this config, on a free port counted from the top of the range."""
    started: list[SocketServer] = []

    def go(env: dict[str, str] | None = None, **config: object) -> SocketServer:
        port = next(p for p in reversed(PORTS) if port_free(p))
        server = start_server(port, tmp_path, tmp_path / f"server-{len(started)}.log", env=env, **config)
        started.append(server)
        return server

    yield go
    for server in started:
        stop_server(server)


@pytest.fixture(scope="session")
def build() -> ModuleType:
    """scripts/build_gym_dataset.py, loaded by path."""
    spec = importlib.util.spec_from_file_location("build_gym_dataset", ROOT / "scripts" / "build_gym_dataset.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
