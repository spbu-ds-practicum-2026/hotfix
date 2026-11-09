"""Приведение схемы к docs/tr.md: contests.migration_state, submissions.submitted_at, индексы Watchdog, строка standings.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE_STATEMENTS = [
    # --- contests.migration_state: ACTIVE по умолчанию, MIGRATING — идёт перенос контеста на другой шард.
    "ALTER TABLE contests ADD COLUMN migration_state VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'",
    """
    ALTER TABLE contests ADD CONSTRAINT ck_contests_migration_state
        CHECK (migration_state IN ('ACTIVE', 'MIGRATING'))
    """,
    # --- submissions.submitted_at: неизменяемое время принятия отправки (основа расчёта штрафа ICPC).
    "ALTER TABLE submissions ADD COLUMN submitted_at TIMESTAMPTZ",
    "UPDATE submissions SET submitted_at = created_at",
    "ALTER TABLE submissions ALTER COLUMN submitted_at SET NOT NULL",
    "ALTER TABLE submissions ALTER COLUMN submitted_at SET DEFAULT now()",
    # --- Индексы Watchdog: поиск просроченной аренды и наступивших повторов.
    """
    CREATE INDEX ix_submissions_processing_lease_until
        ON submissions (lease_until) WHERE status = 'PROCESSING'
    """,
    """
    CREATE INDEX ix_submissions_retrying_next_attempt_at
        ON submissions (next_attempt_at) WHERE status = 'RETRYING'
    """,
    # --- Строка standings создаётся вместе с контестом: на ней Standings берёт SELECT ... FOR UPDATE.
    """
    INSERT INTO standings (contest_id)
    SELECT contest_id FROM contests
    ON CONFLICT (contest_id) DO NOTHING
    """,
]

DOWNGRADE_STATEMENTS = [
    "DROP INDEX ix_submissions_retrying_next_attempt_at",
    "DROP INDEX ix_submissions_processing_lease_until",
    "ALTER TABLE submissions DROP COLUMN submitted_at",
    "ALTER TABLE contests DROP CONSTRAINT ck_contests_migration_state",
    "ALTER TABLE contests DROP COLUMN migration_state",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
