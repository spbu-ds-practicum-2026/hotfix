import asyncio
import logging

from config import ProcessingSettings
from worker.ports import WorkerStore
from worker.runtime import sleep_until_stopped

logger = logging.getLogger("watchdog")


class Watchdog:
    def __init__(
        self,
        store: WorkerStore,
        settings: ProcessingSettings,
        unavailable_errors: tuple[type[Exception], ...],
    ) -> None:
        self._store = store
        self._settings = settings
        self._unavailable_errors = unavailable_errors

    async def run_once(self) -> tuple[int, int]:
        recovered = await self._store.recover_expired(
            self._settings.outbox_batch_size, self._settings.max_retry_attempts
        )
        requeued = await self._store.enqueue_due_retries(self._settings.outbox_batch_size)
        if recovered or requeued:
            logger.info("watchdog recovered=%s requeued=%s", len(recovered), requeued)
        return len(recovered), requeued

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.run_once()
            except self._unavailable_errors as error:
                logger.warning("watchdog storage unavailable: %s", error)
            await sleep_until_stopped(stop_event, self._settings.watchdog_interval_seconds)