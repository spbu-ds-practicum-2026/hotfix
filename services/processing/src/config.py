import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import load_dotenv

OPTIONAL_DEFAULTS: dict[str, str] = {
    "RETRY_MAX_DELAY_SECONDS": "30",
    "JUDGE0_TOTAL_TIMEOUT_SECONDS": "20",
    "JUDGE0_POLL_INTERVAL_SECONDS": "0.5",
    "PUBLISHER_CONFIRM_TIMEOUT_SECONDS": "5",
    "WATCHDOG_INTERVAL_SECONDS": "5",
    "OUTBOX_BATCH_SIZE": "50",
    "OUTBOX_IDLE_SECONDS": "0.2",
    "PREFETCH_COUNT": "4",
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ProcessingSettings:
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_password: str = field(repr=False)
    judge0_url: str
    lease_seconds: int
    max_retry_attempts: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float
    judge0_total_timeout_seconds: float
    judge0_poll_interval_seconds: float
    publisher_confirm_timeout_seconds: float
    watchdog_interval_seconds: float
    outbox_batch_size: int
    outbox_idle_seconds: float
    prefetch_count: int

    @property
    def heartbeat_seconds(self) -> float:
        return self.lease_seconds / 3

    @classmethod
    def from_env(cls, environment: Mapping[str, str]) -> "ProcessingSettings":
        def required(name: str) -> str:
            value = environment.get(name)
            if value is None or value == "":
                raise ConfigError(f"missing config env: {name}")
            return value

        def optional(name: str) -> str:
            return environment.get(name) or OPTIONAL_DEFAULTS[name]

        return cls(
            rabbitmq_host=required("RABBITMQ_HOST"),
            rabbitmq_port=int(required("RABBITMQ_PORT")),
            rabbitmq_user=required("RABBITMQ_USER"),
            rabbitmq_password=required("RABBITMQ_PASSWORD"),
            judge0_url=required("JUDGE0_URL"),
            lease_seconds=int(required("LEASE_SECONDS")),
            max_retry_attempts=int(required("MAX_RETRY_ATTEMPTS")),
            retry_base_delay_seconds=float(required("RETRY_BASE_DELAY_SECONDS")),
            retry_max_delay_seconds=float(optional("RETRY_MAX_DELAY_SECONDS")),
            judge0_total_timeout_seconds=float(optional("JUDGE0_TOTAL_TIMEOUT_SECONDS")),
            judge0_poll_interval_seconds=float(optional("JUDGE0_POLL_INTERVAL_SECONDS")),
            publisher_confirm_timeout_seconds=float(optional("PUBLISHER_CONFIRM_TIMEOUT_SECONDS")),
            watchdog_interval_seconds=float(optional("WATCHDOG_INTERVAL_SECONDS")),
            outbox_batch_size=int(optional("OUTBOX_BATCH_SIZE")),
            outbox_idle_seconds=float(optional("OUTBOX_IDLE_SECONDS")),
            prefetch_count=int(optional("PREFETCH_COUNT")),
        )


def load_settings() -> ProcessingSettings:
    load_dotenv()
    return ProcessingSettings.from_env(os.environ)