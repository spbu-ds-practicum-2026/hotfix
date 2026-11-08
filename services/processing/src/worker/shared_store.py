from datetime import datetime
from uuid import UUID

from contracts.statuses import SubmissionStatus, Verdict
from storage import enums as storage_enums
from storage.shard_router.router import ShardRouter
from worker.ports import (
    AffectedSubmission,
    Claim,
    JudgingData,
    SubmissionNotFoundError,
    WriteResultOutcome,
)


class ShardedWorkerStore:
    def __init__(self, shard_router: ShardRouter) -> None:
        self._shard_router = shard_router

    async def claim_submission(
        self, submission_id: UUID, worker_id: UUID, lease_seconds: int
    ) -> Claim | None:
        repository = await self._shard_router.find_repository_for_submission(submission_id)
        if repository is None:
            raise SubmissionNotFoundError(str(submission_id))
        claimed = await repository.claim_submission(submission_id, worker_id, lease_seconds)
        if claimed is None:
            return None
        return Claim(
            submission_id=submission_id,
            attempt_id=claimed.attempt_id,
            attempt_count=claimed.attempt_count,
            fencing_token=claimed.fencing_token,
        )

    async def extend_lease(
        self, submission_id: UUID, fencing_token: int, lease_seconds: int
    ) -> bool:
        repository = await self._shard_router.find_repository_for_submission(submission_id)
        if repository is None:
            return False
        return await repository.extend_lease(submission_id, fencing_token, lease_seconds)

    async def write_result(
        self, submission_id: UUID, fencing_token: int, verdict: Verdict
    ) -> WriteResultOutcome:
        repository = await self._shard_router.find_repository_for_submission(submission_id)
        if repository is None:
            return WriteResultOutcome.STALE_TOKEN
        outcome = await repository.write_result(
            submission_id, fencing_token, storage_enums.Verdict(verdict.value)
        )
        return WriteResultOutcome(outcome.value)

    async def mark_retrying(
        self,
        submission_id: UUID,
        fencing_token: int,
        reason: str,
        next_attempt_at: datetime,
    ) -> bool:
        repository = await self._shard_router.find_repository_for_submission(submission_id)
        if repository is None:
            return False
        return await repository.mark_retrying(
            submission_id, fencing_token, reason, next_attempt_at
        )

    async def mark_system_error(
        self, submission_id: UUID, fencing_token: int, reason: str
    ) -> bool:
        repository = await self._shard_router.find_repository_for_submission(submission_id)
        if repository is None:
            return False
        return await repository.mark_system_error(submission_id, fencing_token, reason)

    async def recover_expired(
        self, limit: int, max_attempts: int
    ) -> list[AffectedSubmission]:
        affected: list[AffectedSubmission] = []
        for repository in self._shard_router.all_repositories():
            for item in await repository.recover_expired(limit, max_attempts):
                affected.append(
                    AffectedSubmission(
                        submission_id=item.submission_id,
                        status=SubmissionStatus(item.status.value),
                    )
                )
        return affected

    async def enqueue_due_retries(self, limit: int) -> int:
        total = 0
        for repository in self._shard_router.all_repositories():
            total += await repository.enqueue_due_retries(limit)
        return total

    async def load_for_judging(self, submission_id: UUID) -> JudgingData | None:
        repository = await self._shard_router.find_repository_for_submission(submission_id)
        if repository is None:
            return None
        data = await repository.load_for_judging(submission_id)
        if data is None:
            return None
        return JudgingData(
            source_code=data.source_code,
            language=data.language.value.lower(),
            time_limit_ms=data.time_limit_ms,
            memory_limit_kb=data.memory_limit_kb,
        )