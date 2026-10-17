"""Начальная схема VERDICT: contests, teams, problems, submissions, idempotency_keys, outbox_events, standings.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Каждое выражение выполняется отдельным op.execute: asyncpg не принимает несколько команд в одном запросе.
UPGRADE_STATEMENTS = [
    # ---------------------------------------------------------------- contests
    """
    CREATE TABLE contests (
        contest_id UUID PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        starts_at TIMESTAMPTZ NOT NULL,
        ends_at TIMESTAMPTZ NOT NULL,
        is_frozen BOOLEAN NOT NULL DEFAULT false,
        CONSTRAINT ck_contests_time_range CHECK (ends_at > starts_at)
    )
    """,
    # ------------------------------------------------------------------- teams
    # UNIQUE (contest_id, team_id) нужен для составного внешнего ключа из submissions:
    # он гарантирует, что команда действительно принадлежит соревнованию отправки.
    """
    CREATE TABLE teams (
        team_id UUID PRIMARY KEY,
        contest_id UUID NOT NULL
            CONSTRAINT fk_teams_contest REFERENCES contests (contest_id),
        name VARCHAR(255) NOT NULL,
        CONSTRAINT uq_teams_contest_id_team_id UNIQUE (contest_id, team_id)
    )
    """,
    # ---------------------------------------------------------------- problems
    """
    CREATE TABLE problems (
        problem_id UUID PRIMARY KEY,
        contest_id UUID NOT NULL
            CONSTRAINT fk_problems_contest REFERENCES contests (contest_id),
        code VARCHAR(16) NOT NULL,
        title VARCHAR(255) NOT NULL,
        time_limit_ms INTEGER NOT NULL,
        memory_limit_kb INTEGER NOT NULL,
        test_config_reference VARCHAR(512) NOT NULL,
        CONSTRAINT uq_problems_contest_id_code UNIQUE (contest_id, code),
        CONSTRAINT uq_problems_contest_id_problem_id UNIQUE (contest_id, problem_id),
        CONSTRAINT ck_problems_time_limit_ms CHECK (time_limit_ms > 0),
        CONSTRAINT ck_problems_memory_limit_kb CHECK (memory_limit_kb > 0)
    )
    """,
    # ------------------------------------------------------------- submissions
    """
    CREATE TABLE submissions (
        submission_id UUID PRIMARY KEY,
        contest_id UUID NOT NULL,
        team_id UUID NOT NULL,
        problem_id UUID NOT NULL,
        trace_id UUID NOT NULL,
        source_code TEXT NOT NULL,
        language VARCHAR(16) NOT NULL,
        status VARCHAR(32) NOT NULL,
        verdict VARCHAR(32),
        sequence BIGINT NOT NULL DEFAULT 1,
        fencing_token BIGINT NOT NULL DEFAULT 0,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        lease_until TIMESTAMPTZ,
        worker_id UUID,
        next_attempt_at TIMESTAMPTZ,
        last_error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT fk_submissions_team
            FOREIGN KEY (contest_id, team_id) REFERENCES teams (contest_id, team_id),
        CONSTRAINT fk_submissions_problem
            FOREIGN KEY (contest_id, problem_id) REFERENCES problems (contest_id, problem_id),
        CONSTRAINT ck_submissions_source_code_size
            CHECK (octet_length(source_code) BETWEEN 1 AND 65536),
        CONSTRAINT ck_submissions_language CHECK (language IN ('CPP')),
        CONSTRAINT ck_submissions_status
            CHECK (status IN ('QUEUED', 'PROCESSING', 'RETRYING', 'COMPLETED', 'SYSTEM_ERROR')),
        CONSTRAINT ck_submissions_verdict
            CHECK (verdict IS NULL OR verdict IN
                ('ACCEPTED', 'WRONG_ANSWER', 'COMPILATION_ERROR', 'TIME_LIMIT_EXCEEDED', 'RUNTIME_ERROR')),
        CONSTRAINT ck_submissions_verdict_only_when_completed
            CHECK ((status = 'COMPLETED') = (verdict IS NOT NULL)),
        CONSTRAINT ck_submissions_sequence CHECK (sequence >= 1),
        CONSTRAINT ck_submissions_fencing_token CHECK (fencing_token >= 0),
        CONSTRAINT ck_submissions_attempt_count CHECK (attempt_count >= 0)
    )
    """,
    "CREATE INDEX ix_submissions_contest_id_status ON submissions (contest_id, status)",
    # Для пересчёта standings: все попытки команды по задаче в порядке отправки.
    """
    CREATE INDEX ix_submissions_contest_id_team_id_problem_id_created_at
        ON submissions (contest_id, team_id, problem_id, created_at)
    """,
    """
    CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_submissions_set_updated_at
        BEFORE UPDATE ON submissions
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
    """,
    # -------------------------------------------------------- idempotency_keys
    # FK на submissions отложенный: репозиторий сначала занимает ключ (это даёт защиту от гонок
    # через уникальность PRIMARY KEY), затем вставляет submission в той же транзакции.
    """
    CREATE TABLE idempotency_keys (
        team_id UUID NOT NULL
            CONSTRAINT fk_idempotency_keys_team REFERENCES teams (team_id),
        idempotency_key UUID NOT NULL,
        payload_hash VARCHAR(64) NOT NULL,
        submission_id UUID NOT NULL
            CONSTRAINT fk_idempotency_keys_submission REFERENCES submissions (submission_id)
            DEFERRABLE INITIALLY DEFERRED,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (team_id, idempotency_key),
        CONSTRAINT uq_idempotency_keys_submission_id UNIQUE (submission_id),
        CONSTRAINT ck_idempotency_keys_payload_hash CHECK (payload_hash ~ '^[0-9a-f]{64}$')
    )
    """,
    # ---------------------------------------------------------- outbox_events
    """
    CREATE TABLE outbox_events (
        event_id UUID PRIMARY KEY,
        submission_id UUID NOT NULL
            CONSTRAINT fk_outbox_events_submission REFERENCES submissions (submission_id),
        trace_id UUID NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        routing_key VARCHAR(128) NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        published_at TIMESTAMPTZ,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        CONSTRAINT ck_outbox_events_attempt_count CHECK (attempt_count >= 0)
    )
    """,
    # Частичный индекс: publisher читает только неопубликованные события, поэтому индекс остаётся маленьким.
    """
    CREATE INDEX ix_outbox_events_unpublished_created_at
        ON outbox_events (created_at, event_id) WHERE published_at IS NULL
    """,
    "CREATE INDEX ix_outbox_events_submission_id ON outbox_events (submission_id)",
    # ---------------------------------------------------------------- standings
    # Таблица запрошена участником №3 (domain-model-v3, п. 1.4). Содержимое пишет и читает только домен.
    """
    CREATE TABLE standings (
        contest_id UUID PRIMARY KEY
            CONSTRAINT fk_standings_contest REFERENCES contests (contest_id),
        is_frozen BOOLEAN NOT NULL DEFAULT false,
        rows JSONB NOT NULL DEFAULT '[]'::jsonb,
        last_event_sequence BIGINT NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]

DOWNGRADE_STATEMENTS = [
    "DROP TABLE standings",
    "DROP TABLE outbox_events",
    "DROP TABLE idempotency_keys",
    "DROP TRIGGER trg_submissions_set_updated_at ON submissions",
    "DROP FUNCTION set_updated_at()",
    "DROP TABLE submissions",
    "DROP TABLE problems",
    "DROP TABLE teams",
    "DROP TABLE contests",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
