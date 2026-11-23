"""`SpecVerifier`: turns a `VerifierSpec` into a `rs_core.protocols.Verifier`."""

from __future__ import annotations

from collections.abc import Sequence

from rs_core.diff import diff
from rs_core.models import Check, Episode, SchemaInfo, Verdict

from .answer import check_answer
from .process import check_process
from .spec import VerifierSpec
from .state import check_state


def verdict_from(checks: Sequence[Check]) -> Verdict:
    """The verdict is the AND of every check; the reward is binary."""
    passed = all(c.passed for c in checks)
    return Verdict(passed=passed, reward=1.0 if passed else 0.0, checks=tuple(checks))


class SpecVerifier:
    """Checks an episode against a spec: state (via the world diff), process, answer.

    The schema is needed to diff snapshots (volatile fields, table kinds), so it is fixed
    at construction, where the spec's tables are also checked against it.
    """

    def __init__(self, spec: VerifierSpec, schema: SchemaInfo) -> None:
        unknown = spec.tables() - set(schema.tables)
        if unknown:
            raise ValueError(f"spec {spec.id} names tables missing from schema {schema.version}: {sorted(unknown)}")
        self.spec = spec
        self.schema = schema
        self.id = spec.id

    def verify(self, episode: Episode) -> Verdict:
        if episode.task.id != self.spec.task_id:
            error = f"spec {self.spec.id} is for task {self.spec.task_id}, episode is {episode.task.id}"
            return Verdict(passed=False, reward=0.0, error=error)
        checks: list[Check] = []
        if self.spec.state is not None:
            checks += check_state(self.spec.state, diff(episode.initial, episode.final, self.schema), episode.final)
        checks += check_process(self.spec.process, episode.trajectory.calls)
        checks += check_answer(self.spec.answer, episode.trajectory.final_answer)
        return verdict_from(checks)
