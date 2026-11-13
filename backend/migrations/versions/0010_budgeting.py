"""Increment 10: shared budgeting: budget rounds, department submissions, review trail.

Revision ID: 0010
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE budget_round (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  fiscal_year   INT NOT NULL,
  name          TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'published', 'cancelled')),
  due_date      DATE,
  revenue       JSONB NOT NULL,          -- drivers: new customers/month, ARPA of new, churn, expansion
  assignments   JSONB NOT NULL,          -- account code -> department (non-payroll lines)
  baseline      JSONB NOT NULL,          -- last 12 closed months by group, for comparison
  created_by    UUID REFERENCES app_user(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at  TIMESTAMPTZ,
  plan_id       TEXT
);
CREATE INDEX ON budget_round (tenant_id, created_at DESC);

CREATE TABLE budget_submission (
  round_id       UUID NOT NULL REFERENCES budget_round(id) ON DELETE CASCADE,
  tenant_id      UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  department     TEXT NOT NULL,
  owner_user_id  UUID REFERENCES app_user(id) ON DELETE SET NULL,
  status         TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'approved', 'returned')),
  lines          JSONB NOT NULL DEFAULT '[]',   -- [{account, monthly: [12]}]
  hires          JSONB NOT NULL DEFAULT '[]',   -- [{role, location, planned_start, salary_annual}]
  note           TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (round_id, department)
);

CREATE TABLE budget_event (
  id          BIGSERIAL PRIMARY KEY,
  round_id    UUID NOT NULL REFERENCES budget_round(id) ON DELETE CASCADE,
  tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  department  TEXT,
  actor       TEXT,
  action      TEXT NOT NULL,
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON budget_event (round_id, created_at);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = "DROP TABLE IF EXISTS budget_event, budget_submission, budget_round CASCADE;"


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
