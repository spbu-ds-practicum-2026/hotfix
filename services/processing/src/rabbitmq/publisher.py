from uuid import UUID

import aio_pika
from worker.ports import BrokerUnavailableError


class AioPikaPublisher:
    def __init__(self, exchange: aio_pika.abc.AbstractExchange) -> None:
        self._exchange = exchange

    async def publish_with_confirm(
        self,
        routing_key: str,
        message_id: UUID,
        body: str,
        timeout_seconds: float,
    ) -> bool:
        message = aio_pika.Message(
            body=body.encode("utf-8"),
            message_id=str(message_id),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        try:
            await self._exchange.publish(
                message, routing_key=routing_key, timeout=timeout_seconds
            )
        except aio_pika.exceptions.DeliveryError:
            return False
        except TimeoutError:
            return False
        except (aio_pika.exceptions.AMQPError, ConnectionError, OSError) as error:
            raise BrokerUnavailableError(str(error)) from error
        return True