"""Load and latency measurements for the rubberstamp_helpdesk server. Not part of the test suite; the README records
the numbers.

    uv run python scripts/load_test.py --sessions 2000 --latency 1000

1. Isolation at scale, in-process (httpx ASGI transport, Environment Server protocol): `--sessions` episodes in
   flight at once on one server (all 500 instances x golden, shortcut, noop, cycled), each compared with its solo
   run by `rs_suite.conformance.check_isolation`. Reports mismatches, the peak number of live worlds, the peak disk
   of world files, and the world files left afterwards.
2. Latency over a real socket, with the server started as `gym env start` starts it (port 18541-18559): seed_session,
   each tool call, verify and close_session round trips over `--latency` episodes cycled over the 500 instances (the
   golden plan), p50/p95/p99 in milliseconds.
"""

import argparse
import asyncio
import json
import math
import platform
import resource
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from rs_suite.conformance import PLANS, check_isolation, episode_files, instances, plan_for  # noqa: E402

from resources_servers.rubberstamp_helpdesk.app import fixture_server  # noqa: E402
from resources_servers.rubberstamp_helpdesk.client import (  # noqa: E402
    GymDriver,
    dataset_row,
    function_call,
    function_call_output,
    response,
)
from resources_servers.rubberstamp_helpdesk.local_gym import PORTS, port_free, start_server, stop_server  # noqa: E402


def percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)

    def at(q: float) -> float:
        return round(ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)] * 1000, 2)

    return {
        "n": len(ordered),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "mean": round(statistics.fmean(ordered) * 1000, 2),
    }


async def isolation(sessions: int, tmpdir: Path) -> dict[str, object]:
    tempfile.tempdir = str(tmpdir)  # this process's world files, counted on their own
    server = fixture_server()
    app = server.setup_webserver()
    insts = instances()
    plans = tuple(PLANS[i % len(PLANS)] for i in range(math.ceil(sessions / len(insts))))
    peak = {"live": 0, "files": 0, "bytes": 0}
    done = asyncio.Event()

    async def watch() -> None:
        while not done.is_set():
            files = list(tmpdir.glob("rs-episode-*"))
            peak["live"] = max(peak["live"], server.live_sessions)
            peak["files"] = max(peak["files"], len(files))
            peak["bytes"] = max(peak["bytes"], sum(f.stat().st_size for f in files if f.exists()))
            await asyncio.sleep(0.02)

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rubberstamp", timeout=None)

    watcher = asyncio.create_task(watch())
    started = time.perf_counter()
    found = await check_isolation(lambda: GymDriver(client, "environment"), insts, sessions, plans, temp_dir=tmpdir)
    elapsed = time.perf_counter() - started
    done.set()
    await watcher
    return {
        "episodes": len(insts) * len(plans),
        "concurrency": sessions,
        "mismatches": [str(m) for m in found[:20]],
        "mismatch_count": len(found),
        "peak_live_worlds": peak["live"],
        "peak_world_files": peak["files"],
        "peak_world_mb": round(peak["bytes"] / 1e6, 1),
        "world_files_after": episode_files(tmpdir),
        "seconds": round(elapsed, 1),
    }


def latency(episodes: int, tmpdir: Path) -> dict[str, object]:
    port = next(p for p in PORTS if port_free(p))
    server = start_server(port, tmpdir, tmpdir / "server.log")
    timings: dict[str, list[float]] = {"seed_session": [], "tool_call": [], "verify": [], "close_session": []}
    insts = instances()
    try:
        with httpx.Client(base_url=server.url, timeout=60) as client:
            for i in range(episodes):
                inst = insts[i % len(insts)]
                client.cookies.clear()
                sid = f"latency-{uuid.uuid4().hex}"
                episode = {"rollout_id": sid, "attempt": 0}
                seed = {
                    "resources_session_id": sid,
                    "episode_id": episode,
                    "task_id": {"taskset": "rubberstamp_helpdesk", "task_id": inst.id},
                    "task_data": {"instance_id": inst.id},
                }
                t = time.perf_counter()
                client.post("/seed_session", json=seed).raise_for_status()
                timings["seed_session"].append(time.perf_counter() - t)
                output = []
                for k, (tool, args) in enumerate(plan_for(inst, "golden").calls):
                    t = time.perf_counter()
                    reply = client.post(f"/{tool}", json=args)
                    timings["tool_call"].append(time.perf_counter() - t)
                    reply.raise_for_status()
                    output += [function_call(f"c{k}", tool, args), function_call_output(f"c{k}", reply.text)]
                body = {"instance_id": inst.id} | {
                    "responses_create_params": dataset_row(inst.task_id, inst.seed)["responses_create_params"],
                    "response": response(output),
                }
                t = time.perf_counter()
                graded = client.post("/verify", json=body)
                timings["verify"].append(time.perf_counter() - t)
                graded.raise_for_status()
                assert graded.json()["reward"] == 1.0, graded.text
                t = time.perf_counter()
                client.post(
                    "/close_session", json={"resources_session_id": sid, "episode_id": episode}
                ).raise_for_status()
                timings["close_session"].append(time.perf_counter() - t)
    finally:
        stop_server(server)
    return {name: percentiles(samples) for name, samples in timings.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", type=int, default=2000, help="episodes in flight at once (0 skips)")
    parser.add_argument("--latency", type=int, default=1000, help="episodes timed over a real socket (0 skips)")
    args = parser.parse_args(argv)
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)  # one SQLite file per live world
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(max(soft, 65535), hard), hard))
    report: dict[str, object] = {
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        "python": platform.python_version(),
    }
    with tempfile.TemporaryDirectory(prefix="rs-gym-load-") as scratch:
        if args.latency:
            report["latency_ms"] = latency(args.latency, Path(scratch))
        if args.sessions:
            report["isolation"] = asyncio.run(isolation(args.sessions, Path(scratch)))
    print(json.dumps(report, indent=2))
    failed = args.sessions and (report["isolation"]["mismatch_count"] or report["isolation"]["world_files_after"])  # type: ignore[index]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
