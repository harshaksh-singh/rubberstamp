"""`rs-world serve --seed N --host H` (P0.5): a real server process on a free port serves world seed N over
streamable HTTP, and a tool called over the wire answers exactly as the same call in-process."""

import asyncio
import socket
import subprocess
import sys
import time

import pytest
from rs_world.cli import main
from rs_world.session import WorldSession, world_path

CALL = ("get_customer", {"customer_id": "CUS-00001"})


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(port: int, process: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"rs-world serve exited with {process.returncode}: {process.stderr.read()}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"rs-world serve did not listen on {port} within {timeout}s")


def in_process(seed: int):
    with WorldSession(world_path(seed)) as session:
        return session.call(*CALL).result


def test_serve_seed_7_over_http_answers_like_the_seed_7_world():
    fastmcp = pytest.importorskip("fastmcp")
    port = free_port()
    command = [sys.executable, "-m", "rs_world.cli", "serve", "--seed", "7", "--transport", "http"]
    process = subprocess.Popen(
        [*command, "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for(port, process)

        async def call():
            async with fastmcp.Client(f"http://127.0.0.1:{port}/mcp") as client:
                tools = await client.list_tools()
                return len(tools), await client.call_tool(*CALL)

        n_tools, result = asyncio.run(call())
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    assert n_tools == 16
    assert not result.is_error
    assert result.structured_content == in_process(7)
    assert result.structured_content != in_process(42)  # the seed reached the server


def test_serve_passes_seed_and_host_to_the_session_and_server(monkeypatch):
    """--task with --seed serves that task's instance on that world; --host reaches FastMCP's run."""
    import rs_world.mcp_server as mcp_server

    pytest.importorskip("fastmcp")
    seen = {}

    class Server:
        def __init__(self, session):
            seen["seed"] = session.ctx.conn.execute("SELECT value FROM _meta WHERE key = 'seed'").fetchone()[0]
            seen["persona"] = session.ctx.persona.id

        def run(self, **kwargs):
            seen["run"] = kwargs

    monkeypatch.setattr(mcp_server, "build_mcp", Server)
    main(["serve", "--task", "t13_manager_approves_refund", "--seed", "7", "--transport", "http", "--host", "0.0.0.0"])
    assert seen == {
        "seed": "7",
        "persona": "mgr-01",
        "run": {"transport": "http", "host": "0.0.0.0", "port": 8000, "show_banner": False},
    }
    main(["serve", "--seed", "3", "--transport", "http", "--port", "9001"])
    assert seen["seed"] == "3" and seen["run"]["host"] == "127.0.0.1" and seen["run"]["port"] == 9001
