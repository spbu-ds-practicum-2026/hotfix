"""Демо-идентификаторы и синхронность enum'ов storage с контрактом processing."""

from uuid import UUID

from contracts import statuses as contract_statuses
from storage import enums as storage_enums
from storage.seed import seed_ids


def test_storage_status_values_match_processing_contract():
    assert [s.value for s in storage_enums.SubmissionStatus] == [
        s.value for s in contract_statuses.SubmissionStatus
    ]


def test_storage_verdict_values_match_processing_contract():
    assert [v.value for v in storage_enums.Verdict] == [
        v.value for v in contract_statuses.Verdict
    ]


def test_seed_ids_are_pinned():
    # Одинаковы на любой машине: на них опираются fixtures и ручная демонстрация.
    assert seed_ids.CONTEST_ID == UUID("ab5d057f-b8b1-564b-83a7-837e8b1b607f")
    assert seed_ids.TEAM_ALPHA_ID == UUID("e67f9e77-0da4-55b3-8ae0-96e6c09b0fd5")
    assert seed_ids.PROBLEM_A_ID == UUID("22e549cd-46f2-5bc0-8388-4a6b7a61bd93")
    assert seed_ids.submission_id_for("s1") == UUID(
        "8e7b91de-f487-5e89-8066-6bee21eb6dfd"
    )


def test_seed_ids_are_all_distinct():
    fixed = [
        seed_ids.CONTEST_ID,
        seed_ids.TEAM_ALPHA_ID,
        seed_ids.TEAM_BETA_ID,
        seed_ids.PROBLEM_A_ID,
        seed_ids.PROBLEM_B_ID,
    ]
    per_submission = [
        make(name)
        for name in seed_ids.SUBMISSION_NAMES
        for make in (
            seed_ids.submission_id_for,
            seed_ids.trace_id_for,
            seed_ids.idempotency_key_for,
        )
    ]
    events = [
        make(name, kind)
        for name in seed_ids.SUBMISSION_NAMES
        for kind in ("queued", "verdict")
        for make in (seed_ids.event_id_for, seed_ids.attempt_id_for)
    ]
    everything = fixed + per_submission + events
    assert len(set(everything)) == len(everything)


def test_seed_has_two_teams_two_problems_and_several_submissions():
    assert seed_ids.TEAM_ALPHA_ID != seed_ids.TEAM_BETA_ID
    assert seed_ids.PROBLEM_A_ID != seed_ids.PROBLEM_B_ID
    assert len(seed_ids.SUBMISSION_NAMES) >= 3
