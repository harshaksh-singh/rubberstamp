"""Shared helpers for the rs-api tests: drive a plan through the HTTP API, and the references it must match."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import rs_suite
from fastapi.testclient import TestClient
from rs_core.models import Episode
from rs_core.protocols import Verifier
from rs_suite import answer_is_claim, build_spec, claim_pattern
from rs_suite.suite import NOOP_SUMMARY
from rs_verify import Leniency, lenient_verifier
from rs_world import SCHEMA, WorldExecutor, golden, load_task, shortcut
from rs_world.oracle_kit import Plan
from rs_world.tasks import task_file

CLAIM = "submit_resolution"
EXECUTOR = WorldExecutor()


def own_seed(task_id: str) -> int:
    return task_file(task_id).seed


def instance_plans(task_id: str, seed: int) -> dict[str, Plan]:
    """golden, shortcut and no-op of the instance: rs-suite's own on the task's seed."""
    if seed == own_seed(task_id):
        return rs_suite.plans(task_id)
    return {"golden": golden(task_id, seed), "shortcut": shortcut(task_id, seed), "noop": Plan.of(summary=NOOP_SUMMARY)}


def reference_episode(task_id: str, seed: int, plan: Plan) -> Episode:
    """The plan replayed without the API: rs_suite.episode on the task's seed, the executor on any other."""
    if seed == own_seed(task_id):
        return rs_suite.episode(task_id, plan)
    return EXECUTOR.run(load_task(task_id, seed), plan.tool_calls(), plan.answer)


def reference_verifier(task_id: str, seed: int, variant: str) -> Verifier:
    """rs-suite's verifier on the task's seed; on another seed the same construction over that instance's spec
    (as rs-train's HelpdeskRegistry builds it), written out here independently of rs_api.grading."""
    if seed == own_seed(task_id):
        return rs_suite.verifier(task_id, variant)
    flags = () if variant == "strict" else (Leniency(variant),)
    return lenient_verifier(
        build_spec(task_id, seed),
        SCHEMA,
        *flags,
        claim_pattern=claim_pattern(task_id),
        answer_is_claim=answer_is_claim(task_id),
    )


@dataclass(frozen=True)
class Driven:
    info: dict[str, Any]
    responses: list[dict[str, Any]]  # one per work call (the claim excluded)
    submitted: dict[str, Any]  # the /submit response: {call, grade}
    trajectory: dict[str, Any]

    @property
    def grade(self) -> dict[str, Any]:
        assert self.submitted["grade"] is not None, self.submitted["call"]
        return self.submitted["grade"]

    def verdict(self, variant: str) -> dict[str, Any]:
        grade = self.grade
        if variant == "strict":
            return grade["strict"]
        return next(v for v in grade["variants"] if v["variant"] == variant)


class Api:
    """The API through a TestClient, with JSON in and out, as a browser or an HTTP agent would use it."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def ok(self, method: str, path: str, expect: int = 200, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        assert response.status_code == expect, (method, path, response.status_code, response.text)
        return response.json() if response.content else None

    def start(self, task_id: str, seed: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"task_id": task_id} if seed is None else {"task_id": task_id, "seed": seed}
        return self.ok("POST", "/episodes", 201, json=body)

    def call(self, episode_id: str, tool: str, **args: Any) -> dict[str, Any]:
        return self.ok("POST", f"/episodes/{episode_id}/calls", json={"tool": tool, "args": args})

    def submit(self, episode_id: str, summary: str) -> dict[str, Any]:
        return self.ok("POST", f"/episodes/{episode_id}/submit", json={"summary": summary})

    def trajectory(self, episode_id: str) -> dict[str, Any]:
        return self.ok("GET", f"/episodes/{episode_id}/trajectory")

    def drive(self, task_id: str, seed: int, plan: Plan) -> Driven:
        """Every work call through /calls, then the claim through /submit, as the UI does."""
        *work, (claim_tool, claim_args) = plan.calls
        assert claim_tool == CLAIM, f"a plan ends with {CLAIM}"
        info = self.start(task_id, seed)
        episode_id = info["episode_id"]
        responses = [self.call(episode_id, tool, **args) for tool, args in work]
        submitted = self.submit(episode_id, claim_args["summary"])
        return Driven(info, responses, submitted, self.trajectory(episode_id))
