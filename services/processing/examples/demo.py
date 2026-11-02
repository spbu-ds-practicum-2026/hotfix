import asyncio
import random
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import ConfigError, ProcessingSettings
from contracts.errors import contest_not_open, idempotency_key_reused, validation_error
from contracts.idempotency import (
    IdempotencyRecord,
    compute_payload_hash,
    decide_idempotency,
)
from contracts.statuses import SubmissionStatus, Verdict, can_transition, is_valid_state
from judge0.adapter import (
    JudgeStatus,
    JudgeTimeoutError,
    JudgeUnavailableError,
    to_verdict,
)
from judge0.fake_judge0 import FakeJudge0
from retry.policy import attempts_exhausted, backoff_delay_seconds

ENVIRONMENT = {
    "RABBITMQ_HOST": "rabbitmq", "RABBITMQ_PORT": "5672", "RABBITMQ_USER": "verdict",
    "RABBITMQ_PASSWORD": "change_me", "JUDGE0_URL": "http://judge0:2358",
    "LEASE_SECONDS": "30", "MAX_RETRY_ATTEMPTS": "3", "RETRY_BASE_DELAY_SECONDS": "2",
}


async def main() -> None:
    print("== переходы статусов ==")
    print("QUEUED -> PROCESSING:", can_transition(SubmissionStatus.QUEUED, SubmissionStatus.PROCESSING))
    print("PROCESSING -> COMPLETED:", can_transition(SubmissionStatus.PROCESSING, SubmissionStatus.COMPLETED))
    print("RETRYING -> QUEUED:", can_transition(SubmissionStatus.RETRYING, SubmissionStatus.QUEUED))
    print("COMPLETED -> PROCESSING (False):", can_transition(SubmissionStatus.COMPLETED, SubmissionStatus.PROCESSING))
    print("COMPLETED без вердикта допустимо (False):", is_valid_state(SubmissionStatus.COMPLETED, None))
    print("RETRYING без вердикта допустимо (True):", is_valid_state(SubmissionStatus.RETRYING, None))

    print("\n== ошибки ==")
    for error in (idempotency_key_reused(), contest_not_open(), validation_error("language must be cpp", "language")):
        print(error.http_status, error.to_body())

    print("\n== идемпотентность ==")
    contest_id, team_id, problem_id = uuid4(), uuid4(), uuid4()
    payload_hash = compute_payload_hash(contest_id, team_id, problem_id, "cpp", "int main(){}")
    other_hash = compute_payload_hash(contest_id, team_id, problem_id, "cpp", "int main(){return 1;}")
    record = IdempotencyRecord(payload_hash, uuid4())
    print("хеш:", payload_hash[:16] + "...")
    print("нет записи:", decide_idempotency(None, payload_hash).value)
    print("то же тело:", decide_idempotency(record, payload_hash).value)
    print("другое тело:", decide_idempotency(record, other_hash).value)

    print("\n== fake Judge0 ==")
    judge = FakeJudge0()
    for scenario in ("accepted", "wrong_answer", "compilation_error", "time_limit_exceeded", "runtime_error"):
        run_id = await judge.submit(f"int main(){{}}\n// @judge: {scenario}", "cpp", 2000, 262144)
        result = await judge.get_result(run_id)
        verdict = to_verdict(result.status)
        print(scenario, "->", verdict.value if verdict else "PENDING")
    run_id = await judge.submit("// @judge: accepted\n// @pending: 2", "cpp", 2000, 262144)
    first = (await judge.get_result(run_id)).status is JudgeStatus.PENDING
    second = (await judge.get_result(run_id)).status is JudgeStatus.PENDING
    third = to_verdict((await judge.get_result(run_id)).status)
    print("pending:", first, second, "затем", third.value if isinstance(third, Verdict) else third)
    judge.set_transient_failures(1)
    try:
        await judge.submit("x", "cpp", 2000, 262144)
    except JudgeUnavailableError as error:
        print("недоступен -> RETRYING:", error)
    run_id = await judge.submit("// @judge: timeout", "cpp", 2000, 262144)
    try:
        await judge.get_result(run_id)
    except JudgeTimeoutError as error:
        print("таймаут -> RETRYING:", error)

    print("\n== настройки и backoff ==")
    settings = ProcessingSettings.from_env(ENVIRONMENT)
    print("heartbeat, с:", settings.heartbeat_seconds)
    random_generator = random.Random(1)
    for attempt_count in range(1, 4):
        delay = backoff_delay_seconds(settings, attempt_count, random_generator)
        suffix = "  -> SYSTEM_ERROR и DLQ" if attempts_exhausted(settings, attempt_count) else ""
        print(f"попытка {attempt_count}: {delay:.2f} с{suffix}")
    try:
        ProcessingSettings.from_env({})
    except ConfigError as error:
        print("пустое окружение ->", error)


if __name__ == "__main__":
    asyncio.run(main())