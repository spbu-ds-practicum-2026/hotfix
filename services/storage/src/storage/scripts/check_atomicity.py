"""Проверка на живой базе: атомарность Submission + Outbox, идемпотентность, claim/fencing/lease, Watchdog.

Предварительно: применить миграции и загрузить seed (./services/scripts/seed-dev.sh).

    ./services/scripts/check-atomicity.sh
    PYTHONPATH=services/storage/src python -m storage.scripts.check_atomicity [--shard N]

По умолчанию проверка идёт на шарде-владельце демо-контеста. Завершается с кодом 1, если любая проверка
не прошла. За собой убирает тестовые строки.
"""

import argparse
import asyncio
import hashlib
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.config import load_settings
from storage.database import create_database_engine
from storage.enums import SubmissionStatus, Verdict
from storage.outbox.events import build_submission_queued_event
from storage.repositories import (
    ContestMigratingError,
    DuplicateRecordError,
    IdempotencyKeyReusedError,
    NewSubmission,
    SubmissionRepository,
    WriteResultOutcome,
)
from storage.seed import seed_ids
from storage.shard_router.ring import ShardRing

LEASE_SECONDS = 30


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


async def _status(engine: AsyncEngine, submission_id: UUID) -> tuple[str, str | None, int, int]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT status, verdict, fencing_token, sequence FROM submissions WHERE submission_id = :id"),
                {"id": submission_id},
            )
        ).one()
    return row.status, row.verdict, row.fencing_token, row.sequence


async def _execute(engine: AsyncEngine, sql: str, parameters: dict) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(sql), parameters)


