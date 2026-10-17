"""Настройки и политика повторов (services/processing/src/config.py, retry/policy.py)."""

import random

import pytest
from config import ConfigError, ProcessingSettings
from retry.policy import attempts_exhausted, backoff_delay_seconds

BASE_ENV = {
    "RABBITMQ_HOST": "rabbit",
    "RABBITMQ_PORT": "5672",
    "RABBITMQ_USER": "guest",
    "RABBITMQ_PASSWORD": "secret-password",
    "JUDGE0_URL": "http://judge0:2358",
    "LEASE_SECONDS": "30",
    "MAX_RETRY_ATTEMPTS": "5",
    "RETRY_BASE_DELAY_SECONDS": "2",
}


def make_settings(**overrides):
    env = {**BASE_ENV, **overrides}
    return ProcessingSettings.from_env(env)


def test_required_values_are_parsed():
    settings = make_settings()
    assert settings.rabbitmq_host == "rabbit"
    assert settings.rabbitmq_port == 5672
    assert settings.lease_seconds == 30
    assert settings.max_retry_attempts == 5
    assert settings.retry_base_delay_seconds == 2.0


def test_optional_values_use_defaults():
    settings = make_settings()
    assert settings.retry_max_delay_seconds == 30.0
    assert settings.outbox_batch_size == 50
    assert settings.prefetch_count == 4


def test_optional_values_can_be_overridden():
    settings = make_settings(PREFETCH_COUNT="10", RETRY_MAX_DELAY_SECONDS="60")
    assert settings.prefetch_count == 10
    assert settings.retry_max_delay_seconds == 60.0


@pytest.mark.parametrize("name", sorted(BASE_ENV))
def test_missing_required_value_raises(name):
    env = {key: value for key, value in BASE_ENV.items() if key != name}
    with pytest.raises(ConfigError):
        ProcessingSettings.from_env(env)


def test_empty_required_value_raises():
    with pytest.raises(ConfigError):
        make_settings(JUDGE0_URL="")


def test_heartbeat_is_third_of_lease():
    assert make_settings(LEASE_SECONDS="30").heartbeat_seconds == 10.0


def test_password_is_not_in_repr():
    assert "secret-password" not in repr(make_settings())


def test_backoff_stays_within_half_cap_and_cap():
    settings = make_settings(RETRY_BASE_DELAY_SECONDS="2", RETRY_MAX_DELAY_SECONDS="30")
    rng = random.Random(1)
    for attempt, cap in [(1, 2), (2, 4), (3, 8), (4, 16), (5, 30), (10, 30)]:
        for _ in range(50):
            delay = backoff_delay_seconds(settings, attempt, rng)
            assert cap / 2 <= delay <= cap


def test_backoff_attempt_zero_equals_first_attempt_range():
    settings = make_settings(RETRY_BASE_DELAY_SECONDS="2")
    delay = backoff_delay_seconds(settings, 0, random.Random(1))
    assert 1.0 <= delay <= 2.0


def test_backoff_is_deterministic_for_same_seed():
    settings = make_settings()
    first = backoff_delay_seconds(settings, 3, random.Random(42))
    second = backoff_delay_seconds(settings, 3, random.Random(42))
    assert first == second


@pytest.mark.parametrize(
    "attempts,expected",
    [(0, False), (4, False), (5, True), (6, True)],
)
def test_attempts_exhausted_boundary(attempts, expected):
    assert (
        attempts_exhausted(make_settings(MAX_RETRY_ATTEMPTS="5"), attempts) is expected
    )
