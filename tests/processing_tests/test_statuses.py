"""Статусы, вердикты и допустимые переходы (services/processing/src/contracts/statuses.py)."""

import pytest
from contracts.statuses import (
    ALLOWED_TRANSITIONS,
    FINAL_STATUSES,
    SubmissionStatus,
    Verdict,
    can_transition,
    is_final,
    is_valid_state,
)

ALL_STATUSES = list(SubmissionStatus)


def test_status_values_are_fixed():
    assert [s.value for s in SubmissionStatus] == [
        "QUEUED",
        "PROCESSING",
        "RETRYING",
        "COMPLETED",
        "SYSTEM_ERROR",
    ]


def test_verdict_values_are_fixed():
    assert [v.value for v in Verdict] == [
        "ACCEPTED",
        "WRONG_ANSWER",
        "COMPILATION_ERROR",
        "TIME_LIMIT_EXCEEDED",
        "RUNTIME_ERROR",
    ]


def test_every_status_has_transition_rule():
    assert set(ALLOWED_TRANSITIONS) == set(SubmissionStatus)


@pytest.mark.parametrize(
    "current,target",
    [
        (SubmissionStatus.QUEUED, SubmissionStatus.PROCESSING),
        (SubmissionStatus.PROCESSING, SubmissionStatus.COMPLETED),
        (SubmissionStatus.PROCESSING, SubmissionStatus.RETRYING),
        (SubmissionStatus.PROCESSING, SubmissionStatus.SYSTEM_ERROR),
        (SubmissionStatus.RETRYING, SubmissionStatus.QUEUED),
        (SubmissionStatus.RETRYING, SubmissionStatus.PROCESSING),
        (SubmissionStatus.RETRYING, SubmissionStatus.SYSTEM_ERROR),
    ],
)
def test_allowed_transitions(current, target):
    assert can_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (SubmissionStatus.QUEUED, SubmissionStatus.COMPLETED),
        (SubmissionStatus.QUEUED, SubmissionStatus.RETRYING),
        (SubmissionStatus.QUEUED, SubmissionStatus.SYSTEM_ERROR),
        (SubmissionStatus.PROCESSING, SubmissionStatus.QUEUED),
        (SubmissionStatus.PROCESSING, SubmissionStatus.PROCESSING),
    ],
)
def test_forbidden_transitions(current, target):
    assert not can_transition(current, target)


@pytest.mark.parametrize(
    "final", [SubmissionStatus.COMPLETED, SubmissionStatus.SYSTEM_ERROR]
)
def test_final_status_cannot_change(final):
    assert is_final(final)
    assert final in FINAL_STATUSES
    for target in ALL_STATUSES:
        assert not can_transition(final, target)


@pytest.mark.parametrize(
    "status",
    [SubmissionStatus.QUEUED, SubmissionStatus.PROCESSING, SubmissionStatus.RETRYING],
)
def test_non_final_status(status):
    assert not is_final(status)


@pytest.mark.parametrize("verdict", list(Verdict))
def test_completed_requires_verdict(verdict):
    assert is_valid_state(SubmissionStatus.COMPLETED, verdict)
    assert not is_valid_state(SubmissionStatus.COMPLETED, None)


@pytest.mark.parametrize(
    "status",
    [
        SubmissionStatus.QUEUED,
        SubmissionStatus.PROCESSING,
        SubmissionStatus.RETRYING,
        SubmissionStatus.SYSTEM_ERROR,
    ],
)
def test_non_completed_must_not_have_verdict(status):
    assert is_valid_state(status, None)
    assert not is_valid_state(status, Verdict.ACCEPTED)
