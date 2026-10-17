import random

from config import ProcessingSettings


def backoff_delay_seconds(
    settings: ProcessingSettings,
    attempt_count: int,
    random_generator: random.Random,
) -> float:
    exponent = max(attempt_count - 1, 0)
    cap = min(
        settings.retry_max_delay_seconds,
        settings.retry_base_delay_seconds * (2**exponent),
    )
    return random_generator.uniform(cap / 2, cap)


def attempts_exhausted(settings: ProcessingSettings, attempt_count: int) -> bool:
    return attempt_count >= settings.max_retry_attempts
