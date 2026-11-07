from dataclasses import dataclass

import aio_pika
from rabbitmq.names import (
    DEAD_LETTER_QUEUE,
    EXCHANGE_NAME,
    QUEUED_QUEUE,
    QUEUED_ROUTING_KEY,
    VERDICT_QUEUE,
    VERDICT_ROUTING_KEY,
)


@dataclass(frozen=True)
class Topology:
    exchange: aio_pika.abc.AbstractExchange
    queued_queue: aio_pika.abc.AbstractQueue


async def declare_topology(channel: aio_pika.abc.AbstractChannel) -> Topology:
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
    )
    queued_queue = await channel.declare_queue(QUEUED_QUEUE, durable=True)
    await queued_queue.bind(exchange, routing_key=QUEUED_ROUTING_KEY)
    verdict_queue = await channel.declare_queue(VERDICT_QUEUE, durable=True)
    await verdict_queue.bind(exchange, routing_key=VERDICT_ROUTING_KEY)
    await channel.declare_queue(DEAD_LETTER_QUEUE, durable=True)
    return Topology(exchange=exchange, queued_queue=queued_queue)