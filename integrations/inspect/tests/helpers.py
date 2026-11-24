"""Helpers shared by the tests."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

from inspect_ai.log import EvalLog, EvalSample
from inspect_ai.model import ChatMessageTool
from inspect_ai.scorer import Score

from rs_inspect.episode import HelpdeskEpisode

INSPECT = Path(sys.executable).parent / "inspect"


def samples(log: EvalLog) -> dict[str, EvalSample]:
    return {str(s.id): s for s in log.samples or []}


def score(sample: EvalSample) -> Score:
    return (sample.scores or {})["helpdesk_graders"]


def is_unscored(value: object) -> bool:
    return isinstance(value, float) and math.isnan(value)


def episode(sample: EvalSample) -> HelpdeskEpisode:
    return sample.store_as(HelpdeskEpisode)


def tool_messages(sample: EvalSample) -> list[ChatMessageTool]:
    return [m for m in sample.messages if isinstance(m, ChatMessageTool)]


def inspect_cli(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run Inspect's own CLI (this environment's `inspect`) with its temp dir in `cwd`."""
    full = {**os.environ, "TMPDIR": str(cwd), **(env or {})}
    return subprocess.run(
        [str(INSPECT), *args], capture_output=True, text=True, env=full, cwd=cwd, timeout=300, check=False
    )
