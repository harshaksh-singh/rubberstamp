import os
import tempfile
from pathlib import Path

import pytest

os.environ["HARBOR_TELEMETRY"] = "0"  # harbor sends usage events unless told not to; tests never do


@pytest.fixture
def private_tmp(monkeypatch):
    """Temp files (episodes, trials) in a directory of their own: other xdist workers share the system temp dir.
    The world cache is filled first, so it stays where it was."""
    from rs_suite.instances import SEEDS
    from rs_world import prewarm

    prewarm(SEEDS)
    with tempfile.TemporaryDirectory(prefix="rs-harbor-test-") as tmp:
        monkeypatch.setattr(tempfile, "tempdir", tmp)
        yield Path(tmp)
