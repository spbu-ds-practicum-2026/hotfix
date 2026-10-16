"""Исключения слоя хранения. Вызывающий код (API, worker) ловит только их, а не ошибки SQLAlchemy."""


class StorageError(Exception):
    """Базовая ошибка слоя хранения."""


class StorageUnavailableError(StorageError):
    """База недоступна или оборвала соединение. API должен ответить 503 TEMPORARILY_UNAVAILABLE."""


class IdempotencyKeyReusedError(StorageError):
    """Тот же (team_id, idempotency_key), но другое тело запроса. API: 409 IDEMPOTENCY_KEY_REUSED."""


class DuplicateRecordError(StorageError):
    """Нарушена уникальность (например, повторный submission_id или event_id). Транзакция откачена."""


class ReferenceNotFoundError(StorageError):
    """Ссылка на несуществующую команду или задачу (либо они из другого соревнования).

    reference принимает значения "team" или "problem": API мапит их на TEAM_NOT_FOUND / PROBLEM_NOT_FOUND.
    """

    def __init__(self, reference: str, message: str) -> None:
        super().__init__(message)
        self.reference = reference
