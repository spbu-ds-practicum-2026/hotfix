import asyncio
import logging
import signal
from uuid import uuid4

import aio_pika
from config import ConfigError, load_settings
from judge0.fake_judge0 import FakeJudge0
from outbox.publisher import OutboxPublisher
from rabbitmq.consumer import QueueConsumer
from rabbitmq.dead_letter import AioPikaDeadLetterSink
from rabbitmq.publisher import AioPikaPublisher
from rabbitmq.topology import declare_topology
from storage.repositories import StorageUnavailableError
from storage.shard_router.router import ShardRouter
from worker.handler import SubmissionHandler
from worker.shared_store import ShardedWorkerStore
from worker.watchdog import Watchdog


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = load_settings()
    except ConfigError as error:
        raise SystemExit(str(error)) from error

    shard_router = ShardRouter.from_environment()
    connection = await aio_pika.connect_robust(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        login=settings.rabbitmq_user,
        password=settings.rabbitmq_password,
    )
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=settings.prefetch_count)
    topology = await declare_topology(channel)

    unavailable_errors = (StorageUnavailableError,)
    store = ShardedWorkerStore(shard_router)
    handler = SubmissionHandler(
        store,
        FakeJudge0(),
        AioPikaDeadLetterSink(channel),
        settings,
        uuid4(),
        unavailable_errors,
    )
    outbox = OutboxPublisher(
        shard_router.all_repositories(),
        AioPikaPublisher(topology.exchange),
        settings,
        unavailable_errors,
    )
    watchdog = Watchdog(store, settings, unavailable_errors)
    consumer = QueueConsumer(topology.queued_queue, handler, settings)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop_event.set)

    tasks = [
        asyncio.create_task(outbox.run_forever(stop_event)),
        asyncio.create_task(watchdog.run_forever(stop_event)),
        asyncio.create_task(consumer.run(stop_event)),
    ]
    await stop_event.wait()
    await asyncio.gather(*tasks, return_exceptions=True)
    await connection.close()
    await shard_router.dispose()


if __name__ == "__main__":
    asyncio.run(main())