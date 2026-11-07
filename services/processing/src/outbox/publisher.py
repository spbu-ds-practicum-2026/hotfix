import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from config import ProcessingSettings
from worker.ports import BrokerUnavailableError, MessagePublisher
from worker.runtime import sleep_until_stopped

logger = logging.getLogger("outbox")


class OutboxSource(Protocol):
    async def get_pending_outbox_events(self, limit: int) -> list[Any]: ...

    async def mark_outbox_event_published(self, event_id: UUID) -> bool: ...

    async def record_outbox_publish_failure(
        self, event_id: UUID, error_message: str
    ) -> None: ...


class OutboxPublisher:
    def __init__(
        self,
        sources: Sequence[OutboxSource],
        publisher: MessagePublisher,
        settings: ProcessingSettings,
        unavailable_errors: tuple[type[Exception], ...],
    ) -> None:
        self._sources = list(sources)
        self._publisher = publisher
        self._settings = settings
        self._unavailable_errors = unavailable_errors

    async def publish_pending(self) -> int:
        published = 0
        for source in self._sources:
            events = await source.get_pending_outbox_events(self._settings.outbox_batch_size)
            for event in events:
                body = json.dumps(event.payload, ensure_ascii=False)
                try:
                    confirmed = await self._publisher.publish_with_confirm(
                        event.routing_key,
                        event.event_id,
                        body,
                        self._settings.publisher_confirm_timeout_seconds,
                    )
                except BrokerUnavailableError as error:
                    await source.record_outbox_publish_failure(event.event_id, str(error))
                    logger.warning("broker unavailable eventId=%s: %s", event.event_id, error)
                    return published
                if not confirmed:
                    await source.record_outbox_publish_failure(
                        event.event_id, "no publisher confirm"
                    )
                    logger.warning("no confirm eventId=%s", event.event_id)
                    continue
                await source.mark_outbox_event_published(event.event_id)
                published += 1
        return published

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            published = 0
            try:
                published = await self.publish_pending()
            except self._unavailable_errors as error:
                logger.warning("outbox storage unavailable: %s", error)
            if published == 0:
                await sleep_until_stopped(stop_event, self._settings.outbox_idle_seconds)