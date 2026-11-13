"""Increment 9: OAuth connectors (QuickBooks, Xero, Salesforce, Gusto, Google Ads), token connectors
(Zendesk, Intercom, Meta Ads), CRM ids for write-back.

Revision ID: 0009
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE oauth_state (
  state_hash  TEXT PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,
  user_id     UUID,
  verifier    TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Where a lead / account lives in the CRM, so scores can be written back to the right record
ALTER TABLE lead ADD COLUMN crm TEXT;
ALTER TABLE lead ADD COLUMN external_id TEXT;
ALTER TABLE account ADD COLUMN crm TEXT;
ALTER TABLE account ADD COLUMN crm_id TEXT;
-- Feedback can come from several tools at once; each sync replaces only its own rows
ALTER TABLE feedback ADD COLUMN source TEXT NOT NULL DEFAULT 'csv';

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS oauth_state;
ALTER TABLE lead DROP COLUMN IF EXISTS crm, DROP COLUMN IF EXISTS external_id;
ALTER TABLE account DROP COLUMN IF EXISTS crm, DROP COLUMN IF EXISTS crm_id;
ALTER TABLE feedback DROP COLUMN IF EXISTS source;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
