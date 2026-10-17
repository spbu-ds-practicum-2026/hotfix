"""Идемпотентность отправок (services/processing/src/contracts/idempotency.py)."""

from uuid import UUID

from contracts.idempotency import (
    IdempotencyDecision,
    IdempotencyRecord,
    compute_payload_hash,
    decide_idempotency,
)

CONTEST = UUID("00000000-0000-0000-0000-0000000000c1")
TEAM = UUID("00000000-0000-0000-0000-0000000000a1")
PROBLEM = UUID("00000000-0000-0000-0000-0000000000b1")
SUBMISSION = UUID("00000000-0000-0000-0000-0000000000d1")


def _hash(**overrides):
    args = {
        "contest_id": CONTEST,
        "team_id": TEAM,
        "problem_id": PROBLEM,
        "language": "cpp",
        "source_code": "int main() {}",
    }
    args.update(overrides)
    return compute_payload_hash(**args)


def test_hash_is_deterministic_sha256_hex():
    first = _hash()
    assert first == _hash()
    assert len(first) == 64
    int(first, 16)


def test_hash_changes_when_any_field_changes():
    base = _hash()
    other = UUID("00000000-0000-0000-0000-0000000000ff")
    assert _hash(contest_id=other) != base
    assert _hash(team_id=other) != base
    assert _hash(problem_id=other) != base
    assert _hash(language="java") != base
    assert _hash(source_code="int main() { return 1; }") != base


def test_no_record_means_new():
    assert decide_idempotency(None, _hash()) is IdempotencyDecision.NEW


def test_same_payload_is_replay():
    record = IdempotencyRecord(payload_hash=_hash(), submission_id=SUBMISSION)
    assert decide_idempotency(record, _hash()) is IdempotencyDecision.REPLAY


def test_different_payload_with_same_key_is_conflict():
    record = IdempotencyRecord(payload_hash=_hash(), submission_id=SUBMISSION)
    changed = _hash(source_code="другой код")
    assert decide_idempotency(record, changed) is IdempotencyDecision.CONFLICT
