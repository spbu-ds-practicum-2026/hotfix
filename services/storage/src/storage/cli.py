"""Команды хранилища: python -m storage.cli <migrate|seed|check|distribution|rebalance>.

    PYTHONPATH=services/storage/src python -m storage.cli migrate                 # миграции на всех шардах
    PYTHONPATH=services/storage/src python -m storage.cli migrate --shard 2       # только на одном шарде
    PYTHONPATH=services/storage/src python -m storage.cli seed                    # повторяемые данные
    PYTHONPATH=services/storage/src python -m storage.cli check                   # подключение и ревизия схемы
    PYTHONPATH=services/storage/src python -m storage.cli distribution            # контестов на каждом шарде
    PYTHONPATH=services/storage/src python -m storage.cli rebalance add-shard 2 --dry-run
    PYTHONPATH=services/storage/src python -m storage.cli rebalance add-shard 2
    PYTHONPATH=services/storage/src python -m storage.cli rebalance remove-shard 1
"""

import argparse
import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from storage.config import load_settings
from storage.rebalancer import Rebalancer
from storage.rebalancer.rebalancer import RebalanceError
from storage.seed.seed import seed
from storage.shard_router.router import ShardRouter

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"  # services/storage/alembic.ini


def _alembic_config(shard_id: int) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["shard"] = shard_id
    return config


def _migrate(arguments: argparse.Namespace) -> int:
    settings = load_settings()
    shard_ids = [arguments.shard] if arguments.shard is not None else sorted(settings.shards)
    for shard_id in shard_ids:
        if shard_id not in settings.shards:
            print(f"шард {shard_id} не настроен в .env", file=sys.stderr)
            return 2
        if arguments.downgrade:
            command.downgrade(_alembic_config(shard_id), arguments.downgrade)
        else:
            command.upgrade(_alembic_config(shard_id), "head")
        shard = settings.shards[shard_id]
        print(f"шард {shard_id}: миграции применены ({shard.host}:{shard.port}/{shard.database})")
    return 0


async def _check() -> int:
    router = ShardRouter.from_environment()
    ok = True
    try:
        for shard_id in router.configured_shard_ids:
            try:
                async with router.engine(shard_id).connect() as connection:
                    revision = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
                print(f"шард {shard_id}: OK, ревизия {revision}")
            except Exception as error:  # noqa: BLE001 - диагностическая команда
                ok = False
                print(f"шард {shard_id}: ОШИБКА {type(error).__name__}: {error}")
    finally:
        await router.dispose()
    return 0 if ok else 1


async def _distribution() -> int:
    settings = load_settings()
    rebalancer = Rebalancer(settings)
    try:
        for shard_id, count in (await rebalancer.distribution()).items():
            marker = "" if shard_id in settings.active_shards else " (не в ACTIVE_SHARDS)"
            print(f"шард {shard_id}: контестов {count}{marker}")
    finally:
        await rebalancer.router.dispose()
    return 0


async def _rebalance(arguments: argparse.Namespace) -> int:
    settings = load_settings()
    rebalancer = Rebalancer(settings)
    try:
        if arguments.action == "add-shard":
            report = await rebalancer.add_shard(arguments.shard, dry_run=arguments.dry_run)
        else:
            report = await rebalancer.remove_shard(arguments.shard, dry_run=arguments.dry_run)
    except RebalanceError as error:
        print(f"ОШИБКА: {error}", file=sys.stderr)
        return 1
    finally:
        await rebalancer.router.dispose()
    for move in report.moves:
        print(f"{move.status}: {move.contest_id} шард {move.source} -> {move.target} {move.rows or ''}")
    print(f"перенесено/запланировано: {len(report.moves)}, осталось на месте: {report.unchanged}")
    if not arguments.dry_run:
        print(f"Задайте ACTIVE_SHARDS={report.new_active_shards} в .env ВСЕХ сервисов одинаково и перезапустите их")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="storage")
    subparsers = parser.add_subparsers(dest="command", required=True)
    migrate = subparsers.add_parser("migrate", help="применить миграции (по умолчанию на всех шардах)")
    migrate.add_argument("--shard", type=int, help="номер одного шарда")
    migrate.add_argument("--downgrade", metavar="REVISION", help="откатить до ревизии (например base)")
    seed_parser = subparsers.add_parser("seed", help="повторяемые демо-данные на шарды-владельцы")
    seed_parser.add_argument("--shard", type=int, help="принудительно загрузить всё на один шард")
    subparsers.add_parser("check", help="проверить подключение и ревизию схемы на шардах")
    subparsers.add_parser("distribution", help="число контестов на каждом шарде")
    rebalance = subparsers.add_parser("rebalance", help="перенос контестов между шардами")
    rebalance.add_argument("action", choices=["add-shard", "remove-shard"])
    rebalance.add_argument("shard", type=int)
    rebalance.add_argument("--dry-run", action="store_true", help="только показать план")
    arguments = parser.parse_args(argv)

    if arguments.command == "migrate":
        return _migrate(arguments)  # alembic сам запускает event loop — вне asyncio.run
    if arguments.command == "seed":
        asyncio.run(seed(arguments.shard))
        return 0
    if arguments.command == "check":
        return asyncio.run(_check())
    if arguments.command == "distribution":
        return asyncio.run(_distribution())
    return asyncio.run(_rebalance(arguments))


if __name__ == "__main__":
    sys.exit(main())
