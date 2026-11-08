# VERDICT — схема БД и хранилище (владелец: №1)

Версия 1.1 · источник правды — `docs/tr.md` (при расхождении приоритет у ТР).
Миграции: `services/storage/migrations/versions/0001_initial_schema.py`, `0002_migration_state_and_submitted_at.py`.
Каждый шард содержит одинаковую схему; контест лежит на одном шарде целиком.

## Таблицы

| Таблица | Назначение | Ключевые ограничения |
|---|---|---|
| `contests` | соревнования | `ends_at > starts_at`; `is_frozen`; `migration_state` ∈ `ACTIVE` / `MIGRATING` (0002) |
| `teams` | команды | `UNIQUE(contest_id, team_id)` — основа составного FK |
| `problems` | задачи | `UNIQUE(contest_id, code)`, `UNIQUE(contest_id, problem_id)`, лимиты > 0, `test_config_reference` |
| `submissions` | посылки | составные FK `(contest_id, team_id)`, `(contest_id, problem_id)`; `language='CPP'`; `octet_length(source_code)` 1..65536; `verdict NOT NULL ⇔ status='COMPLETED'`; `sequence>=1`; `fencing_token>=0`; `submitted_at` (0002) |
| `idempotency_keys` | идемпотентность | `PK(team_id, idempotency_key)`; `payload_hash` = SHA-256 тела (64 hex); `UNIQUE(submission_id)`; FK на `submissions` отложенный |
| `outbox_events` | Outbox | `submission_id`, `routing_key`, `payload jsonb`, `published_at`, `attempt_count`, `last_error`; частичный индекс по `published_at IS NULL` |
| `standings` | рейтинг (владелец данных — №3) | `PK(contest_id)`, `is_frozen`, `rows jsonb`, `last_event_sequence`; строка создаётся вместе с контестом, на ней Standings берёт `SELECT … FOR UPDATE` |

Именование: таблицы и колонки — snake_case, JSON — camelCase. Время — `timestamptz` (UTC), в событиях — ISO 8601 с `Z`. ID — `uuid`.
Язык в БД хранится как `CPP`; API принимает `cpp`, worker получает `cpp`.

## Жизненный цикл посылки

```
QUEUED ──claim──▶ PROCESSING ──write_result──▶ COMPLETED (+ verdict, + событие verdict)
   ▲                  │  ├──mark_system_error──▶ SYSTEM_ERROR (verdict = null, события нет)
   │                  │  └──mark_retrying / lease истёк (Watchdog)──▶ RETRYING
   └── Watchdog, когда next_attempt_at ≤ now() ◀──────────────────────┘
```

* `sequence`: 1 при создании, +1 на каждый переход (claim, retry, requeue, итог). Дубликат события сохраняет `eventId` и `sequence`.
* `fencing_token`: 0 при создании, +1 на каждый claim. `attempt_count` растёт на каждый claim (1 для первой попытки).
* Claim возможен **только из QUEUED** и только при `migration_state = 'ACTIVE'`.
* Любой переход worker'а — один условный `UPDATE` с условием `fencing_token = :token AND status = 'PROCESSING' AND lease_until > now()`; иначе `stale_token` и строка не меняется.
* `write_result` пишет событие `submission.verdict.v1` в Outbox в той же транзакции. При `SYSTEM_ERROR` события вердикта нет.
* Watchdog: `PROCESSING` с истёкшей арендой → `RETRYING` (с `next_attempt_at` по экспоненциальной задержке с jitter) или `SYSTEM_ERROR`, если `attempt_count ≥ MAX_RETRY_ATTEMPTS`; `RETRYING` с наступившим `next_attempt_at` → `QUEUED` + `submission.queued.v1` в одной транзакции. Контесты в `MIGRATING` пропускаются.

## События (docs/tr.md)

* `submission.queued.v1`: `eventId`, `schemaVersion`, `traceId`, `occurredAt`, `submissionId`, `sequence`.
* `submission.verdict.v1`: те же поля + `contestId`, `teamId`, `problemId`, `submittedAt`, `fencingToken`, `status` (всегда `COMPLETED`), `verdict`.
* `attemptId` в событиях нет. Для логов worker'а он вычисляется как `uuid5(submissionId, fencingToken)` (`storage.repositories.attempt_id_for`).

## Шардирование

* `ACTIVE_SHARDS=postgres_shard_0,postgres_shard_1`, `SHARD_VIRTUAL_NODES=128` — одинаково во всех сервисах.
* Владелец контеста: consistent hashing (SHA-256, виртуальные узлы) по `contestId`. Размещение совпадает с прежним `shard_index_for_contest`.
* Роутер (`ShardRouter`): владелец по текущему кольцу; если контеста там нет — поиск на остальных шардах. Посылка по `submissionId` ищется перебором шардов; при двух копиях берётся копия на шарде-владельце.
* `MIGRATING`: отправки → `ContestMigratingError` (HTTP 503 `TEMPORARILY_UNAVAILABLE`); claim → `ContestMigratingError` (NACK с задержкой); запись результата / продление аренды / retry → `stale_token` / `false`; Watchdog пропускает контест; чтение доступно.
* Rebalancer (`python -m storage.cli rebalance add-shard N | remove-shard N [--dry-run]`): MIGRATING на старом шарде → копирование всех строк контеста одной транзакцией → ACTIVE на новом → удаление на старом. Уже скопированный контест повторно не копируется (только удаление), поэтому повторный запуск безопасен и не теряет и не дублирует данные. В конце печатается новое `ACTIVE_SHARDS`.

## Интерфейс хранилища (для №2 и №3)

`ShardRouter.from_environment()`, `repository_for_contest(contest_id)`, `contest_repository_for(contest_id)`, `all_repositories()`, `find_repository_for_submission(submission_id)`, `shard_for_contest(contest_id)`, `dispose()`.

`SubmissionRepository`: `create_submission_with_outbox`, `get_submission_by_id`, `get_pending_outbox_events`, `mark_outbox_event_published`, `record_outbox_publish_failure`, `claim_submission`, `extend_lease`, `write_result`, `mark_retrying`, `mark_system_error`, `recover_expired`, `enqueue_due_retries`, `load_for_judging`.

`ContestRepository`: `get_contest_schedule`, `contest_exists`, `list_contests`, `list_teams`, `list_problems`.

Ошибки: `StorageUnavailableError` (503) ← `ContestMigratingError`, `ContestUnavailableError`; `IdempotencyKeyReusedError` (409); `ReferenceNotFoundError("team"|"problem")` (404); `DuplicateRecordError`.

## Команды

```bash
docker compose --env-file .env -f services/storage/compose.yaml up -d
./services/scripts/seed-dev.sh              # миграции на всех шардах + seed на шарды-владельцы
./services/scripts/check-atomicity.sh       # проверки на живой базе
./services/scripts/rebalance.sh add-shard 2 --dry-run
```
