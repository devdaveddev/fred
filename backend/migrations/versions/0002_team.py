"""Increment 2: data the analyst team needs: leads, weekly usage, firmographics, model runs.

Revision ID: 0002
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE account
  ADD COLUMN acquisition_channel TEXT,
  ADD COLUMN employees INT,
  ADD COLUMN industry TEXT,
  ADD COLUMN country TEXT;

CREATE TABLE lead (
  id                UUID PRIMARY KEY,
  tenant_id         UUID NOT NULL REFERENCES tenant(id),
  created_at        TIMESTAMPTZ NOT NULL,
  source_channel    TEXT NOT NULL,
  company           TEXT NOT NULL,
  contact_name      TEXT,
  industry          TEXT,
  employees         INT,
  country           TEXT,
  seniority         TEXT,
  pricing_views     INT NOT NULL DEFAULT 0,
  docs_views        INT NOT NULL DEFAULT 0,
  email_engagement  INT NOT NULL DEFAULT 0,
  demo_requested    BOOLEAN NOT NULL DEFAULT FALSE,
  webinar           BOOLEAN NOT NULL DEFAULT FALSE,
  status            TEXT NOT NULL CHECK (status IN ('open', 'converted', 'lost')),
  converted_at      TIMESTAMPTZ,
  account_id        UUID REFERENCES account(id)
);
CREATE INDEX ON lead (tenant_id, created_at DESC);
CREATE INDEX ON lead (tenant_id, status);

-- account x week product usage (rolled up from usage events)
CREATE TABLE usage_weekly (
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  account_id    UUID NOT NULL REFERENCES account(id),
  week          DATE NOT NULL,
  seats         INT NOT NULL,
  active_users  INT NOT NULL,
  events        INT NOT NULL,
  PRIMARY KEY (tenant_id, account_id, week)
);
CREATE INDEX ON usage_weekly (tenant_id, week);

-- Model registry: every trained model, its evaluation and the snapshot it saw.
CREATE TABLE model_run (
  id           TEXT PRIMARY KEY,
  tenant_id    UUID NOT NULL REFERENCES tenant(id),
  kind         TEXT NOT NULL,
  snapshot_id  TEXT NOT NULL,
  algorithm    TEXT NOT NULL,
  params       JSONB NOT NULL,
  metrics      JSONB NOT NULL,
  features     JSONB NOT NULL,
  n_train      INT NOT NULL,
  n_test       INT NOT NULL,
  trained_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON model_run (tenant_id, kind, trained_at DESC);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS model_run, usage_weekly, lead CASCADE;
ALTER TABLE account DROP COLUMN acquisition_channel, DROP COLUMN employees, DROP COLUMN industry, DROP COLUMN country;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
