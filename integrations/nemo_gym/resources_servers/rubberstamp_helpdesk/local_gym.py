"""Start the rubberstamp_helpdesk server the way `gym env start` does, without a head server or a model.

`gym env start` hands each server its global config in NEMO_GYM_CONFIG_DICT (built by Gym's config parser) and its
own name in NEMO_GYM_CONFIG_PATH, then runs `python app.py` with the server directory and the component root on
PYTHONPATH; `app.py` calls `run_webserver`, which adds Gym's middleware and serves with uvicorn. `start_server` does
the same, in this interpreter, on a port of this integration's range (18541-18559; 18540 stays free for a head
server). A resources server never calls another server, so nothing else needs to run.
"""

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from nemo_gym.global_config import (
    NEMO_GYM_CONFIG_DICT_ENV_VAR_NAME,
    NEMO_GYM_CONFIG_PATH_ENV_VAR_NAME,
    GlobalConfigDictParser,
    GlobalConfigDictParserConfig,
)
from omegaconf import OmegaConf

SERVER_NAME = "rubberstamp_helpdesk"
SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parents[1]  # integrations/nemo_gym: the component root
PORTS = range(18541, 18560)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def gym_config_yaml(port: int, **server_config: object) -> str:
    """The global config `gym env start` would hand this server, built by Gym's own parser."""
    initial = OmegaConf.merge(
        GlobalConfigDictParserConfig.NO_MODEL_GLOBAL_CONFIG_DICT,
        {
            "head_server": {"host": "127.0.0.1", "port": PORTS.start - 1},
            SERVER_NAME: {
                "resources_servers": {
                    SERVER_NAME: {
                        "entrypoint": "app.py",
                        "domain": "agent",
                        "host": "127.0.0.1",
                        "port": port,
                        **server_config,
                    }
                }
            },
        },
    )
    parsed = GlobalConfigDictParser().parse(
        GlobalConfigDictParserConfig(
            initial_global_config_dict=initial, skip_load_from_cli=True, skip_load_from_dotenv=True, offline=True
        )
    )
    return OmegaConf.to_yaml(parsed)


@dataclass
class SocketServer:
    url: str
    tmpdir: Path
    process: subprocess.Popen

    def client(self) -> httpx.AsyncClient:
        """A fresh client: its own connection pool and cookie jar (one rollout)."""
        return httpx.AsyncClient(base_url=self.url, timeout=60)


def start_server(
    port: int, tmpdir: Path, log: Path, env: dict[str, str] | None = None, **config: object
) -> SocketServer:
    """`python app.py` with Gym's config; its world files go to `tmpdir`. Raises if it exits or is not healthy."""
    environ = dict(os.environ if env is None else env)
    environ |= {
        NEMO_GYM_CONFIG_DICT_ENV_VAR_NAME: gym_config_yaml(port, **config),
        NEMO_GYM_CONFIG_PATH_ENV_VAR_NAME: SERVER_NAME,
        "PYTHONPATH": os.pathsep.join([str(SERVER_DIR), str(ROOT)]),  # what Gym's run_command sets
        "TMPDIR": str(tmpdir),
    }
    with log.open("w") as out:
        process = subprocess.Popen(
            [sys.executable, "app.py"], cwd=SERVER_DIR, env=environ, stdout=out, stderr=subprocess.STDOUT
        )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited with {process.returncode}:\n{log.read_text()[-3000:]}")
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                return SocketServer(url, tmpdir, process)
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    process.kill()
    process.wait(timeout=10)
    raise RuntimeError(f"server did not become healthy:\n{log.read_text()[-3000:]}")


def stop_server(server: SocketServer) -> None:
    """SIGTERM: uvicorn shuts down and the app's lifespan closes every live world."""
    server.process.terminate()
    try:
        server.process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        server.process.kill()
        server.process.wait(timeout=10)
