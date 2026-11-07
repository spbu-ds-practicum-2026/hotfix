import asyncio
import logging
from uuid import UUID, uuid4

import aio_pika
from config import ProcessingSettings
from worker.ports import AckDecision, Delivery, MessageHandler

logger = logging.getLogger("consumer")


def _message_uuid(message: aio_pika.abc.AbstractIncomingMessage) -> UUID:
    try:
        return UUID(str(message.message_id))
    except ValueError:
        return uuid4()


class QueueConsumer:
    def __init__(
        self,
        queue: aio_pika.abc.AbstractQueue,
        handler: MessageHandler,
        settings: ProcessingSettings,
    ) -> None:
        self._queue = queue
        self._handler = handler
        self._settings = settings

    async def run(self, stop_event: asyncio.Event) -> None:
        consumer_tag = await self._queue.consume(self._on_message)
        await stop_event.wait()
        await self._queue.cancel(consumer_tag)

    async def _on_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        delivery = Delivery(
            message_id=_message_uuid(message),
            body=message.body.decode("utf-8", errors="replace"),
            is_redelivered=bool(message.redelivered),
        )
        try:
            decision = await self._handler.handle(delivery)
        except Exception:
            logger.exception("handler failed eventId=%s", delivery.message_id)
            await message.nack(requeue=True)
            return
        if decision is AckDecision.ACK:
            await message.ack()
        elif decision is AckDecision.REQUEUE_WITH_DELAY:
            await asyncio.sleep(self._settings.retry_base_delay_seconds)
            await message.nack(requeue=True)
        else:
            await message.reject(requeue=False)