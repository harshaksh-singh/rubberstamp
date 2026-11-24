"""Strict argument types through the API (design log Entry 18, F15): rs-api forwards a call's JSON arguments to
`WorldSession.call`, so it inherits the world's refusal of a value of the wrong JSON type. `public: "false"` used to
be stored as an internal note while the recorded call said "false", which the strict grader read as a public reply."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from rs_api import create_app
from rs_world import golden
from rsapi_helpers import Api

TASK = "t04_resolve_with_comment"


@pytest.fixture(scope="module")
def api() -> Iterator[Api]:
    with TestClient(create_app(allowed_hosts=["testserver"])) as client:
        yield Api(client)


@pytest.mark.parametrize("public", ["false", "0", 0, 1])
def test_a_non_boolean_public_is_a_recorded_refusal(api: Api, public: object) -> None:
    ticket = next(a["ticket_id"] for t, a in golden(TASK).calls if t == "add_ticket_comment")
    info = api.start(TASK)
    sent = api.call(
        info["episode_id"], "add_ticket_comment", ticket_id=ticket, body="Replacement confirmed.", public=public
    )
    assert sent["ok"] is False and "valid boolean" in sent["error"]
    thread = api.call(info["episode_id"], "get_ticket", ticket_id=ticket)["result"]["comments"]
    assert not any(c["body"] == "Replacement confirmed." for c in thread)  # nothing was written
