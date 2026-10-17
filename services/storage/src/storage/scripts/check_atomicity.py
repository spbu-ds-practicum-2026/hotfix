"""Проверка атомарности Submission + Outbox и идемпотентности на живой базе.

Предварительно: применить миграции и загрузить seed.

    ./scripts/check-atomicity.sh   (или PYTHONPATH=services/storage/src python -m storage.scripts.check_atomicity --shard 0)

Завершается с кодом 1, если любая проверка не прошла. За собой убирает тестовые строки.
"""

import argparse
import asyncio
import hashlib
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.database import create_database_engine
from storage.outbox.events import build_submission_queued_event
from storage.repositories import (
    DuplicateRecordError,
    IdempotencyKeyReusedError,
    NewSubmission,
    SubmissionRepository,
)
from storage.seed import seed_ids


async def _count(engine: AsyncEngine, sql: str, parameters: dict[str, UUID]) -> int:
    async with engine.connect() as connection:
        return (await connection.execute(text(sql), parameters)).scalar_one()


def _new_submission() -> NewSubmission:
    return NewSubmission(
        submission_id=uuid4(),
        contest_id=seed_ids.CONTEST_ID,
        team_id=seed_ids.TEAM_ALPHA_ID,
        problem_id=seed_ids.PROBLEM_A_ID,
        trace_id=uuid4(),
        source_code="int main() { return 0; }",
        created_at=datetime.now(UTC),
    )


async def run_checks(shard_index: int) -> bool:
    engine = create_database_engine(shard_index)
    repository = SubmissionRepository(engine)
    all_passed = True

    def report(title: str, passed: bool) -> None:
        nonlocal all_passed
        all_passed = all_passed and passed
        print(f"[{'PASS' if passed else 'FAIL'}] {title}")

    try:
        # 1. Событие с занятым event_id ломает вторую вставку: submission и ключ не должны остаться.
        rollback_submission = _new_submission()
        rollback_key = uuid4()
        broken_event = build_submission_queued_event(
            submission_id=rollback_submission.submission_id,
            trace_id=rollback_submission.trace_id,
            occurred_at=rollback_submission.created_at,
            event_id=seed_ids.event_id_for("s1", "queued"),
        )
        try:
            await repository.create_submission_with_outbox(
                rollback_submission,
                broken_event,
                rollback_key,
                hashlib.sha256(b"rollback").hexdigest(),
            )
            report("ошибка вставки события откатывает всю транзакцию", False)
        except DuplicateRecordError:
            submission_left = await repository.get_submission_by_id(
                rollback_submission.submission_id
            )
            key_count = await _count(
                engine,
                "SELECT count(*) FROM idempotency_keys WHERE team_id = :team_id AND idempotency_key = :key",
                {"team_id": seed_ids.TEAM_ALPHA_ID, "key": rollback_key},
            )
            report(
                "ошибка вставки события откатывает всю транзакцию",
                submission_left is None and key_count == 0,
            )

        # 2. Успешное создание: все три записи на месте.
        submission = _new_submission()
        key = uuid4()
        payload_hash = hashlib.sha256(b"body").hexdigest()
        event = build_submission_queued_event(
            submission_id=submission.submission_id,
            trace_id=submission.trace_id,
            occurred_at=submission.created_at,
        )
        created = await repository.create_submission_with_outbox(
            submission, event, key, payload_hash
        )
        outbox_count = await _count(
            engine,
            "SELECT count(*) FROM outbox_events WHERE submission_id = :id",
            {"id": submission.submission_id},
        )
        report(
            "успешное создание: submission в QUEUED и ровно одно событие в Outbox",
            created.is_new
            and created.submission.status.value == "QUEUED"
            and outbox_count == 1,
        )

        # 3. Повтор с тем же ключом и тем же телом: та же отправка, без новых строк.
        repeat_event = build_submission_queued_event(
            submission_id=submission.submission_id,
            trace_id=submission.trace_id,
            occurred_at=submission.created_at,
        )
        repeated = await repository.create_submission_with_outbox(
            submission, repeat_event, key, payload_hash
        )
        outbox_count_after = await _count(
            engine,
            "SELECT count(*) FROM outbox_events WHERE submission_id = :id",
            {"id": submission.submission_id},
        )
        report(
            "повтор с тем же ключом возвращает ту же отправку и не пишет событие",
            not repeated.is_new
            and repeated.submission.submission_id == submission.submission_id
            and outbox_count_after == 1,
        )

        # 4. Тот же ключ, другое тело.
        other = _new_submission()
        other_event = build_submission_queued_event(
            submission_id=other.submission_id,
            trace_id=other.trace_id,
            occurred_at=other.created_at,
        )
        try:
            await repository.create_submission_with_outbox(
                other, other_event, key, hashlib.sha256(b"different body").hexdigest()
            )
            report("тот же ключ с другим телом отклоняется", False)
        except IdempotencyKeyReusedError:
            report(
                "тот же ключ с другим телом отклоняется",
                await repository.get_submission_by_id(other.submission_id) is None,
            )

        # Уборка тестовых строк.
        async with engine.begin() as connection:
            parameters = {"id": submission.submission_id}
            await connection.execute(
                text("DELETE FROM outbox_events WHERE submission_id = :id"), parameters
            )
            await connection.execute(
                text("DELETE FROM idempotency_keys WHERE submission_id = :id"),
                parameters,
            )
            await connection.execute(
                text("DELETE FROM submissions WHERE submission_id = :id"), parameters
            )
    finally:
        await engine.dispose()

    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Проверка атомарности Submission + Outbox"
    )
    parser.add_argument("--shard", type=int, default=0)
    arguments = parser.parse_args()
    sys.exit(0 if asyncio.run(run_checks(arguments.shard)) else 1)


if __name__ == "__main__":
    main()
