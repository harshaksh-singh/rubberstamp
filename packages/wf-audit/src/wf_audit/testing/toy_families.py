"""The loophole families bound to the toy world's declarations, as an OpenAdapt `--families-module`.

OpenAdapt's `load_families(module)` (behind `--families-module` in both CLIs) imports a module and reads its
`FAMILIES` tuple of `FaultFamily`. The loophole families need a world's declarations, and in openadapt-flow 3.4.0
nothing carries them from the target to a family, so this module binds the toy world's at import time:

    uv run wf-audit sweep --world toy --out /tmp/a --families-module wf_audit.testing.toy_families
"""

from __future__ import annotations

from ..families import loophole_families
from ..worlds import toy_world

FAMILIES = loophole_families(toy_world())
