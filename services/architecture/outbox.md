# VERDICT — Transactional Outbox

## 1. Проблема

Если приложение сохранило Submission и упало до отправки сообщения в RabbitMQ, в базе есть отправка, о которой никто не узнает. Если отправить сообщение до коммита и упасть, сообщение получит несуществующую отправку. Outbox убирает обе ситуации: событие записывается в БД в той же транзакции, что и данные, а в RabbitMQ его отправляет отдельный publisher.

## 2. Правило атомарности

Создание отправки (`SubmissionRepository.create_submission_with_outbox`) выполняет в **одной транзакции**:

1. `INSERT INTO idempotency_keys ... ON CONFLICT (team_id, idempotency_key) DO NOTHING` — занимаем ключ. При конфликте транзакция ничего больше не пишет: сравнивается `payload_hash`, при совпадении возвращается существующая отправка, иначе `IdempotencyKeyReusedError` (409).
2. `INSERT INTO submissions` со статусом `QUEUED`, `sequence = 1`.
3. `INSERT INTO outbox_events` с событием `submission.queued.v1`.
4. `COMMIT`.

Ключ занимается первым, потому что именно его уникальность разрешает параллельные одинаковые запросы. Внешний ключ `idempotency_keys.submission_id → submissions` отложенный и проверяется на `COMMIT`, когда submission уже вставлена.

Любая ошибка внутри блока откатывает всё: нет ни отправки, ни ключа, ни события.

Тот же принцип для результата проверки: worker в одной транзакции проверяет `fencing_token`, записывает результат и добавляет событие `submission.verdict.v1`. Методы `claim` и `write_result` в этот этап не входят.

**Запрещено** публиковать сообщение в RabbitMQ напрямую из обработчика API или worker, минуя Outbox.

## 3. Поля `outbox_events`

| Поле | Назначение |
|---|---|
| `event_id` | идентификатор события, ключ дедупликации (`eventId` в сообщении) |
| `submission_id` | агрегат, к которому относится событие |
| `trace_id` | сквозной идентификатор запроса |
| `event_type` | `submission.queued` / `submission.verdict` |
| `routing_key` | `submission.queued.v1` / `submission.verdict.v1` |
| `schema_version` | версия схемы сообщения |
| `payload` | тело сообщения, ключи в camelCase |
| `created_at` | момент возникновения события (`occurredAt`) |
| `published_at` | `NULL` — ещё не опубликовано; иначе время подтверждённой публикации |
| `attempt_count`, `last_error` | сколько раз публикация не удалась и почему |

## 4. Статусы публикации

Отдельной колонки статуса нет, достаточно `published_at`:

| Состояние | Признак |
|---|---|
| ожидает публикации | `published_at IS NULL`, `attempt_count = 0` |
| публикация не удалась, будет повтор | `published_at IS NULL`, `attempt_count > 0`, заполнен `last_error` |
| опубликовано | `published_at IS NOT NULL` |

## 5. Алгоритм publisher

```text
цикл:
    events = repository.get_pending_outbox_events(limit)
    для каждого event:
        публикация в exchange verdict.events с routing_key = event.routing_key (publisher confirm)
        если подтверждено:  repository.mark_outbox_event_published(event.event_id)
        если ошибка:        repository.record_outbox_publish_failure(event.event_id, текст)
    пауза, если событий не было
```

После шардирования publisher обходит оба шарда (у каждого своя таблица `outbox_events`).

## 6. Почему возможны дубликаты

Доставка **at-least-once**. Дубликат появляется, если publisher опубликовал сообщение и упал до `mark_outbox_event_published`, либо не получил подтверждение от брокера, хотя тот сообщение принял (неопределённый исход). После перезапуска событие всё ещё `published_at IS NULL` и будет отправлено снова. Терять событие нельзя, поэтому система выбирает повтор.

## 7. Как дубликаты обрабатывают получатели

- Потребитель хранит `eventId` обработанных событий и пропускает повторы.
- Для порядка используется `sequence` отправки: событие с `sequence <=` уже известного игнорируется (как в SSE-контракте).
- Обработка событий должна быть идемпотентной: повторный `submission.verdict.v1` не должен менять standings второй раз.

## 8. Известные ограничения

- Порядок публикации по `created_at` не гарантирует порядок коммитов; опираться на порядок доставки нельзя, нужно опираться на `sequence`.
- `get_pending_outbox_events` не блокирует строки. Два publisher'а одновременно отправят одно и то же событие дважды. Это безопасно из-за дедупликации, но лишняя нагрузка; при необходимости можно перейти на `FOR UPDATE SKIP LOCKED` внутри одной транзакции с публикацией.
- Очистка опубликованных событий не реализована.
