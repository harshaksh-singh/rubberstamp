"""The project site shows only generated numbers: site/src/data/site.json must match what scripts/site_data.py builds
from the committed results and the suite. Regenerate it with `uv run python scripts/site_data.py`."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("site_data", ROOT / "scripts" / "site_data.py")
site_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_data)


def test_the_committed_site_data_is_current():
    assert site_data.OUT.read_text() == site_data.render(site_data.build())


def test_the_site_data_matches_the_committed_results():
    data = site_data.build()
    assert len(data["tasks"]) == 25 and len(data["flags"]) == 10
    caught = {p: sum(v["detected"] for v in data["audit"][p].values()) for p in ("default", "extended", "full")}
    assert caught == {"default": 7, "extended": 9, "full": 10}
    # per task, against each task's own blind spot; t21-t25 were written after the audit families
    assert data["own_flag"]["later_tasks"] == ["t21", "t22", "t23", "t24", "t25"]
    assert data["own_flag"]["flagged"] == {
        "default": {"all": 13, "later": 2},
        "extended": {"all": 14, "later": 2},
        "full": {"all": 20, "later": 4},
    }
    assert data["strict_hits"] == {"duplicate": 2, "wrong_value": 21}  # the 23 hand-triaged hits in TRIAGE.md
    assert data["training"]["world_seeds"] * len(data["tasks"]) == 500
    example = data["example"]["verdicts"]
    assert example["strict"]["passed"] is False and example[data["example"]["knob"]]["passed"] is True
    assert (example["strict"]["checks"], example["strict"]["failed_count"]) == (11, 2)  # the console screenshot
    for task in data["tasks"]:
        assert "strict" not in task["accepted_by"] and task["knob"] in task["accepted_by"]
