"""Truth tables for the process layer (precedes / count) and the answer layer."""

import pytest
from wf_core.models import ToolCall
from wf_verify import AnswerCheck, CallPattern, ProcessRule, eq
from wf_verify.answer import check_answer, numbers_in
from wf_verify.process import check_process


def calls(*specs):
    """("tool", {args}) or ("tool", {args}, False) for a failed call."""
    out = []
    for i, s in enumerate(specs):
        tool, args, *rest = s
        out.append(ToolCall(idx=i, tool=tool, args=args, ok=rest[0] if rest else True))
    return out


APPROVE = ("approve", {"order": "o2"})
REFUND = ("refund", {"order": "o2"})
REFUND_OTHER = ("refund", {"order": "o9"})
FIRST = ProcessRule(
    id="approve_first",
    kind="precedes",
    a=CallPattern(tool="approve", args=(eq("/order", "o2"),)),
    b=CallPattern(tool="refund", args=(eq("/order", "o2"),)),
)
FIRST_ANY_OK = FIRST.model_copy(update={"a": FIRST.a.model_copy(update={"ok_only": False})})
SELF = ProcessRule(id="self", kind="precedes", a=CallPattern(tool="refund"), b=CallPattern(tool="refund"))
AT_MOST_ONE = ProcessRule(id="once", kind="count", a=CallPattern(tool="ref*"), max=1)
BETWEEN = ProcessRule(
    id="between", kind="count", a=CallPattern(tool="refund", args=(eq("/order", "o2"),)), min=1, max=2
)

PROCESS_CASES = [
    ("a then b", FIRST, calls(APPROVE, REFUND), True),
    ("b then a", FIRST, calls(REFUND, APPROVE), False),
    ("no b at all is vacuously fine", FIRST, calls(APPROVE), True),
    ("empty trajectory", FIRST, calls(), True),
    ("b on another record does not count", FIRST, calls(REFUND_OTHER), True),
    ("a on another record does not count", FIRST, calls(("approve", {"order": "o9"}), REFUND), False),
    ("a failed, so it does not count", FIRST, calls((*APPROVE, False), REFUND), False),
    ("a failed but ok_only=False", FIRST_ANY_OK, calls((*APPROVE, False), REFUND), True),
    ("b failed, so it does not count", FIRST, calls((*REFUND, False)), True),
    ("every b needs an earlier a", FIRST, calls(REFUND, APPROVE, REFUND), False),
    ("one a covers later bs", FIRST, calls(APPROVE, REFUND, REFUND), True),
    ("a call is not earlier than itself", SELF, calls(REFUND), False),
    ("count: zero within [0,1]", AT_MOST_ONE, calls(), True),
    ("count: glob matches", AT_MOST_ONE, calls(REFUND, ("refresh", {})), False),
    ("count: failed calls excluded", AT_MOST_ONE, calls(REFUND, (*REFUND, False)), True),
    ("count: below min", BETWEEN, calls(REFUND_OTHER), False),
    ("count: at max", BETWEEN, calls(REFUND, REFUND), True),
    ("count: above max", BETWEEN, calls(REFUND, REFUND, REFUND), False),
]


@pytest.mark.parametrize(("name", "rule", "trajectory", "passed"), PROCESS_CASES, ids=[c[0] for c in PROCESS_CASES])
def test_process_precedes_count_truth_table(name, rule, trajectory, passed):
    (check,) = check_process([rule], trajectory)
    assert check.id == f"process:{rule.id}"
    assert check.passed is passed


def test_precedes_detail_names_the_offending_call():
    (check,) = check_process([FIRST], calls(REFUND, APPROVE))
    assert "#0 refund" in check.detail


ORDER = FIRST.model_copy(update={"id": "approve_then_refund", "kind": "never_after"})
SUBMIT = ("submit", {"summary": "done"})
LAST = ProcessRule(id="submit_last", kind="never_after", a=CallPattern(tool="*"), b=CallPattern(tool="submit"))

NEVER_AFTER_CASES = [
    ("a then b", ORDER, calls(APPROVE, REFUND), True),
    ("b then a", ORDER, calls(REFUND, APPROVE), False),
    ("only a: order holds vacuously", ORDER, calls(APPROVE), True),
    ("only b: existence is the state layer's job", ORDER, calls(REFUND), True),
    ("an a after the first b fails", ORDER, calls(APPROVE, REFUND, APPROVE), False),
    ("a failed after b, so it does not count", ORDER, calls(REFUND, (*APPROVE, False)), True),
    ("a on another record does not count", ORDER, calls(REFUND, ("approve", {"order": "o9"})), True),
    ("glob a: nothing after the claim", LAST, calls(APPROVE, SUBMIT), True),
    ("glob a: a read after the claim fails", LAST, calls(SUBMIT, ("get", {})), False),
    ("glob a: a second claim fails", LAST, calls(SUBMIT, SUBMIT), False),
    ("glob a: a call is not after itself", LAST, calls(SUBMIT), True),
]


@pytest.mark.parametrize(
    ("name", "rule", "trajectory", "passed"), NEVER_AFTER_CASES, ids=[c[0] for c in NEVER_AFTER_CASES]
)
def test_never_after_truth_table(name, rule, trajectory, passed):
    (check,) = check_process([rule], trajectory)
    assert check.id == f"process:{rule.id}"
    assert check.passed is passed


def test_never_after_detail_names_the_late_call():
    (check,) = check_process([ORDER], calls(REFUND, APPROVE))
    assert "#1 approve" in check.detail and "#0 refund" in check.detail


ANSWER_CASES = [
    (AnswerCheck(kind="contains", value="Refunded"), "order o2 was refunded", True),
    (AnswerCheck(kind="contains", value="refunded"), "order o2 was not processed", False),
    (AnswerCheck(kind="regex", value=r"^Total: \d+$"), "Total: 42", True),
    (AnswerCheck(kind="regex", value=r"^Total: \d+$"), "total: 42", False),
    (AnswerCheck(kind="number", value=12000), "Refunded $12,000 today", True),
    (AnswerCheck(kind="number", value=120.5), "That is 120.50 dollars", True),
    (AnswerCheck(kind="number", value=120), "That is 120.50 dollars", False),
    (AnswerCheck(kind="number", value=120, tol=0.5), "That is 120.50 dollars", True),
    (AnswerCheck(kind="number", value=2), "order o2 is done", False),  # ids are not numbers
    (AnswerCheck(kind="number", value=-3), "balance is -3", True),
    (AnswerCheck(kind="contains", value="x"), None, False),
    (AnswerCheck(kind="number", value=0), None, False),
]


@pytest.mark.parametrize(("check", "answer", "passed"), ANSWER_CASES)
def test_answer_checks(check, answer, passed):
    (result,) = check_answer([check], answer)
    assert result.id == "answer:0"
    assert result.passed is passed


def test_numbers_in():
    assert numbers_in("$12,000.50 on 2026-11-22 for o2, v1.5") == [12000.5, 2026, 11, 22]
