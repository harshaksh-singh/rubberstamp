"""Test helpers: a trial's evidence built in-process (sidecar session + export hook), ready for the verifier."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from rs_suite.conformance import instance, plan_for
from rs_world.tasks import load_task

from rs_harbor.driver import play
from rs_harbor.export import export
from rs_harbor.generate import instance_files, write
from rs_harbor.grade import main as grade_main
from rs_harbor.layout import ERROR, METRICS, REWARD_TXT, folder_name
from rs_harbor.sidecar import LoggedSession


@dataclass
class Trial:
    task_dir: Path
    evidence: Path
    verifier: Path

    @property
    def instance_file(self) -> Path:
        return self.task_dir / "tests" / "instance.json"

    def grade(self) -> int:
        """Run the verifier entry point as tests/test.sh does; returns its exit code."""
        argv = ["--instance", str(self.instance_file), "--evidence", str(self.evidence), "--out", str(self.verifier)]
        try:
            grade_main(argv)
        except SystemExit as exc:
            return int(exc.code or 0)
        return 0

    @property
    def reward(self) -> str | None:
        path = self.verifier / REWARD_TXT
        return path.read_text() if path.exists() else None

    @property
    def metrics(self) -> dict:
        return json.loads((self.verifier / METRICS).read_text())

    @property
    def error(self) -> dict:
        return json.loads((self.verifier / ERROR).read_text())


def make_trial(root: Path, iid: str, plan_name: str | None = "golden", calls=None) -> Trial:
    """The task folder, the sidecar's evidence after playing `plan_name` (or the raw `calls`, or nothing for
    plan_name=None) through the MCP server, and the export hook's output. Not graded yet."""
    inst = instance(iid)
    task_dir = root / folder_name(inst.task_id, inst.seed)
    write(task_dir, {"": instance_files(inst)})
    evidence = root / "var-rs"
    with LoggedSession(load_task(inst.task_id, inst.seed), evidence) as session:
        if calls is not None:
            for tool, args in calls:
                session.call(tool, args)
        elif plan_name is not None:
            asyncio.run(play(session, plan_for(inst, plan_name)))
    export(evidence)
    return Trial(task_dir, evidence, root / "verifier")
