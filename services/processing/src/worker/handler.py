import asyncio
import json
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from config import ProcessingSettings
from contracts.statuses import Verdict
from judge0.adapter import (
    Judge0Adapter,
    JudgeError,
    JudgeTimeoutError,
    to_verdict,
)
from retry.policy import attempts_exhausted, backoff_delay_seconds
from worker.ports import (
    AckDecision,
    Claim,
    DeadLetter,
    DeadLetterSink,
    Delivery,
    JudgingData,
    WorkerStore,
    WriteResultOutcome,
)

logger = logging.getLogger("worker")


class LeaseLostError(Exception):
    pass


def parse_submission_id(body: str) -> UUID:
    data = json.loads(body)
    value = data.get("submissionId") if isinstance(data, dict) else None
    if value is None:
        raise ValueError("submissionId is missing")
    return UUID(str(value))


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SubmissionHandler:
    def __init__(
        self,
        store: WorkerStore,
        judge: Judge0Adapter,
        dead_letters: DeadLetterSink,
        settings: ProcessingSettings,
        worker_id: UUID,
        unavailable_errors: tuple[type[Exception], ...],
        random_generator: random.Random | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store
        self._judge = judge
        self._dead_letters = dead_letters
        self._settings = settings
        self._worker_id = worker_id
        self._unavailable_errors = unavailable_errors
        self._random = random_generator or random.Random()
        self._clock = clock

    async def handle(self, delivery: Delivery) -> AckDecision:
        try:
            submission_id = parse_submission_id(delivery.body)
        except ValueError as error:
            logger.warning("invalid message eventId=%s: %s", delivery.message_id, error)
            await self._dead_letters.send(
                DeadLetter(
                    message_id=delivery.message_id,
                    body=delivery.body,
                    reason=f"invalid message: {error}",
                    submission_id=None,
                    attempts=0,
                )
            )
            return AckDecision.ACK
        try:
            return await self._process(delivery, submission_id)
        except self._unavailable_errors as error:
            logger.warning(
                "storage unavailable submissionId=%s eventId=%s: %s",
                submission_id,
                delivery.message_id,
                error,
            )
            return AckDecision.REQUEUE_WITH_DELAY

    async def _process(self, delivery: Delivery, submission_id: UUID) -> AckDecision:
        claim = await self._store.claim_submission(
            submission_id, self._worker_id, self._settings.lease_seconds
        )
        if claim is None:
            logger.info(
                "claim not acquired submissionId=%s eventId=%s",
                submission_id,
                delivery.message_id,
            )
            return AckDecision.ACK

        data = await self._store.load_for_judging(submission_id)
        if data is None:
            await self._fail_permanently(delivery, claim, "submission data not found")
            return AckDecision.ACK

        try:
            verdict = await self._run_judge(claim, data)
        except LeaseLostError:
            logger.warning(
                "lease lost submissionId=%s attemptId=%s fencingToken=%s",
                submission_id,
                claim.attempt_id,
                claim.fencing_token,
            )
            return AckDecision.ACK
        except JudgeError as error:
            await self._handle_judge_failure(delivery, claim, error)
            return AckDecision.ACK

        outcome = await self._store.write_result(
            submission_id, claim.fencing_token, verdict
        )
        if outcome is WriteResultOutcome.STALE_TOKEN:
            logger.warning(
                "stale result dropped submissionId=%s attemptId=%s fencingToken=%s",
                submission_id,
                claim.attempt_id,
                claim.fencing_token,
            )
        else:
            logger.info(
                "result written submissionId=%s attemptId=%s fencingToken=%s verdict=%s",
                submission_id,
                claim.attempt_id,
                claim.fencing_token,
                verdict.value,
            )
        return AckDecision.ACK

    async def _run_judge(self, claim: Claim, data: JudgingData) -> Verdict:
        judge_submission_id = await self._judge.submit(
            data.source_code, data.language, data.time_limit_ms, data.memory_limit_kb
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        last_heartbeat = started
        while True:
            result = await self._judge.get_result(judge_submission_id)
            verdict = to_verdict(result.status)
            if verdict is not None:
                return verdict
            now = loop.time()
            if now - started >= self._settings.judge0_total_timeout_seconds:
                raise JudgeTimeoutError("no final result within the time limit")
            if now - last_heartbeat >= self._settings.heartbeat_seconds:
                extended = await self._store.extend_lease(
                    claim.submission_id, claim.fencing_token, self._settings.lease_seconds
                )
                if not extended:
                    raise LeaseLostError()
                last_heartbeat = now
            await asyncio.sleep(self._settings.judge0_poll_interval_seconds)

    async def _handle_judge_failure(
        self, delivery: Delivery, claim: Claim, error: JudgeError
    ) -> None:
        reason = f"{type(error).__name__}: {error}"
        if attempts_exhausted(self._settings, claim.attempt_count):
            await self._fail_permanently(delivery, claim, reason)
            return
        delay = backoff_delay_seconds(self._settings, claim.attempt_count, self._random)
        next_attempt_at = self._clock() + timedelta(seconds=delay)
        marked = await self._store.mark_retrying(
            claim.submission_id, claim.fencing_token, reason, next_attempt_at
        )
        logger.warning(
            "retry scheduled=%s submissionId=%s attemptId=%s attempt=%s reason=%s",
            marked,
            claim.submission_id,
            claim.attempt_id,
            claim.attempt_count,
            reason,
        )

    async def _fail_permanently(
        self, delivery: Delivery, claim: Claim, reason: str
    ) -> None:
        marked = await self._store.mark_system_error(
            claim.submission_id, claim.fencing_token, reason
        )
        if not marked:
            return
        logger.error(
            "system error submissionId=%s attemptId=%s attempts=%s reason=%s",
            claim.submission_id,
            claim.attempt_id,
            claim.attempt_count,
            reason,
        )
        await self._dead_letters.send(
            DeadLetter(
                message_id=delivery.message_id,
                body=delivery.body,
                reason=reason,
                submission_id=claim.submission_id,
                attempts=claim.attempt_count,
            )
        )