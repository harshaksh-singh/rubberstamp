"""P0.1 and P0.2: the rs-core, rs-verify, rs-world and rs-suite wheels, installed offline into a fresh venv with no
extras, load every task and spec from site-packages, pass the seed-42 conformance matrix (75 plans) and reproduce the
committed shortcut matrix, and bring in no rs-audit, fastmcp, mcp, TRL, transformers or peft (scripts/wheel_check.py).
"""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("wheel_check", ROOT / "scripts" / "wheel_check.py")
assert spec is not None and spec.loader is not None
wheel_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wheel_check)


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv on PATH")
def test_the_wheels_alone_load_every_task_and_spec_and_pass_the_seed_42_matrix(tmp_path):
    report = wheel_check.check(tmp_path)
    assert report["tasks"] == report["specs"] == 25 and report["plans_checked"] == 75, json.dumps(report, indent=1)
    assert report["conformance_mismatches"] == [] and report["shortcut_matrix_matches_committed"]
    assert "site-packages" in report["rs_suite_file"] and "site-packages" in report["rs_world_file"]
    assert report["forbidden_installed"] == [] and not any(report["importable"].values())
    assert set(report["installed"]) == {
        "annotated-types",
        "faker",
        "pydantic",
        "pydantic-core",
        "pyyaml",
        "rs-core",
        "rs-suite",
        "rs-verify",
        "rs-world",
        "typing-extensions",
        "typing-inspection",
    }
    assert report["ok"]
