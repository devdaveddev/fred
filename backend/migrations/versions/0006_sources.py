"""Increment 6: data sources: connections, sync history, external ids, spend provenance, mapping metadata.

Revision ID: 0006
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE connection (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL,              -- stripe | hubspot
  secret_enc    TEXT NOT NULL,              -- Fernet-encrypted credential; never returned by the API
  secret_hint   TEXT NOT NULL,
  config        JSONB NOT NULL DEFAULT '{}',
  created_by    UUID REFERENCES app_user(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, kind)
);

CREATE TABLE sync_run (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  source        TEXT NOT NULL,              -- stripe | hubspot | csv:<kind>
  status        TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error')),
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  stats         JSONB NOT NULL DEFAULT '{}',
  warnings      JSONB NOT NULL DEFAULT '[]',
  error         TEXT,
  created_by    UUID REFERENCES app_user(id)
);
CREATE INDEX ON sync_run (tenant_id, started_at DESC);

ALTER TABLE account ADD COLUMN external_id TEXT;
CREATE UNIQUE INDEX account_external ON account (tenant_id, external_id) WHERE external_id IS NOT NULL;
ALTER TABLE spend ADD COLUMN source TEXT NOT NULL DEFAULT 'demo';   -- demo | gl | ads
ALTER TABLE gl_account ADD COLUMN section TEXT, ADD COLUMN mapped_by TEXT NOT NULL DEFAULT 'seed',
  ADD COLUMN confidence REAL;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS sync_run, connection CASCADE;
DROP INDEX IF EXISTS account_external;
ALTER TABLE account DROP COLUMN external_id;
ALTER TABLE spend DROP COLUMN source;
ALTER TABLE gl_account DROP COLUMN section, DROP COLUMN mapped_by, DROP COLUMN confidence;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
