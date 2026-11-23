"""Pins the toy-world results: which planted blind spots the pre-training audit detects.

1. OpenAdapt's default families (design log Entry 4): `ignore_order` is missed.
2. The `extended` preset, with the loophole families from `wf_audit.families` (Entry 6).
3. The `full` preset: `extended` plus the schema families, one family per slot (Entry 8), and `repeat_existing`,
   which refuses every toy task (Entry 10: the toy world declares no repeatable create).
4. Every committed result directory regenerates byte for byte, `audit_features.json` included.

Changing any result is a deliberate act: update the design log with it.
"""

import importlib.util
import json
from pathlib import Path

import pytest
from wf_audit import load_reports, scorecard
from wf_audit.cli import main

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("audit_planted", ROOT / "scripts" / "audit_planted.py")
audit_planted = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_planted)


def test_planted_blind_spots_vs_openadapt_audit(tmp_path, capsys):
    matrix = audit_planted.main(str(tmp_path / "audit"))
    # strict baseline: only the hand-triaged free-text false alarm (changed email subject)
    assert matrix["strict"]["all_families"] == ["wrong_value"]
    detected = {v for v, row in matrix.items() if row["detected"]}
    missed = {v for v, row in matrix.items() if row["detected"] is False}
    assert detected == {"ignore_collateral", "count_at_least", "status_only", "trust_claim", "ignore_side_effects"}
    assert missed == {"ignore_order"}  # order-blind effect model: known OpenAdapt gap
    assert matrix["ignore_collateral"]["families_beyond_strict"] == ["unauthorized_side_effect"]
    assert matrix["count_at_least"]["families_beyond_strict"] == ["duplicate"]


def test_extended_families_detect_ignore_order_and_surface_over_strictness(tmp_path, capsys):
    out = tmp_path / "audit"
    matrix = audit_planted.main(str(out), families="extended")
    detected = {v for v, row in matrix.items() if row["detected"]}
    assert detected == {
        "ignore_collateral",
        "count_at_least",
        "status_only",
        "ignore_order",
        "trust_claim",
        "ignore_side_effects",
    }
    assert matrix["ignore_order"]["families_beyond_strict"] == ["wrong_order"]  # was MISSED with the defaults
    # the constrained-value family finds the 1-cent-off refund that status_only ignores (amount -1)
    assert matrix["status_only"]["families_beyond_strict"] == ["duplicate", "partial_write", "wrong_constrained_value"]

    strict = scorecard(out)["variants"]["strict"]
    fa = strict["false_accept"]
    assert (fa["wrong_order"]["hits"], fa["wrong_order"]["n"]) == (0, 1)
    # free-text false alarm: OpenAdapt's wrong_value (kept for comparison) still edits the email
    # subject and "hits" the strict grader; the constrained-value family skips the subject
    assert (fa["wrong_value"]["hits"], fa["wrong_value"]["n"]) == (1, 2)
    assert (fa["wrong_constrained_value"]["hits"], fa["wrong_constrained_value"]["n"]) == (0, 3)
    assert strict["blind_spots"] == ["wrong_value"]

    # Resolved over-strictness (design log, Entry 7): the refund_email prompt was ambiguous about order, so the
    # strict spec's refund-before-email rule rejected a "valid" reorder. The prompt now states the order explicitly
    # and the world declares it, so no independent reorder remains for that task and no variant is over-strict.
    # (Over-strictness detection itself stays demonstrated by the `exact_trajectory` variant in results/audit/toy.)
    fr = strict["false_reject"]["reorder_independent"]
    assert (fr["hits"], fr["n"]) == (0, 0)
    over = {v for v, row in matrix.items() if row["over_strict"]}
    assert over == set()


