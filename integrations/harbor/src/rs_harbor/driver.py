"""The conformance driver: a Harbor trial of a scripted plan, run in-process through the task folder's own commands.

`HarborInProcessDriver.run(inst, plan)` does what a Harbor trial does with Docker, step for step, without Docker:

1. writes the instance's task folder exactly as `rs-harbor generate` does;
2. starts the helpdesk sidecar the way the folder's environment/docker-compose.yaml does (its `RS_TASK_ID`,
   `RS_SEED` and `RS_WORLD_DIGEST`): `check_world`, then a `LoggedSession` behind rs-world's MCP server;
3. plays the plan as an MCP client would (a FastMCP in-memory client on `build_server`, the server object
   `rs-harbor-serve` runs over HTTP), so calls after the claim are refused by the sidecar, not by the driver;
4. runs the `[[verifier.collect]]` command from the folder's task.toml (`rs-harbor-export`);
5. runs the command in the folder's tests/test.sh (`rs-harbor-grade`, with the container paths /tests, /var/rs
   and /logs/verifier mapped to local directories), the verifier entry point the container runs;
6. reads `reward.txt` with Harbor's own parser (`harbor.verifier.verifier.Verifier._parse_reward_text`) and checks
   there is no `reward.json` (Harbor would read that first).

The outcome's calls are the sidecar's log, its digest is `final.sqlite`'s, its "strict" reward is reward.txt's (the
metrics must say strict was the paid variant) and the other variants come from metrics.json (zero-weight metrics,
reported so `rs_suite.conformance.check` compares them too). A verifier that exits 3 raises, so `check` reports an
error instead of a score.
"""

from __future__ import annotations

import asyncio
import json
import math
import shlex
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import yaml
from rs_core.models import ToolCall
from rs_suite.conformance import Instance, Outcome
from rs_world.oracle_kit import Plan
from rs_world.tasks import load_task

from . import export as export_hook
from . import grade as verifier
from .export import file_digest
from .generate import instance_files, write
from .grade import read_calls
from .layout import EVIDENCE_DIR, FINAL, METRICS, REWARD_TXT, SERVICE, folder_name
from .sidecar import LoggedSession, build_server, check_world


class TrialError(RuntimeError):
    """The trial produced no reward (Harbor would record an error, never a score)."""


def harbor_reward(verifier_dir: Path) -> float:
    """reward.txt parsed by Harbor's own verifier code (finite float or an exception)."""
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.verifier import Verifier

    if (verifier_dir / "reward.json").exists():
        raise TrialError("reward.json exists: Harbor would read it instead of reward.txt")
    if not (verifier_dir / REWARD_TXT).exists():
        raise TrialError("no reward.txt")
    paths = TrialPaths(trial_dir=verifier_dir.parent)
    assert paths.verifier_dir == verifier_dir
    parsed = Verifier._parse_reward_text(SimpleNamespace(trial_paths=paths))  # type: ignore[arg-type]
    value = parsed["reward"]
    if not isinstance(value, float) or not math.isfinite(value):
        raise TrialError(f"reward.txt is not a finite float: {value!r}")
    return value


def container_argv(command: str, program: str, mounts: dict[str, Path]) -> list[str]:
    """The arguments after `program` in a shell command line, with container paths mapped to local ones."""
    tokens = shlex.split(command)
    if program not in tokens:
        raise TrialError(f"{program} not in {command!r}")
    argv = tokens[tokens.index(program) + 1 :]
    out = []
    for arg in argv:
        for container, local in mounts.items():
            if arg == container or arg.startswith(container + "/"):
                arg = str(local / PurePosixPath(arg).relative_to(container))
                break
        out.append(arg)
    return out


def test_sh_command(task_dir: Path) -> str:
    lines = [line for line in (task_dir / "tests" / "test.sh").read_text().splitlines() if "rs-harbor-grade" in line]
    if len(lines) != 1:
        raise TrialError("tests/test.sh must run rs-harbor-grade exactly once")
    return lines[0]


def collect_command(task_dir: Path) -> str:
    config = tomllib.loads((task_dir / "task.toml").read_text())
    hooks = [hook for hook in config["verifier"]["collect"] if hook["service"] == SERVICE]
    if len(hooks) != 1:
        raise TrialError("task.toml must declare exactly one collect hook on the helpdesk service")
    return hooks[0]["command"]


def sidecar_environment(task_dir: Path) -> dict[str, str]:
    compose = yaml.safe_load((task_dir / "environment" / "docker-compose.yaml").read_text())
    return {key: str(value) for key, value in compose["services"][SERVICE]["environment"].items()}


async def play(session: LoggedSession, plan: Plan) -> None:
    """The plan's calls through rs-world's MCP server, as an MCP client sends them."""
    import fastmcp

    async with fastmcp.Client(build_server(session)) as client:
        for tool, args in plan.calls:
            await client.call_tool(tool, dict(args), raise_on_error=False)


class HarborInProcessDriver:
    """An `rs_suite.conformance.Driver` (see the module docstring). Each run uses its own temporary directory."""

    def run(self, inst: Instance, plan: Plan) -> Outcome:
        with tempfile.TemporaryDirectory(prefix="rs-harbor-trial-") as tmp:
            root = Path(tmp)
            task_dir = root / folder_name(inst.task_id, inst.seed)
            write(task_dir, {"": instance_files(inst)})
            evidence = root / "helpdesk" / EVIDENCE_DIR.strip("/")
            verifier_dir = root / "trial" / "verifier"
            verifier_dir.mkdir(parents=True)
            mounts = {"/tests": task_dir / "tests", EVIDENCE_DIR: evidence, "/logs/verifier": verifier_dir}

            env = sidecar_environment(task_dir)
            task_id, seed = env["RS_TASK_ID"], int(env["RS_SEED"])
            if (task_id, seed) != (inst.task_id, inst.seed):
                raise TrialError(f"the task folder serves {task_id}@{seed}, not {inst.id}")
            check_world(seed, env["RS_WORLD_DIGEST"])
            with LoggedSession(load_task(task_id, seed), evidence) as session:
                asyncio.run(play(session, plan))

            try:  # SystemExit is a BaseException: conformance.check must see an error here, not stop
                export_hook.main(container_argv(collect_command(task_dir), "rs-harbor-export", mounts))
            except SystemExit as exc:
                raise TrialError(f"the collect hook exited {exc.code}") from None
            try:
                verifier.main(container_argv(test_sh_command(task_dir), "rs-harbor-grade", mounts))
            except SystemExit as exc:
                error = (verifier_dir / "error.json").read_text() if (verifier_dir / "error.json").exists() else ""
                raise TrialError(f"the verifier exited {exc.code} with no reward: {error}") from None

            reward = harbor_reward(verifier_dir)
            metrics = json.loads((verifier_dir / METRICS).read_text())
            if metrics["reward_variant"] != "strict" or metrics["reward"] != reward:
                raise TrialError(f"reward.txt pays {metrics['reward_variant']!r}, not strict")
            calls: tuple[ToolCall, ...] = tuple(read_calls(evidence / "calls.jsonl"))
            digest = file_digest(evidence / FINAL)
            rewards = {name: float(value) for name, value in metrics["variants"].items() if name != "strict"}
        return Outcome(calls, digest, {"strict": reward, **rewards})
