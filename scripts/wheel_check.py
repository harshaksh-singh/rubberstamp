"""Build the rs-core, rs-verify, rs-world and rs-suite wheels, install them into a fresh venv offline, and check that
the wheels alone load every task and spec and pass the seed-42 conformance matrix (P0.1, P0.2).

1. `uv build --wheel --offline` each package into a temporary directory;
2. `uv venv` plus an offline install of the four wheels, no extras, with their third-party dependencies pinned to
   uv.lock (`uv export`), then `uv pip check` (every dependency the wheels declare is installed). The uv cache,
   warmed by `uv sync`, supplies pydantic, faker and pyyaml. (`uv pip install --offline dist/*.whl` alone cannot
   resolve offline: `uv sync` caches the locked wheels but not the index metadata a fresh resolution needs.)
3. `python -I` in that venv, from an empty directory: 25 tasks and 25 specs load from site-packages; the 75 plans
   (25 tasks x golden / shortcut / no-op on seed 42) pass `rs_suite.conformance.check` with the reference driver and
   reproduce the committed shortcut matrix; `rs_suite.instances`, `rs_suite.detectors` and `rs_suite.conformance`
   import; rs-audit, fastmcp, mcp, TRL, transformers and peft are not installed.

Usage: uv run python scripts/wheel_check.py [--keep]   (prints a JSON report; exit 1 on any failure)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("rs-core", "rs-verify", "rs-world", "rs-suite")
FORBIDDEN = ("trl", "transformers", "peft", "openadapt-flow", "fastmcp", "mcp", "rs-audit", "torch")

PROBE = r"""
import importlib.util, json, sys
from pathlib import Path

import rs_suite, rs_world
from rs_suite import load_spec, plans, task_ids
from rs_suite.conformance import ReferenceDriver, check, instances
from rs_suite.matrix import matrix_json
import rs_suite.detectors, rs_suite.instances

HEAVY = ("rs_audit", "fastmcp", "mcp", "trl", "transformers", "peft")
committed = json.loads(Path(sys.argv[1]).read_text())
ids = task_ids()
assert len(ids) == 25, ids
specs = [load_spec(t) for t in ids]
assert all(len(plans(t)) == 3 for t in ids)
mismatches = check(ReferenceDriver(), instances(seeds=(42,)))
fresh = matrix_json()
print(json.dumps({
    "python": sys.version.split()[0],
    "rs_world_file": rs_world.__file__,
    "rs_suite_file": rs_suite.__file__,
    "tasks": len(ids),
    "specs": len(specs),
    "plans_checked": 3 * len(instances(seeds=(42,))),
    "conformance_mismatches": [str(m) for m in mismatches],
    "shortcut_matrix_matches_committed": fresh == committed,
    "importable": {m: importlib.util.find_spec(m) is not None for m in HEAVY},
}))
"""


def run(*args: str | Path, cwd: Path | None = None) -> str:
    done = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, args))} failed ({done.returncode}):\n{done.stdout}\n{done.stderr}")
    return done.stdout


def check(work: Path) -> dict:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH")
    dist, venv, empty = work / "dist", work / "venv", work / "empty"
    empty.mkdir()
    for package in PACKAGES:
        run(uv, "build", "--wheel", "--offline", "--out-dir", dist, ROOT / "packages" / package, cwd=ROOT)
    wheels = sorted(dist.glob("*.whl"))
    run(uv, "venv", "--offline", "--python", "3.12", venv)
    python = venv / "bin" / "python"
    locked = run(
        uv, "export", "--frozen", "--package", "rs-suite", "--no-dev", "--no-emit-workspace", "--no-hashes", cwd=ROOT
    )
    requirements = work / "requirements.txt"
    requirements.write_text(locked + "".join(f"{wheel}\n" for wheel in wheels))
    # Offline when uv's cache holds the index metadata (a dev machine); else fetch the locked pins (a fresh CI cache).
    try:
        run(uv, "pip", "sync", "--offline", "--python", python, requirements)
    except RuntimeError as exc:
        if "network was disabled" not in str(exc):
            raise
        run(uv, "pip", "sync", "--python", python, requirements)
    run(uv, "pip", "check", "--python", python)  # the wheels' own Requires-Dist are all satisfied
    installed = {
        line.split()[0].lower(): line.split()[1]
        for line in run(uv, "pip", "list", "--python", python).splitlines()[2:]
        if line.strip()
    }
    probe = work / "probe.py"
    probe.write_text(PROBE)
    report = json.loads(run(python, "-I", probe, ROOT / "results" / "suite" / "shortcut_matrix.json", cwd=empty))
    report["wheels"] = [w.name for w in wheels]
    report["installed"] = installed
    report["forbidden_installed"] = sorted(set(FORBIDDEN) & set(installed))
    report["ok"] = (
        report["tasks"] == 25
        and report["specs"] == 25
        and report["plans_checked"] == 75
        and not report["conformance_mismatches"]
        and report["shortcut_matrix_matches_committed"]
        and not report["forbidden_installed"]
        and not any(report["importable"].values())
        and "site-packages" in report["rs_suite_file"]
        and "site-packages" in report["rs_world_file"]
    )
    return report


def main(keep: bool) -> int:
    work = Path(tempfile.mkdtemp(prefix="rs-wheels-"))
    try:
        report = check(work)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="keep the temporary build directory")
    sys.exit(main(parser.parse_args().keep))
