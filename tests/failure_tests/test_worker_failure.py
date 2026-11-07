
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

handler_module = pytest.importorskip("worker.handler")
ports = pytest.importorskip("worker.ports")
config_module = pytest.importorskip("config")

from worker.handler import SubmissionHandler
from worker.ports import AckDecision, Delivery, WriteResultOutcome

SUBMISSION_ID = UUID("00000000-0000-0000-0000-000000000001")


def _settings(**overrides) -> object:
    values = {
        "rabbitmq_host": "localhost",
        "rabbitmq_port": 5672,
        "rabbitmq_user": "guest",
        "rabbitmq_password": "guest",
        "judge0_url": "http://fake",
        "lease_seconds": 60,
        "max_retry_attempts": 3,
        "retry_base_delay_seconds": 1.0,
        "retry_max_delay_seconds": 30.0,
        "judge0_total_timeout_seconds": 5.0,
        "judge0_poll_interval_seconds": 0.01,
        "publisher_confirm_timeout_seconds": 5.0,
        "watchdog_interval_seconds": 5.0,
        "outbox_batch_size": 50,
        "outbox_idle_seconds": 0.2,
        "prefetch_count": 4,
    }
    values.update(overrides)
    return config_module.ProcessingSettings(**values)


class InMemoryWorkerStore:
    """Двойник WorkerStore с семантикой захвата и fencing token из tr.md."""

    def __init__(self) -> None:
        self.rows: dict[UUID, dict] = {
            SUBMISSION_ID: {
                "status": "QUEUED",
                "fencing_token": 0,
                "attempt_count": 0,
                "lease_until": None,
                "next_attempt_at": None,
                "verdict": None,
                "worker_id": None,
            }
        }
        self.results: list[tuple[UUID, int, str]] = []

    async def claim_submission(
        self, submission_id: UUID, worker_id: UUID, lease_seconds: int
    ):
        row = self.rows[submission_id]
        if row["status"] != "QUEUED":
            return None
        row["status"] = "PROCESSING"
        row["fencing_token"] += 1
        row["attempt_count"] += 1
        row["lease_until"] = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        row["worker_id"] = worker_id
        return ports.Claim(
            submission_id=submission_id,
            attempt_id=uuid4(),
            attempt_count=row["attempt_count"],
            fencing_token=row["fencing_token"],
        )

    async def extend_lease(
        self, submission_id: UUID, fencing_token: int, lease_seconds: int
    ) -> bool:
        row = self.rows[submission_id]
        if row["fencing_token"] != fencing_token or row["status"] != "PROCESSING":
            return False
        row["lease_until"] = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        return True

    async def write_result(
        self, submission_id: UUID, fencing_token: int, verdict
    ) -> WriteResultOutcome:
        row = self.rows[submission_id]
        if (
            row["fencing_token"] != fencing_token
            or row["status"] != "PROCESSING"
            or row["lease_until"] is None
            or row["lease_until"] <= datetime.now(UTC)
        ):
            return WriteResultOutcome.STALE_TOKEN
        row["status"] = "COMPLETED"
        row["verdict"] = verdict.value
        self.results.append((submission_id, fencing_token, verdict.value))
        return WriteResultOutcome.SUCCESS

    async def mark_retrying(
        self, submission_id: UUID, fencing_token: int, reason: str, next_attempt_at
    ) -> bool:
        row = self.rows[submission_id]
        if row["fencing_token"] != fencing_token or row["status"] != "PROCESSING":
            return False
        row["status"] = "RETRYING"
        row["next_attempt_at"] = next_attempt_at
        return True

    async def mark_system_error(
        self, submission_id: UUID, fencing_token: int, reason: str
    ) -> bool:
        row = self.rows[submission_id]
        if row["fencing_token"] != fencing_token or row["status"] != "PROCESSING":
            return False
        row["status"] = "SYSTEM_ERROR"
        return True

    async def recover_expired(self, limit: int, max_attempts: int):
        affected = []
        for submission_id, row in self.rows.items():
            if (
                row["status"] == "PROCESSING"
                and row["lease_until"] is not None
                and row["lease_until"] <= datetime.now(UTC)
            ):
                row["status"] = "RETRYING"
                row["next_attempt_at"] = datetime.now(UTC)
                affected.append(
                    ports.AffectedSubmission(
                        submission_id, ports.SubmissionStatus.RETRYING
                    )
                )
        return affected

    async def enqueue_due_retries(self, limit: int) -> int:
        moved = 0
        for row in self.rows.values():
            if (
                row["status"] == "RETRYING"
                and row["next_attempt_at"] is not None
                and row["next_attempt_at"] <= datetime.now(UTC)
            ):
                row["status"] = "QUEUED"
                moved += 1
        return moved

    async def load_for_judging(self, submission_id: UUID):
        return ports.JudgingData(
            source_code="int main() {}",
            language="cpp",
            time_limit_ms=1000,
            memory_limit_kb=262144,
        )


