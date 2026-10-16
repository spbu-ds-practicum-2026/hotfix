"""Допустимые значения статусов, вердиктов и языков.

Дубликат services/processing/src/contracts/statuses.py (источник правды для статусов и вердиктов):
значения обязаны совпадать с ним и с CHECK-ограничениями миграции 0001. Storage не импортирует
пакет contracts из services/processing, чтобы не зависеть от его расположения.
"""

from enum import Enum


class SubmissionStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class Verdict(str, Enum):
    ACCEPTED = "ACCEPTED"
    WRONG_ANSWER = "WRONG_ANSWER"
    COMPILATION_ERROR = "COMPILATION_ERROR"
    TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"
    RUNTIME_ERROR = "RUNTIME_ERROR"


class Language(str, Enum):
    CPP = "CPP"
