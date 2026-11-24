"""OpenEnv's Task API (`/{env_name}/splits`, `/tasks`, `/num_tasks`, `/task`, `/task_range`) over the 500 instances."""

from __future__ import annotations

import httpx
import pytest
from rs_suite.conformance import instances

from rubberstamp_env.client import RubberstampEnv

KEYS = {"instance_id", "task_id", "seed", "split", "category"}


def post(server_url: str, route: str, body: dict) -> httpx.Response:
    return httpx.post(f"{server_url}/rubberstamp_env/{route}", json=body)


def test_splits_and_counts(server_url: str) -> None:
    assert httpx.get(f"{server_url}/list_environments").json() == ["rubberstamp_env"]
    splits = httpx.get(f"{server_url}/rubberstamp_env/splits").json()
    assert splits == [
        {"name": "train", "type": "train"},
        {"name": "dev", "type": "validation"},
        {"name": "test", "type": "test"},
    ]
    counts = {s["name"]: post(server_url, "num_tasks", {"split": s["name"]}).json()["num_tasks"] for s in splits}
    assert counts == {"train": 300, "dev": 80, "test": 120} and sum(counts.values()) == 500


@pytest.mark.parametrize("split", ["train", "dev", "test"])
def test_task_lists_follow_the_instance_order(server_url: str, split: str) -> None:
    tasks = post(server_url, "tasks", {"split": split}).json()["tasks"]
    assert [t["instance_id"] for t in tasks] == [i.id for i in instances(split)]
    assert all(set(t) == KEYS and t["split"] == split for t in tasks)
    window = post(server_url, "task_range", {"split": split, "start": 5, "stop": 9}).json()["tasks"]
    assert window == tasks[5:9]
    assert post(server_url, "task", {"split": split, "index": 7}).json()["task"] == tasks[7]


@pytest.mark.parametrize(("split", "index"), [("dev", 80), ("train", 300), ("test", -1)])
def test_an_out_of_range_index_is_a_400(server_url: str, split: str, index: int) -> None:
    assert post(server_url, "task", {"split": split, "index": index}).status_code == 400


def test_an_unknown_split_is_a_400(server_url: str) -> None:
    assert post(server_url, "num_tasks", {"split": "holdout"}).status_code == 400


def test_reset_by_split_and_index_matches_the_task_api(server_url: str) -> None:
    task = post(server_url, "task", {"split": "test", "index": 42}).json()["task"]
    with RubberstampEnv(base_url=server_url).sync() as env:
        assert env.reset(split="test", index=42).observation.instance_id == task["instance_id"]