class RecordingDeadLetterSink:
    def __init__(self) -> None:
        self.dead_letters = []

    async def send(self, dead_letter) -> None:
        self.dead_letters.append(dead_letter)


class FakeJudge:
    """Мгновенно успешная проверка."""

    async def submit(self, source_code, language, time_limit_ms, memory_limit_kb):
        return "run-1"

    async def get_result(self, judge_submission_id):
        from judge0.adapter import JudgeResult, JudgeStatus

        return JudgeResult(status=JudgeStatus.ACCEPTED)


def _delivery() -> Delivery:
    import json

    return Delivery(
        message_id=uuid4(),
        body=json.dumps({"submissionId": str(SUBMISSION_ID)}),
    )


def _handler(store, judge, dead_letters, settings=None) -> SubmissionHandler:
    return SubmissionHandler(
        store=store,
        judge=judge,
        dead_letters=dead_letters,
        settings=settings or _settings(),
        worker_id=uuid4(),
        unavailable_errors=(ConnectionError,),
    )


@pytest.mark.asyncio
async def test_happy_path_writes_result_once():
    store = InMemoryWorkerStore()

    decision = await _handler(store, FakeJudge(), RecordingDeadLetterSink()).handle(
        _delivery()
    )

    assert decision is AckDecision.ACK
    row = store.rows[SUBMISSION_ID]
    assert row["status"] == "COMPLETED"
    assert row["verdict"] == "ACCEPTED"
    assert len(store.results) == 1


@pytest.mark.asyncio
async def test_dead_worker_result_is_dropped_and_written_exactly_once():
    store = InMemoryWorkerStore()
    worker1, worker2 = uuid4(), uuid4()

    # worker 1 захватил посылку (token 1) и «умер» — аренда истекла
    claim1 = await store.claim_submission(SUBMISSION_ID, worker1, lease_seconds=0)
    store.rows[SUBMISSION_ID]["lease_until"] = datetime.now(UTC) - timedelta(seconds=1)

    # Watchdog: PROCESSING -> RETRYING -> QUEUED
    affected = await store.recover_expired(limit=10, max_attempts=3)
    assert len(affected) == 1
    assert await store.enqueue_due_retries(limit=10) == 1

    # worker 2 захватывает посылку (token 2) и завершает проверку
    claim2 = await store.claim_submission(SUBMISSION_ID, worker2, lease_seconds=60)
    outcome = await store.write_result(
        SUBMISSION_ID, claim2.fencing_token, ports.Verdict.ACCEPTED
    )
    assert outcome is WriteResultOutcome.SUCCESS

    # «оживший» worker 1 пытается записать результат со старым токеном
    stale = await store.write_result(
        SUBMISSION_ID, claim1.fencing_token, ports.Verdict.WRONG_ANSWER
    )

    assert stale is WriteResultOutcome.STALE_TOKEN
    row = store.rows[SUBMISSION_ID]
    assert row["status"] == "COMPLETED"
    assert row["verdict"] == "ACCEPTED"  # результат worker 2 не перезаписан
    assert len(store.results) == 1  # результат записан ровно один раз


