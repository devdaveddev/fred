"""Increment 5: deliverables (investor update, board pack, decision memos) and the action loop.

Revision ID: 0005
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE = """
-- Finished work products. body = structured sections; facts = every number used, with its receipt.
CREATE TABLE document (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN ('investor_update', 'board_pack', 'decision_memo')),
  title         TEXT NOT NULL,
  period        TEXT,
  snapshot_id   TEXT NOT NULL,
  body          JSONB NOT NULL,
  facts         JSONB NOT NULL,
  verification  JSONB NOT NULL,
  created_by    UUID REFERENCES app_user(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON document (tenant_id, created_at DESC);

-- A finding turned into owned work, with the baseline needed to judge whether it worked.
CREATE TABLE task (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  finding_id        TEXT NOT NULL,
  module            TEXT NOT NULL,
  title             TEXT NOT NULL,
  steps             JSONB NOT NULL DEFAULT '[]',
  owner             TEXT NOT NULL,
  due_date          DATE NOT NULL,
  status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'done', 'cancelled')),
  baseline_impact   DOUBLE PRECISION,
  baseline_snapshot TEXT NOT NULL,
  created_by        UUID REFERENCES app_user(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at      TIMESTAMPTZ
);
CREATE INDEX ON task (tenant_id, status, due_date);

-- Business memory: "this is expected, don't flag it" (with why, and until when)
CREATE TABLE finding_note (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  finding_id      TEXT NOT NULL,
  note            TEXT NOT NULL,
  suppress_until  DATE NOT NULL,
  created_by      UUID REFERENCES app_user(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON finding_note (tenant_id, finding_id);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = "DROP TABLE IF EXISTS finding_note, task, document CASCADE;"


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
