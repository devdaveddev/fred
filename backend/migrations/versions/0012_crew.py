"""Increment 12: the crew: role agents that create follow-ups, send reminders and weekly notes.

Revision ID: 0012
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE IF NOT EXISTS agent_setting (
  tenant_id   UUID PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
  mode        TEXT NOT NULL DEFAULT 'draft' CHECK (mode IN ('off', 'draft', 'auto')),
  owners      JSONB NOT NULL DEFAULT '{}',      -- role id -> user id who receives that agent's work
  weekly_day  SMALLINT NOT NULL DEFAULT 0 CHECK (weekly_day BETWEEN 0 AND 6),  -- 0 = Monday
  last_run_at TIMESTAMPTZ,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Everything an agent did or wants to do. dedupe_key makes each reminder happen once.
CREATE TABLE IF NOT EXISTS agent_activity (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  role               TEXT NOT NULL,
  kind               TEXT NOT NULL CHECK (kind IN ('new_task', 'due_soon', 'overdue', 'worse', 'weekly')),
  status             TEXT NOT NULL CHECK (status IN ('draft', 'sent', 'failed', 'dismissed', 'done')),
  recipient_user_id  UUID REFERENCES app_user(id) ON DELETE SET NULL,
  recipient_email    TEXT,
  subject            TEXT NOT NULL,
  body               TEXT NOT NULL,
  writer             TEXT NOT NULL,             -- template | crewai:<model>
  finding_id         TEXT,
  task_id            UUID,
  payload            JSONB NOT NULL DEFAULT '{}',
  dedupe_key         TEXT NOT NULL,
  error              TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at         TIMESTAMPTZ,
  decided_by         UUID REFERENCES app_user(id) ON DELETE SET NULL,
  UNIQUE (tenant_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS agent_activity_recent ON agent_activity (tenant_id, created_at DESC);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_activity, agent_setting CASCADE;")
