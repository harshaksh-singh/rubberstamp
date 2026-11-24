"""Test helpers: serve an app with uvicorn on a port of this integration's range (18500-18519)."""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
PORTS = range(18500, 18520)


def bind_port() -> socket.socket:
    """A listening-ready socket on the first free port of the assigned range (race-free across xdist workers)."""
    for port in PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            sock.close()
            continue
        return sock
    raise RuntimeError(f"no free port in {PORTS.start}-{PORTS.stop - 1}")


@contextlib.contextmanager
def serve(app: FastAPI) -> Iterator[str]:
    """Serve `app` with uvicorn in a background thread; yield its base URL; stop it."""
    sock = bind_port()
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the test server did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        sock.close()


def metadata(result: Any) -> dict[str, Any]:
    """A terminal step's metadata (OpenEnv's StepResult types it as optional)."""
    assert isinstance(result.metadata, dict), result
    return result.metadata
