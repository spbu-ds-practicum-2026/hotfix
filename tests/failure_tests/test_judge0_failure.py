
from datetime import UTC, datetime
from uuid import uuid4

import pytest

handler_module = pytest.importorskip("worker.handler")
ports = pytest.importorskip("worker.ports")
fake_judge0 = pytest.importorskip("judge0.fake_judge0")
statuses = pytest.importorskip("contracts.statuses")

from worker.handler import SubmissionHandler
from worker.ports import AckDecision, Delivery

SUBMISSION_ID = uuid4()


def _settings(max_retry_attempts: int = 2):
    from config import ProcessingSettings

    return ProcessingSettings(
        rabbitmq_host="localhost",
        rabbitmq_port=5672,
        rabbitmq_user="guest",
        rabbitmq_password="guest",
        judge0_url="http://fake",
        lease_seconds=60,
        max_retry_attempts=max_retry_attempts,
        retry_base_delay_seconds=1.0,
        retry_max_delay_seconds=30.0,
        judge0_total_timeout_seconds=1.0,
        judge0_poll_interval_seconds=0.01,
        publisher_confirm_timeout_seconds=5.0,
        watchdog_interval_seconds=5.0,
        outbox_batch_size=50,
        outbox_idle_seconds=0.2,
        prefetch_count=4,
    )


class FlakyStore:
    

    def __init__(self) -> None:
        self.transitions: list[str] = []

    async def claim_submission(self, submission_id, worker_id, lease_seconds):
        self.transitions.append("CLAIM")
        return ports.Claim(
            submission_id=submission_id,
            attempt_id=uuid4(),
            attempt_count=1,
            fencing_token=1,
        )

    async def extend_lease(self, submission_id, fencing_token, lease_seconds):
        return True

    async def write_result(self, submission_id, fencing_token, verdict):
        self.transitions.append(f"COMPLETED:{verdict.value}")
        return ports.WriteResultOutcome.SUCCESS

    async def mark_retrying(
        self, submission_id, fencing_token, reason, next_attempt_at
    ):
        self.transitions.append(f"RETRYING:{reason}")
        return True

    async def mark_system_error(self, submission_id, fencing_token, reason):
        self.transitions.append(f"SYSTEM_ERROR:{reason}")
        return True

    async def recover_expired(self, limit, max_attempts):
        return []

    async def enqueue_due_retries(self, limit):
        return 0

    async def load_for_judging(self, submission_id):
        return ports.JudgingData(
            source_code="// @judge: timeout",
            language="cpp",
            time_limit_ms=1000,
            memory_limit_kb=262144,
        )


class DeadLetters:
    def __init__(self) -> None:
        self.items = []

    async def send(self, dead_letter):
        self.items.append(dead_letter)


def _delivery() -> Delivery:
    import json

    return Delivery(
        message_id=uuid4(),
        body=json.dumps({"submissionId": str(SUBMISSION_ID)}),
    )


def _handler(store, judge, dead_letters, settings) -> SubmissionHandler:
    return SubmissionHandler(
        store=store,
        judge=judge,
        dead_letters=dead_letters,
        settings=settings,
        worker_id=uuid4(),
        unavailable_errors=(ConnectionError,),
    )


@pytest.mark.asyncio
async def test_judge0_timeout_leads_to_retrying_not_wrong_answer():
    store = FlakyStore()
    dead_letters = DeadLetters()
    judge = fake_judge0.FakeJudge0()  # сценарий timeout задаётся маркером в коде

    decision = await _handler(
        store, judge, dead_letters, _settings(max_retry_attempts=3)
    ).handle(_delivery())

    assert decision is AckDecision.ACK
    # инфраструктурная ошибка не превращается в вердикт
    assert store.transitions == [
        "CLAIM",
        "RETRYING:JudgeTimeoutError: fake judge0: no result in time",
    ]
    assert dead_letters.items == []


@pytest.mark.asyncio
async def test_exhausted_attempts_lead_to_system_error_without_verdict():
    store = FlakyStore()
    dead_letters = DeadLetters()
    judge = fake_judge0.FakeJudge0()

    decision = await _handler(
        store, judge, dead_letters, _settings(max_retry_attempts=1)
    ).handle(_delivery())

    assert decision is AckDecision.ACK
    assert store.transitions == [
        "CLAIM",
        "SYSTEM_ERROR:JudgeTimeoutError: fake judge0: no result in time",
    ]
    assert len(dead_letters.items) == 1
    assert dead_letters.items[0].submission_id == SUBMISSION_ID
    # SYSTEM_ERROR — финальный статус без вердикта
    assert statuses.is_final(statuses.SubmissionStatus.SYSTEM_ERROR)
    assert not statuses.is_valid_state(
        statuses.SubmissionStatus.SYSTEM_ERROR, statuses.Verdict.ACCEPTED
    )


@pytest.mark.asyncio
async def test_transient_judge0_failure_recovers_to_completed():
    store = FlakyStore()
    dead_letters = DeadLetters()
    judge = fake_judge0.FakeJudge0()
    judge.set_transient_failures(1)  # первый submit падает, второй успешен

    decision = await _handler(
        store, judge, dead_letters, _settings(max_retry_attempts=3)
    ).handle(_delivery())

    assert decision is AckDecision.ACK
    assert store.transitions == [
        "CLAIM",
        "RETRYING:JudgeUnavailableError: fake judge0: connection refused",
    ]


@pytest.mark.asyncio
async def test_judge0_unavailable_is_retried_not_fatal():
    store = FlakyStore()
    judge = fake_judge0.FakeJudge0()
    judge.set_unavailable(True)

    decision = await _handler(
        store, judge, DeadLetters(), _settings(max_retry_attempts=3)
    ).handle(_delivery())

    assert decision is AckDecision.ACK
    assert store.transitions[0] == "CLAIM"
    assert store.transitions[1].startswith("RETRYING:JudgeUnavailableError")


def test_system_error_does_not_affect_standings():
    """SYSTEM_ERROR не создаёт вердикт-событие: standings не пересчитываются."""
    events_contract = pytest.importorskip("contracts.events")

    # verdict = null допустим только не для COMPLETED: у SYSTEM_ERROR вердикта нет
    assert statuses.is_valid_state(statuses.SubmissionStatus.SYSTEM_ERROR, None)

    # вердикт-событие физически невозможно собрать со статусом SYSTEM_ERROR:
    # контракт требует Literal[COMPLETED]
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        events_contract.SubmissionVerdictEvent(
            event_id=uuid4(),
            trace_id=uuid4(),
            occurred_at=datetime(2026, 10, 15, 10, 0, tzinfo=UTC),
            submission_id=uuid4(),
            sequence=1,
            attempt_id=uuid4(),
            fencing_token=1,
            status=statuses.SubmissionStatus.SYSTEM_ERROR,
            verdict=statuses.Verdict.ACCEPTED,
        )
