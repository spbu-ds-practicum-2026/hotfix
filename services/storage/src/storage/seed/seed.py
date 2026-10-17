"""Демо-данные: 1 соревнование, 2 команды, 2 задачи, 7 отправок + Outbox-события.

Повторный запуск безопасен: каждая вставка использует ON CONFLICT DO NOTHING.

    PYTHONPATH=services/storage/src python -m storage.seed.seed --shard 0

Сценарий для ICPC (старт 09:00 UTC):
    Alpha: A — ACCEPTED в 09:12; B — TLE в 10:05, SYSTEM_ERROR в 10:20, PROCESSING в 10:30
    Beta:  A — WRONG_ANSWER в 09:20, ACCEPTED в 09:45; B — QUEUED в 10:40
Ожидаемые standings: Alpha solved=1 penalty=12; Beta solved=1 penalty=45+20=65. SYSTEM_ERROR штраф не даёт.
"""

import argparse
import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from storage.database import create_database_engine
from storage.enums import SubmissionStatus, Verdict
from storage.outbox.events import (
    build_submission_queued_event,
    build_submission_verdict_event,
)
from storage.seed import seed_ids

CONTEST_STARTS_AT = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
CONTEST_ENDS_AT = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)

SOURCE_CODE = "#include <iostream>\nint main() {\n    std::cout << 42 << std::endl;\n    return 0;\n}\n"


@dataclass(frozen=True)
class SeedSubmission:
    name: str
    team_id: UUID
    problem_id: UUID
    minutes_after_start: int
    status: SubmissionStatus
    verdict: Verdict | None
    sequence: int
    fencing_token: int
    attempt_count: int
    last_error: str | None = None
    has_lease: bool = False
    queued_event_published: bool = True
    verdict_event_published: bool = True


SEED_SUBMISSIONS = [
    SeedSubmission(
        "s1",
        seed_ids.TEAM_ALPHA_ID,
        seed_ids.PROBLEM_A_ID,
        12,
        SubmissionStatus.COMPLETED,
        Verdict.ACCEPTED,
        3,
        1,
        1,
    ),
    SeedSubmission(
        "s2",
        seed_ids.TEAM_BETA_ID,
        seed_ids.PROBLEM_A_ID,
        20,
        SubmissionStatus.COMPLETED,
        Verdict.WRONG_ANSWER,
        3,
        1,
        1,
    ),
    SeedSubmission(
        "s3",
        seed_ids.TEAM_BETA_ID,
        seed_ids.PROBLEM_A_ID,
        45,
        SubmissionStatus.COMPLETED,
        Verdict.ACCEPTED,
        3,
        1,
        1,
    ),
    SeedSubmission(
        "s4",
        seed_ids.TEAM_ALPHA_ID,
        seed_ids.PROBLEM_B_ID,
        65,
        SubmissionStatus.COMPLETED,
        Verdict.TIME_LIMIT_EXCEEDED,
        3,
        1,
        1,
        verdict_event_published=False,
    ),
    SeedSubmission(
        "s5",
        seed_ids.TEAM_ALPHA_ID,
        seed_ids.PROBLEM_B_ID,
        80,
        SubmissionStatus.SYSTEM_ERROR,
        None,
        3,
        3,
        3,
        last_error="Judge0 недоступен: timeout",
    ),
    SeedSubmission(
        "s6",
        seed_ids.TEAM_ALPHA_ID,
        seed_ids.PROBLEM_B_ID,
        90,
        SubmissionStatus.PROCESSING,
        None,
        2,
        1,
        1,
        has_lease=True,
    ),
    SeedSubmission(
        "s7",
        seed_ids.TEAM_BETA_ID,
        seed_ids.PROBLEM_B_ID,
        100,
        SubmissionStatus.QUEUED,
        None,
        1,
        0,
        0,
        queued_event_published=False,
    ),
]

