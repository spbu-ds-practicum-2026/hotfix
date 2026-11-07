import aio_pika
from rabbitmq.names import DEAD_LETTER_QUEUE
from worker.ports import BrokerUnavailableError, DeadLetter


class AioPikaDeadLetterSink:
    def __init__(self, channel: aio_pika.abc.AbstractChannel) -> None:
        self._channel = channel

    async def send(self, dead_letter: DeadLetter) -> None:
        message = aio_pika.Message(
            body=dead_letter.body.encode("utf-8"),
            message_id=str(dead_letter.message_id),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={
                "x-reason": dead_letter.reason,
                "x-submission-id": str(dead_letter.submission_id or ""),
                "x-attempts": dead_letter.attempts,
            },
        )
        try:
            await self._channel.default_exchange.publish(
                message, routing_key=DEAD_LETTER_QUEUE
            )
        except (aio_pika.exceptions.AMQPError, ConnectionError, OSError) as error:
            raise BrokerUnavailableError(str(error)) from error