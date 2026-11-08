import asyncio
import json
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import ProcessingSettings  # noqa: E402
from contracts.statuses import SubmissionStatus, Verdict  # noqa: E402
from judge0.fake_judge0 import FakeJudge0  # noqa: E402
from worker.handler import SubmissionHandler  # noqa: E402
from worker.ports import (  # noqa: E402
    AffectedSubmission,
    Claim,
    DeadLetter,
    Delivery,
    JudgingData,
    SubmissionNotFoundError,
    WriteResultOutcome,
)
from worker.watchdog import Watchdog  # noqa: E402

ENVIRONMENT = {
    "RABBITMQ_HOST": "rabbitmq",
    "RABBITMQ_PORT": "5672",
    "RABBITMQ_USER": "verdict",
    "RABBITMQ_PASSWORD": "change_me",
    "JUDGE0_URL": "http://judge0:2358",
    "LEASE_SECONDS": "30",
    "MAX_RETRY_ATTEMPTS": "3",
    "RETRY_BASE_DELAY_SECONDS": "0",
    "JUDGE0_POLL_INTERVAL_SECONDS": "0.01",
    "JUDGE0_TOTAL_TIMEOUT_SECONDS": "1",
}


class ManualClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


@dataclass
class Row:
    source_code: str
    status: SubmissionStatus = SubmissionStatus.QUEUED
    verdict: Verdict | None = None
    sequence: int = 1
    fencing_token: int = 0
    attempt_count: int = 0
    lease_until: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error: str | None = None


class InMemoryWorkerStore:
    def __init__(self, clock: ManualClock, settings: ProcessingSettings) -> None:
        self.rows: dict[UUID, Row] = {}
        self.queue: asyncio.Queue[UUID] = asyncio.Queue()
        self._clock = clock
        self._settings = settings

    def add(self, source_code: str) -> UUID:
        submission_id = uuid4()
        self.rows[submission_id] = Row(source_code=source_code)
        self.queue.put_nowait(submission_id)
        return submission_id

    def _owns(self, row, fencing_token) -> bool:
        return (
            row.status is SubmissionStatus.PROCESSING
            and row.fencing_token == fencing_token
            and row.lease_until is not None
            and row.lease_until > self._clock()
        )

    async def claim_submission(self, submission_id, worker_id, lease_seconds):
        row = self.rows.get(submission_id)
        if row is None:
            raise SubmissionNotFoundError(str(submission_id))
        if row.status is not SubmissionStatus.QUEUED:
            return None
        row.status = SubmissionStatus.PROCESSING
        row.fencing_token += 1
        row.attempt_count += 1
        row.sequence += 1
        row.lease_until = self._clock() + timedelta(seconds=lease_seconds)
        row.next_attempt_at = None
        return Claim(submission_id, uuid4(), row.attempt_count, row.fencing_token)

    async def extend_lease(self, submission_id, fencing_token, lease_seconds):
        row = self.rows[submission_id]
        if not self._owns(row, fencing_token):
            return False
        row.lease_until = self._clock() + timedelta(seconds=lease_seconds)
        return True

    async def write_result(self, submission_id, fencing_token, verdict):
        row = self.rows[submission_id]
        if not self._owns(row, fencing_token):
            return WriteResultOutcome.STALE_TOKEN
        row.status = SubmissionStatus.COMPLETED
        row.verdict = verdict
        row.sequence += 1
        return WriteResultOutcome.SUCCESS

    async def mark_retrying(self, submission_id, fencing_token, reason, next_attempt_at):
        row = self.rows[submission_id]
        if not self._owns(row, fencing_token):
            return False
        row.status = SubmissionStatus.RETRYING
        row.next_attempt_at = next_attempt_at
        row.last_error = reason
        row.sequence += 1
        return True

    async def mark_system_error(self, submission_id, fencing_token, reason):
        row = self.rows[submission_id]
        if not self._owns(row, fencing_token):
            return False
        row.status = SubmissionStatus.SYSTEM_ERROR
        row.last_error = reason
        row.sequence += 1
        return True

    async def recover_expired(self, limit, max_attempts):
        affected = []
        for submission_id, row in self.rows.items():
            if row.status is SubmissionStatus.PROCESSING and row.lease_until < self._clock():
                exhausted = row.attempt_count >= max_attempts
                row.status = SubmissionStatus.SYSTEM_ERROR if exhausted else SubmissionStatus.RETRYING
                row.next_attempt_at = None if exhausted else self._clock()
                row.sequence += 1
                affected.append(AffectedSubmission(submission_id, row.status))
        return affected

    async def enqueue_due_retries(self, limit):
        count = 0
        for submission_id, row in self.rows.items():
            if row.status is SubmissionStatus.RETRYING and row.next_attempt_at <= self._clock():
                row.status = SubmissionStatus.QUEUED
                row.next_attempt_at = None
                row.sequence += 1
                self.queue.put_nowait(submission_id)
                count += 1
        return count

    async def load_for_judging(self, submission_id):
        return JudgingData(self.rows[submission_id].source_code, "cpp", 2000, 262144)


class DeadLetterList:
    def __init__(self) -> None:
        self.items: list[DeadLetter] = []

    async def send(self, dead_letter: DeadLetter) -> None:
        self.items.append(dead_letter)