INSERT_CONTEST_SQL = text(
    """
    INSERT INTO contests (contest_id, name, starts_at, ends_at, is_frozen)
    VALUES (:contest_id, :name, :starts_at, :ends_at, false)
    ON CONFLICT (contest_id) DO NOTHING
    """
)
INSERT_TEAM_SQL = text(
    "INSERT INTO teams (team_id, contest_id, name) VALUES (:team_id, :contest_id, :name) ON CONFLICT (team_id) DO NOTHING"
)
INSERT_PROBLEM_SQL = text(
    """
    INSERT INTO problems (problem_id, contest_id, code, title, time_limit_ms, memory_limit_kb, test_config_reference)
    VALUES (:problem_id, :contest_id, :code, :title, :time_limit_ms, :memory_limit_kb, :test_config_reference)
    ON CONFLICT (problem_id) DO NOTHING
    """
)
INSERT_SUBMISSION_SQL = text(
    """
    INSERT INTO submissions (
        submission_id, contest_id, team_id, problem_id, trace_id, source_code, language, status, verdict,
        sequence, fencing_token, attempt_count, lease_until, worker_id, last_error, created_at, updated_at
    )
    VALUES (
        :submission_id, :contest_id, :team_id, :problem_id, :trace_id, :source_code, 'CPP', :status, :verdict,
        :sequence, :fencing_token, :attempt_count, :lease_until, :worker_id, :last_error, :created_at, :updated_at
    )
    ON CONFLICT (submission_id) DO NOTHING
    """
)
INSERT_IDEMPOTENCY_KEY_SQL = text(
    """
    INSERT INTO idempotency_keys (team_id, idempotency_key, payload_hash, submission_id, created_at)
    VALUES (:team_id, :idempotency_key, :payload_hash, :submission_id, :created_at)
    ON CONFLICT (team_id, idempotency_key) DO NOTHING
    """
)
INSERT_OUTBOX_EVENT_SQL = text(
    """
    INSERT INTO outbox_events (
        event_id, submission_id, trace_id, event_type, routing_key, schema_version, payload, created_at, published_at
    )
    VALUES (
        :event_id, :submission_id, :trace_id, :event_type, :routing_key, :schema_version, :payload,
        :created_at, :published_at
    )
    ON CONFLICT (event_id) DO NOTHING
    """
).bindparams(bindparam("payload", type_=JSONB))