def test_full_preset_adds_field_coverage_and_collateral_through_any_tool(tmp_path, capsys):
    out = tmp_path / "audit"
    matrix = audit_planted.main(str(out), families="full")
    detected = {v for v, row in matrix.items() if row["detected"]}
    assert detected == {
        "ignore_collateral",
        "count_at_least",
        "status_only",
        "ignore_order",
        "trust_claim",
        "ignore_side_effects",
    }
    # strict baseline unchanged: only the triaged free-text alarm of the comparison family wrong_value
    assert matrix["strict"]["all_families"] == ["wrong_value"]
    assert {v for v, row in matrix.items() if row["over_strict"]} == set()
    # the 1-cent-short refund that status_only accepts, now found by the field that carries it
    assert matrix["status_only"]["families_beyond_strict"] == [
        "duplicate",
        "field_coverage:create_refund.amount_cents",
        "partial_write",
        "wrong_constrained_value",
    ]
    assert matrix["ignore_collateral"]["families_beyond_strict"] == [
        "schema_collateral:create_refund",
        "schema_collateral:request_approval",
        "schema_collateral:send_email",
        "schema_collateral:set_ticket_status",
        "unauthorized_side_effect",
    ]
    for variant in ("count_at_least", "ignore_order", "ignore_side_effects"):  # unchanged from the extended preset
        assert not any(":" in f for f in matrix[variant]["families_beyond_strict"]), variant

    # close_ticket's only write is set_ticket_status, and no other task writes a ticket: OpenAdapt's collateral family
    # has nothing to copy, while schema_collateral writes through the other tools
    cells = {(c["task"], c["family"]): c for c in load_reports(out)["ignore_collateral"]["cells"]}
    assert cells[("close_ticket", "unauthorized_side_effect")]["reason"] == "NO_RETARGETABLE_WRITE"
    on_close = {f for (t, f), c in cells.items() if t == "close_ticket" and c["status"] == "FALSE_ACCEPT"}
    assert on_close == {
        "schema_collateral:create_refund",
        "schema_collateral:request_approval",
        "schema_collateral:send_email",
    }

    # the strict grader catches every scored schema-family cell
    fa = scorecard(out)["variants"]["strict"]["false_accept"]
    schema = {
        f: (c["hits"], c["n"]) for f, c in fa.items() if f.partition(":")[0] in ("field_coverage", "schema_collateral")
    }
    assert schema == {
        "field_coverage:create_refund.amount_cents": (0, 2),
        "field_coverage:set_ticket_status.status": (0, 1),
        "schema_collateral:create_refund": (0, 3),
        "schema_collateral:request_approval": (0, 3),
        "schema_collateral:send_email": (0, 2),
        "schema_collateral:set_ticket_status": (0, 2),
    }
    # no toy tool has an optional arg, so extra_field refuses every task (the fixture test covers it)
    assert (fa["extra_field"]["n"], fa["extra_field"]["refused"]) == (0, 3)
    # no toy record predates an episode, so the toy world declares no repeatable create (the helpdesk test covers it)
    assert (fa["repeat_existing"]["n"], fa["repeat_existing"]["refused"]) == (0, 3)

    rows = {
        (r["task"], r["variant"]): r["families"] for r in json.loads((out / "audit_features.json").read_text())["rows"]
    }
    amount = {"create_refund.amount_cents": True}
    assert rows[("refund_email", "status_only")]["field_coverage"] == {
        "candidates": 1,
        "survived": 1,
        "survival": 1.0,
        "errors": 0,
        "slots": amount,
    }
    assert rows[("close_ticket", "ignore_collateral")]["schema_collateral"]["survival"] == 1.0
    strict_collateral = rows[("close_ticket", "strict")]["schema_collateral"]
    assert (strict_collateral["candidates"], strict_collateral["survived"]) == (3, 0)
    # trust_claim has 30 counterexamples, more than OpenAdapt's REPORT.md shows
    assert "`report.json` has them all" in (out / "SCORECARD.md").read_text()


HAND_WRITTEN = {"NOTES.md"}


def _regenerate(name, out):
    if name == "toy":  # the README's `wf-audit sweep --world toy --out results/audit/toy`
        assert main(["sweep", "--world", "toy", "--out", str(out), "-q"]) == 0
    else:
        preset = {"toy-planted": "default", "toy-planted-extended": "extended", "toy-planted-full": "full"}[name]
        audit_planted.main(str(out), families=preset)


@pytest.mark.parametrize("name", ["toy", "toy-planted", "toy-planted-extended", "toy-planted-full"])
def test_committed_results_regenerate_byte_for_byte(name, tmp_path, capsys):
    out = tmp_path / name
    _regenerate(name, out)
    generated = {p.name for p in out.iterdir() if p.is_file()}
    committed = ROOT / "results" / "audit" / name
    assert {p.name for p in committed.iterdir() if p.is_file()} - HAND_WRITTEN == generated  # raw/ is git-ignored
    for file in sorted(generated):
        assert (out / file).read_bytes() == (committed / file).read_bytes(), f"{name}/{file}"
