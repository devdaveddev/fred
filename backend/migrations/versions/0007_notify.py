"""Increment 7: scheduled syncs and notifications (Slack, email).

Revision ID: 0007
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UPGRADE = """
-- Per-workspace delivery settings. The Slack webhook is a credential: stored encrypted like API keys.
CREATE TABLE notify_setting (
  tenant_id       UUID PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
  slack_enc       TEXT,
  slack_hint      TEXT,
  email_enabled   BOOLEAN NOT NULL DEFAULT true,
  daily_sync      BOOLEAN NOT NULL DEFAULT true,
  digest_hour     SMALLINT NOT NULL DEFAULT 8 CHECK (digest_hour BETWEEN 0 AND 23),
  timezone        TEXT NOT NULL DEFAULT 'UTC',
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Each person chooses whether they get the email digest.
ALTER TABLE membership ADD COLUMN email_digest BOOLEAN NOT NULL DEFAULT true;

-- Exactly-once scheduling across any number of workers: a job runs only if its row could be inserted.
CREATE TABLE scheduled_job (
  tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  job         TEXT NOT NULL,
  period      TEXT NOT NULL,
  claimed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  outcome     JSONB,
  PRIMARY KEY (tenant_id, job, period)
);

-- What the team has already been told, so a digest only carries news.
CREATE TABLE alerted_finding (
  tenant_id         UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  finding_id        TEXT NOT NULL,
  first_alerted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, finding_id)
);
ALTER TABLE task ADD COLUMN notified_state TEXT;

-- Delivery log: every message attempted, so people can see what was sent and why something wasn't.
CREATE TABLE notification (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,
  channel     TEXT NOT NULL CHECK (channel IN ('slack', 'email')),
  recipient   TEXT NOT NULL,
  subject     TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON notification (tenant_id, created_at DESC);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS notification, alerted_finding, scheduled_job, notify_setting CASCADE;
ALTER TABLE task DROP COLUMN IF EXISTS notified_state;
ALTER TABLE membership DROP COLUMN IF EXISTS email_digest;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