def delivery_for(submission_id: UUID) -> Delivery:
    return Delivery(uuid4(), json.dumps({"schemaVersion": 1, "submissionId": str(submission_id)}))


async def main() -> None:
    settings = ProcessingSettings.from_env(ENVIRONMENT)
    clock = ManualClock()
    store = InMemoryWorkerStore(clock, settings)
    judge = FakeJudge0()
    dead_letters = DeadLetterList()
    handler = SubmissionHandler(
        store, judge, dead_letters, settings, uuid4(), (ConnectionError,), random.Random(1), clock
    )
    watchdog = Watchdog(store, settings, (ConnectionError,))

    async def drain() -> None:
        while True:
            await watchdog.run_once()
            if store.queue.empty():
                return
            while not store.queue.empty():
                await handler.handle(delivery_for(store.queue.get_nowait()))

    def show(title: str, submission_id: UUID) -> None:
        row = store.rows[submission_id]
        verdict = row.verdict.value if row.verdict else None
        print(f"{title:<42} status={row.status.value:<12} verdict={verdict} "
              f"attempts={row.attempt_count} token={row.fencing_token} sequence={row.sequence}")

    print("== пять вердиктов ==")
    for scenario in ("accepted", "wrong_answer", "compilation_error", "time_limit_exceeded", "runtime_error"):
        submission_id = store.add(f"int main(){{}}\n// @judge: {scenario}")
        await drain()
        show(scenario, submission_id)

    print("\n== ожидание результата (pending) ==")
    submission_id = store.add("// @judge: accepted\n// @pending: 3")
    await drain()
    show("pending x3, затем accepted", submission_id)

    print("\n== временный сбой Judge0: повтор и успех ==")
    judge.set_transient_failures(1)
    submission_id = store.add("// @judge: accepted")
    await drain()
    show("один сбой, затем accepted", submission_id)

    print("\n== постоянный отказ: SYSTEM_ERROR и DLQ ==")
    judge.set_unavailable(True)
    submission_id = store.add("// @judge: accepted")
    await drain()
    judge.set_unavailable(False)
    show("Judge0 недоступен 3 раза", submission_id)
    print("записей в DLQ:", len(dead_letters.items), "| причина:", dead_letters.items[-1].reason)

    print("\n== таймаут Judge0 вместо вердикта ==")
    submission_id = store.add("// @judge: timeout")
    await drain()
    show("timeout x3", submission_id)

    print("\n== дубликат сообщения ==")
    submission_id = store.add("// @judge: wrong_answer")
    message = delivery_for(store.queue.get_nowait())
    await handler.handle(message)
    before = store.rows[submission_id].sequence
    await handler.handle(message)
    show("повторная доставка после COMPLETED", submission_id)
    print("sequence не изменился:", store.rows[submission_id].sequence == before)

    print("\n== fencing: зависший worker A и новый worker B ==")
    submission_id = store.add("// @judge: accepted")
    store.queue.get_nowait()
    claim_a = await store.claim_submission(submission_id, uuid4(), settings.lease_seconds)
    clock.advance(settings.lease_seconds + 1)
    await watchdog.run_once()
    claim_b = await store.claim_submission(submission_id, uuid4(), settings.lease_seconds)
    stale = await store.write_result(submission_id, claim_a.fencing_token, Verdict.WRONG_ANSWER)
    fresh = await store.write_result(submission_id, claim_b.fencing_token, Verdict.ACCEPTED)
    print(f"токен A={claim_a.fencing_token}: {stale.value}; токен B={claim_b.fencing_token}: {fresh.value}")
    show("итог", submission_id)

    print("\n== посылки нет ни на одном шарде ==")
    before = len(dead_letters.items)
    decision = await handler.handle(delivery_for(uuid4()))
    print("решение:", decision.value, "| записей в DLQ добавилось:", len(dead_letters.items) - before,
          "| причина:", dead_letters.items[-1].reason)

    print("\n== неподдерживаемая schemaVersion ==")
    before = len(dead_letters.items)
    body = json.dumps({"schemaVersion": 2, "submissionId": str(uuid4())})
    decision = await handler.handle(Delivery(uuid4(), body))
    print("решение:", decision.value, "| записей в DLQ добавилось:", len(dead_letters.items) - before,
          "| причина:", dead_letters.items[-1].reason)

    print("\n== аренда истекла до записи результата ==")
    submission_id = store.add("// @judge: accepted")
    store.queue.get_nowait()
    expired_claim = await store.claim_submission(submission_id, uuid4(), settings.lease_seconds)
    clock.advance(settings.lease_seconds + 1)
    outcome = await store.write_result(submission_id, expired_claim.fencing_token, Verdict.ACCEPTED)
    print("запись с верным токеном после истечения аренды:", outcome.value)
    await drain()
    show("после watchdog и повторной проверки", submission_id)

    print("\n== повреждённое сообщение ==")
    bad = Delivery(uuid4(), "not json")
    decision = await handler.handle(bad)
    print("решение:", decision.value, "| записей в DLQ:", len(dead_letters.items))


if __name__ == "__main__":
    asyncio.run(main())