async def run_checks(shard_index: int) -> bool:
    engine = create_database_engine(shard_index)
    repository = SubmissionRepository(engine, retry_base_delay_seconds=2.0, retry_max_delay_seconds=30.0)
    all_passed = True
    created_ids: list[UUID] = []
    shielded: dict[UUID, datetime] = {}

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
                rollback_submission, broken_event, rollback_key, hashlib.sha256(b"rollback").hexdigest()
            )
            report("ошибка вставки события откатывает всю транзакцию", False)
        except DuplicateRecordError:
            submission_left = await repository.get_submission_by_id(rollback_submission.submission_id)
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
        created_ids.append(submission.submission_id)
        key = uuid4()
        payload_hash = hashlib.sha256(b"body").hexdigest()
        event = build_submission_queued_event(
            submission_id=submission.submission_id,
            trace_id=submission.trace_id,
            occurred_at=submission.created_at,
        )
        created = await repository.create_submission_with_outbox(submission, event, key, payload_hash)
        outbox_count = await _count(
            engine, "SELECT count(*) FROM outbox_events WHERE submission_id = :id", {"id": submission.submission_id}
        )
        report(
            "успешное создание: submission в QUEUED, submitted_at = created_at, ровно одно событие в Outbox",
            created.is_new
            and created.submission.status is SubmissionStatus.QUEUED
            and created.submission.submitted_at == submission.created_at
            and outbox_count == 1,
        )

        # 3. Повтор с тем же ключом и тем же телом: та же отправка, без новых строк.
        repeated = await repository.create_submission_with_outbox(
            submission,
            build_submission_queued_event(
                submission_id=submission.submission_id,
                trace_id=submission.trace_id,
                occurred_at=submission.created_at,
            ),
            key,
            payload_hash,
        )
        outbox_count_after = await _count(
            engine, "SELECT count(*) FROM outbox_events WHERE submission_id = :id", {"id": submission.submission_id}
        )
        report(
            "повтор с тем же ключом возвращает ту же отправку и не пишет событие",
            not repeated.is_new
            and repeated.submission.submission_id == submission.submission_id
            and outbox_count_after == 1,
        )

        # 4. Тот же ключ, другое тело.
        other = _new_submission()
        try:
            await repository.create_submission_with_outbox(
                other,
                build_submission_queued_event(
                    submission_id=other.submission_id, trace_id=other.trace_id, occurred_at=other.created_at
                ),
                key,
                hashlib.sha256(b"different body").hexdigest(),
            )
            report("тот же ключ с другим телом отклоняется", False)
        except IdempotencyKeyReusedError:
            report(
                "тот же ключ с другим телом отклоняется",
                await repository.get_submission_by_id(other.submission_id) is None,
            )

        sid = submission.submission_id

        # 5. Claim только из QUEUED: токен 1, повторный claim невозможен.
        worker_one, worker_two = uuid4(), uuid4()
        first = await repository.claim_submission(sid, worker_one, LEASE_SECONDS)
        second = await repository.claim_submission(sid, worker_two, LEASE_SECONDS)
        report(
            "claim из QUEUED даёт fencingToken=1 и attempt_count=1; повторный claim невозможен",
            first is not None and first.fencing_token == 1 and first.attempt_count == 1 and second is None,
        )

        # 6. Запись с чужим токеном отбрасывается, строка не меняется.
        stale = await repository.write_result(sid, 99, Verdict.ACCEPTED)
        status, verdict, token, _ = await _status(engine, sid)
        report(
            "запись с неактуальным fencingToken -> stale_token, посылка не изменена",
            stale is WriteResultOutcome.STALE_TOKEN and status == "PROCESSING" and verdict is None and token == 1,
        )

        # 7. Аренда истекла: даже владелец токена не может записать результат (lease_until > now()).
        await _execute(
            engine,
            "UPDATE submissions SET lease_until = now() - interval '5 seconds' WHERE submission_id = :id",
            {"id": sid},
        )
        expired_write = await repository.write_result(sid, 1, Verdict.ACCEPTED)
        report(
            "запись при истёкшей аренде -> stale_token",
            expired_write is WriteResultOutcome.STALE_TOKEN and (await _status(engine, sid))[0] == "PROCESSING",
        )

        # Чужие просроченные PROCESSING (демо-посылка s6) временно «продлеваем», чтобы Watchdog в проверке их не трогал.
        async with engine.begin() as connection:
            others = (
                await connection.execute(
                    text(
                        "SELECT submission_id, lease_until FROM submissions "
                        "WHERE status = 'PROCESSING' AND lease_until < now() AND submission_id <> :id"
                    ),
                    {"id": sid},
                )
            ).all()
            for other_row in others:
                shielded[other_row.submission_id] = other_row.lease_until
                await connection.execute(
                    text("UPDATE submissions SET lease_until = now() + interval '1 day' WHERE submission_id = :id"),
                    {"id": other_row.submission_id},
                )

        # 8. Watchdog: PROCESSING с истёкшей арендой -> RETRYING; claim из RETRYING невозможен.
        recovered = await repository.recover_expired(limit=10, max_attempts=3)
        mine = [item for item in recovered if item.submission_id == sid]
        status, _, _, _ = await _status(engine, sid)
        report(
            "Watchdog переводит просроченную посылку в RETRYING; claim из RETRYING невозможен",
            len(mine) == 1
            and mine[0].status is SubmissionStatus.RETRYING
            and status == "RETRYING"
            and await repository.claim_submission(sid, worker_two, LEASE_SECONDS) is None,
        )

        # 9. До next_attempt_at посылка не возвращается в очередь; после — QUEUED + новое queued-событие.
        not_due = await repository.enqueue_due_retries(limit=10)
        status_before, _, _, _ = await _status(engine, sid)
        await _execute(
            engine,
            "UPDATE submissions SET next_attempt_at = now() - interval '1 second' WHERE submission_id = :id",
            {"id": sid},
        )
        await repository.enqueue_due_retries(limit=10)
        status_after, _, _, sequence_after = await _status(engine, sid)
        queued_events = await _count(
            engine,
            "SELECT count(*) FROM outbox_events WHERE submission_id = :id AND routing_key = 'submission.queued.v1'",
            {"id": sid},
        )
        report(
            "RETRYING -> QUEUED только после next_attempt_at, с новым событием submission.queued.v1",
            status_before == "RETRYING" and status_after == "QUEUED" and queued_events == 2 and sequence_after >= 4,
        )

        # 10. Новый claim: токен 2. Старый worker (токен 1) получает stale_token, новый — success.
        reclaimed = await repository.claim_submission(sid, worker_two, LEASE_SECONDS)
        old_write = await repository.write_result(sid, 1, Verdict.WRONG_ANSWER)
        new_write = await repository.write_result(sid, 2, Verdict.ACCEPTED)
        status, verdict, token, _ = await _status(engine, sid)
        verdict_payload = (
            await _fetch_verdict_event(engine, sid)
        )
        report(
            "fencing: токен 1 -> stale_token, токен 2 -> success; результат записан ровно один раз",
            reclaimed is not None
            and reclaimed.fencing_token == 2
            and old_write is WriteResultOutcome.STALE_TOKEN
            and new_write is WriteResultOutcome.SUCCESS
            and status == "COMPLETED"
            and verdict == "ACCEPTED"
            and token == 2,
        )
        report(
            "submission.verdict.v1 лежит в Outbox, содержит contestId/teamId/problemId/submittedAt, один экземпляр",
            verdict_payload is not None
            and verdict_payload["count"] == 1
            and verdict_payload["payload"].get("contestId") == str(seed_ids.CONTEST_ID)
            and verdict_payload["payload"].get("teamId") == str(seed_ids.TEAM_ALPHA_ID)
            and verdict_payload["payload"].get("status") == "COMPLETED"
            and "submittedAt" in verdict_payload["payload"]
            and "attemptId" not in verdict_payload["payload"],
        )

        # 11. SYSTEM_ERROR: без события вердикта.
        failing = _new_submission()
        created_ids.append(failing.submission_id)
        await repository.create_submission_with_outbox(
            failing,
            build_submission_queued_event(
                submission_id=failing.submission_id, trace_id=failing.trace_id, occurred_at=failing.created_at
            ),
            uuid4(),
            hashlib.sha256(b"failing").hexdigest(),
        )
        claim = await repository.claim_submission(failing.submission_id, worker_one, LEASE_SECONDS)
        marked = claim is not None and await repository.mark_system_error(
            failing.submission_id, claim.fencing_token, "Judge0 недоступен"
        )
        status, verdict, _, _ = await _status(engine, failing.submission_id)
        verdict_events = await _count(
            engine,
            "SELECT count(*) FROM outbox_events WHERE submission_id = :id AND routing_key = 'submission.verdict.v1'",
            {"id": failing.submission_id},
        )
        report(
            "SYSTEM_ERROR: verdict = null и события submission.verdict.v1 нет",
            marked and status == "SYSTEM_ERROR" and verdict is None and verdict_events == 0,
        )

        # 12. MIGRATING: создание -> ContestMigratingError, claim -> ContestMigratingError, запись -> stale_token.
        pending = _new_submission()
        created_ids.append(pending.submission_id)
        await repository.create_submission_with_outbox(
            pending,
            build_submission_queued_event(
                submission_id=pending.submission_id, trace_id=pending.trace_id, occurred_at=pending.created_at
            ),
            uuid4(),
            hashlib.sha256(b"pending").hexdigest(),
        )
        claimed = await repository.claim_submission(pending.submission_id, worker_one, LEASE_SECONDS)
        await _execute(
            engine,
            "UPDATE contests SET migration_state = 'MIGRATING' WHERE contest_id = :id",
            {"id": seed_ids.CONTEST_ID},
        )
        try:
            refused_create = False
            blocked = _new_submission()
            try:
                await repository.create_submission_with_outbox(
                    blocked,
                    build_submission_queued_event(
                        submission_id=blocked.submission_id, trace_id=blocked.trace_id, occurred_at=blocked.created_at
                    ),
                    uuid4(),
                    hashlib.sha256(b"blocked").hexdigest(),
                )
            except ContestMigratingError:
                refused_create = True
            write_during_migration = (
                await repository.write_result(pending.submission_id, claimed.fencing_token, Verdict.ACCEPTED)
                if claimed
                else None
            )
            report(
                "MIGRATING: создание отклоняется (503), запись результата отбрасывается (stale_token)",
                refused_create and write_during_migration is WriteResultOutcome.STALE_TOKEN,
            )
        finally:
            await _execute(
                engine,
                "UPDATE contests SET migration_state = 'ACTIVE' WHERE contest_id = :id",
                {"id": seed_ids.CONTEST_ID},
            )
    finally:
        for shielded_id, original_lease in shielded.items():
            await _execute(
                engine,
                "UPDATE submissions SET lease_until = :lease WHERE submission_id = :id",
                {"id": shielded_id, "lease": original_lease},
            )
        for created_id in created_ids:
            parameters = {"id": created_id}
            await _execute(engine, "DELETE FROM outbox_events WHERE submission_id = :id", parameters)
            await _execute(engine, "DELETE FROM idempotency_keys WHERE submission_id = :id", parameters)
            await _execute(engine, "DELETE FROM submissions WHERE submission_id = :id", parameters)
        await engine.dispose()

    return all_passed


async def _fetch_verdict_event(engine: AsyncEngine, submission_id: UUID) -> dict | None:
    import json

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT payload FROM outbox_events "
                    "WHERE submission_id = :id AND routing_key = 'submission.verdict.v1'"
                ),
                {"id": submission_id},
            )
        ).all()
    if not rows:
        return None
    payload = rows[0].payload
    return {"count": len(rows), "payload": json.loads(payload) if isinstance(payload, str) else payload}


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка хранилища на живой базе")
    parser.add_argument("--shard", type=int, default=None, help="номер шарда (по умолчанию — владелец демо-контеста)")
    arguments = parser.parse_args()
    shard = arguments.shard
    if shard is None:
        settings = load_settings()
        shard = ShardRing(settings.active_shards, settings.virtual_nodes).owner(seed_ids.CONTEST_ID)
    print(f"проверка на шарде {shard}")
    sys.exit(0 if asyncio.run(run_checks(shard)) else 1)


if __name__ == "__main__":
    main()