async def seed(shard_index: int) -> None:
    engine = create_database_engine(shard_index)
    inserted: dict[str, int] = {
        "contests": 0,
        "teams": 0,
        "problems": 0,
        "submissions": 0,
        "idempotency_keys": 0,
        "outbox_events": 0,
    }
    try:
        async with engine.begin() as connection:
            inserted["contests"] += (
                await connection.execute(
                    INSERT_CONTEST_SQL,
                    {
                        "contest_id": seed_ids.CONTEST_ID,
                        "name": "VERDICT Demo Contest",
                        "starts_at": CONTEST_STARTS_AT,
                        "ends_at": CONTEST_ENDS_AT,
                    },
                )
            ).rowcount

            for team_id, name in (
                (seed_ids.TEAM_ALPHA_ID, "Alpha"),
                (seed_ids.TEAM_BETA_ID, "Beta"),
            ):
                inserted["teams"] += (
                    await connection.execute(
                        INSERT_TEAM_SQL,
                        {
                            "team_id": team_id,
                            "contest_id": seed_ids.CONTEST_ID,
                            "name": name,
                        },
                    )
                ).rowcount

            problems = (
                (
                    seed_ids.PROBLEM_A_ID,
                    "A",
                    "Sum of Two Numbers",
                    1000,
                    262144,
                    "tests/demo/a",
                ),
                (
                    seed_ids.PROBLEM_B_ID,
                    "B",
                    "Shortest Path",
                    2000,
                    262144,
                    "tests/demo/b",
                ),
            )
            for (
                problem_id,
                code,
                title,
                time_limit_ms,
                memory_limit_kb,
                test_config_reference,
            ) in problems:
                inserted["problems"] += (
                    await connection.execute(
                        INSERT_PROBLEM_SQL,
                        {
                            "problem_id": problem_id,
                            "contest_id": seed_ids.CONTEST_ID,
                            "code": code,
                            "title": title,
                            "time_limit_ms": time_limit_ms,
                            "memory_limit_kb": memory_limit_kb,
                            "test_config_reference": test_config_reference,
                        },
                    )
                ).rowcount

            payload_hash = hashlib.sha256(SOURCE_CODE.encode("utf-8")).hexdigest()
            for item in SEED_SUBMISSIONS:
                created_at = CONTEST_STARTS_AT + timedelta(
                    minutes=item.minutes_after_start
                )
                finished_at = created_at + timedelta(seconds=5)
                inserted["submissions"] += (
                    await connection.execute(
                        INSERT_SUBMISSION_SQL,
                        {
                            "submission_id": seed_ids.submission_id_for(item.name),
                            "contest_id": seed_ids.CONTEST_ID,
                            "team_id": item.team_id,
                            "problem_id": item.problem_id,
                            "trace_id": seed_ids.trace_id_for(item.name),
                            "source_code": SOURCE_CODE,
                            "status": item.status.value,
                            "verdict": item.verdict.value if item.verdict else None,
                            "sequence": item.sequence,
                            "fencing_token": item.fencing_token,
                            "attempt_count": item.attempt_count,
                            "lease_until": finished_at + timedelta(seconds=30)
                            if item.has_lease
                            else None,
                            "worker_id": seed_ids.stable_uuid("worker:demo")
                            if item.has_lease
                            else None,
                            "last_error": item.last_error,
                            "created_at": created_at,
                            "updated_at": finished_at,
                        },
                    )
                ).rowcount
                inserted["idempotency_keys"] += (
                    await connection.execute(
                        INSERT_IDEMPOTENCY_KEY_SQL,
                        {
                            "team_id": item.team_id,
                            "idempotency_key": seed_ids.idempotency_key_for(item.name),
                            "payload_hash": payload_hash,
                            "submission_id": seed_ids.submission_id_for(item.name),
                            "created_at": created_at,
                        },
                    )
                ).rowcount

                events = [
                    (
                        build_submission_queued_event(
                            submission_id=seed_ids.submission_id_for(item.name),
                            trace_id=seed_ids.trace_id_for(item.name),
                            occurred_at=created_at,
                            event_id=seed_ids.event_id_for(item.name, "queued"),
                            attempt_id=seed_ids.attempt_id_for(item.name, "queued"),
                        ),
                        item.queued_event_published,
                    )
                ]
                if item.status is SubmissionStatus.COMPLETED:
                    events.append(
                        (
                            build_submission_verdict_event(
                                submission_id=seed_ids.submission_id_for(item.name),
                                trace_id=seed_ids.trace_id_for(item.name),
                                occurred_at=finished_at,
                                submitted_at=created_at,
                                fencing_token=item.fencing_token,
                                status=item.status,
                                verdict=item.verdict,
                                sequence=item.sequence,
                                event_id=seed_ids.event_id_for(item.name, "verdict"),
                                attempt_id=seed_ids.attempt_id_for(
                                    item.name, "verdict"
                                ),
                            ),
                            item.verdict_event_published,
                        )
                    )
                for event, is_published in events:
                    inserted["outbox_events"] += (
                        await connection.execute(
                            INSERT_OUTBOX_EVENT_SQL,
                            {
                                "event_id": event.event_id,
                                "submission_id": event.submission_id,
                                "trace_id": event.trace_id,
                                "event_type": event.event_type,
                                "routing_key": event.routing_key,
                                "schema_version": event.schema_version,
                                "payload": event.payload,
                                "created_at": event.created_at,
                                "published_at": event.created_at + timedelta(seconds=1)
                                if is_published
                                else None,
                            },
                        )
                    ).rowcount
    finally:
        await engine.dispose()

    for table_name, count in inserted.items():
        print(f"{table_name}: добавлено {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка демо-данных VERDICT")
    parser.add_argument(
        "--shard", type=int, default=0, help="номер шарда (по умолчанию 0)"
    )
    arguments = parser.parse_args()
    asyncio.run(seed(arguments.shard))


if __name__ == "__main__":
    main()