@pytest.mark.asyncio
async def test_submission_in_retrying_is_not_claimed_before_next_attempt():
    store = InMemoryWorkerStore()
    worker = uuid4()

    claim = await store.claim_submission(SUBMISSION_ID, worker, lease_seconds=60)
    marked = await store.mark_retrying(
        SUBMISSION_ID,
        claim.fencing_token,
        "judge0 timeout",
        datetime.now(UTC) + timedelta(minutes=5),
    )
    assert marked is True

    # повторная доставка сообщения из очереди: захват невозможен
    assert await store.claim_submission(SUBMISSION_ID, worker, lease_seconds=60) is None

    # после наступления next_attempt_at Watchdog возвращает в QUEUED
    store.rows[SUBMISSION_ID]["next_attempt_at"] = datetime.now(UTC) - timedelta(
        seconds=1
    )
    assert await store.enqueue_due_retries(limit=10) == 1
    assert (
        await store.claim_submission(SUBMISSION_ID, worker, lease_seconds=60)
        is not None
    )


@pytest.mark.asyncio
async def test_handler_acks_when_claim_not_acquired():
    store = InMemoryWorkerStore()
    store.rows[SUBMISSION_ID]["status"] = "PROCESSING"  # уже захвачена другим

    decision = await _handler(store, FakeJudge(), RecordingDeadLetterSink()).handle(
        _delivery()
    )

    assert decision is AckDecision.ACK
    assert store.results == []


@pytest.mark.asyncio
async def test_expired_lease_write_is_rejected():
    store = InMemoryWorkerStore()
    worker = uuid4()

    claim = await store.claim_submission(SUBMISSION_ID, worker, lease_seconds=1)
    store.rows[SUBMISSION_ID]["lease_until"] = datetime.now(UTC) - timedelta(seconds=1)

    outcome = await store.write_result(
        SUBMISSION_ID, claim.fencing_token, ports.Verdict.ACCEPTED
    )

    assert outcome is WriteResultOutcome.STALE_TOKEN
    assert store.rows[SUBMISSION_ID]["status"] == "PROCESSING"


class NeverReadyJudge:
    """Проверка «зависает»: результат всегда PENDING, аренда не продлевается."""

    async def submit(self, source_code, language, time_limit_ms, memory_limit_kb):
        return "run-stuck"

    async def get_result(self, judge_submission_id):
        from judge0.adapter import JudgeResult, JudgeStatus

        return JudgeResult(status=JudgeStatus.PENDING)


class LeaseExpiringStore(InMemoryWorkerStore):
    """Продление аренды невозможно — worker 1 «остановился» во время проверки."""

    async def extend_lease(
        self, submission_id: UUID, fencing_token: int, lease_seconds: int
    ) -> bool:
        return False


@pytest.mark.asyncio
async def test_worker_stop_during_judging_recovered_and_completed_once():
    """Полный сценарий остановки worker на уровне обработчика.

    Worker 1 захватывает посылку, «зависает» на проверке и теряет аренду;
    Watchdog возвращает посылку в очередь; worker 2 завершает проверку.
    Результат записывается ровно один раз, dead-letter пуст.
    """
    store = LeaseExpiringStore()
    dead_letters = RecordingDeadLetterSink()
    # короткая аренда: heartbeat = lease / 3, опрос 0.05 c
    settings = _settings(
        lease_seconds=1,
        judge0_total_timeout_seconds=10.0,
        judge0_poll_interval_seconds=0.05,
    )

    # worker 1: захват и «остановка» во время проверки (LeaseLostError)
    decision1 = await _handler(store, NeverReadyJudge(), dead_letters, settings).handle(
        _delivery()
    )
    assert decision1 is AckDecision.ACK
    assert store.results == []  # результата нет
    assert dead_letters.dead_letters == []  # это не системная ошибка

    # Watchdog: PROCESSING (истёкшая аренда) -> RETRYING -> QUEUED
    store.rows[SUBMISSION_ID]["lease_until"] = datetime.now(UTC) - timedelta(seconds=1)
    affected = await store.recover_expired(limit=10, max_attempts=3)
    assert len(affected) == 1
    assert await store.enqueue_due_retries(limit=10) == 1

    # worker 2: повторная доставка сообщения, проверка завершается
    decision2 = await _handler(store, FakeJudge(), dead_letters, settings).handle(
        _delivery()
    )

    assert decision2 is AckDecision.ACK
    row = store.rows[SUBMISSION_ID]
    assert row["status"] == "COMPLETED"
    assert row["verdict"] == "ACCEPTED"
    # результат записан ровно один раз, с fencing token второго захвата
    assert len(store.results) == 1
    assert store.results[0][1] == 2
    assert dead_letters.dead_letters == []
