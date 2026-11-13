"""Increment 4: production SaaS: users, workspaces, sessions, hired role modules, customer text.

Revision ID: 0004
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE app_user (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email          TEXT NOT NULL,
  name           TEXT NOT NULL,
  password_hash  TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX app_user_email ON app_user (lower(email));

CREATE TABLE membership (
  user_id     UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, tenant_id)
);

-- Only a SHA-256 of the session token is stored; the token itself lives in an httpOnly cookie.
CREATE TABLE user_session (
  token_hash  TEXT PRIMARY KEY,
  user_id     UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL,
  user_agent  TEXT
);
CREATE INDEX ON user_session (user_id);

-- Which roles a workspace has "hired"
CREATE TABLE workspace_module (
  tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  module_id   TEXT NOT NULL,
  hired       BOOLEAN NOT NULL DEFAULT TRUE,
  hired_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  config      JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (tenant_id, module_id)
);

ALTER TABLE tenant ADD COLUMN data_source TEXT NOT NULL DEFAULT 'demo';
ALTER TABLE account ADD COLUMN description TEXT;
ALTER TABLE lead ADD COLUMN description TEXT;

-- Free text from customers: exit surveys, NPS comments, support tickets
CREATE TABLE feedback (
  id          UUID PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  account_id  UUID REFERENCES account(id),
  created_at  TIMESTAMPTZ NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('exit_survey', 'nps', 'support_ticket')),
  score       INT,
  body        TEXT NOT NULL
);
CREATE INDEX ON feedback (tenant_id, kind, created_at DESC);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS feedback, workspace_module, user_session, membership, app_user CASCADE;
ALTER TABLE tenant DROP COLUMN data_source;
ALTER TABLE account DROP COLUMN description;
ALTER TABLE lead DROP COLUMN description;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
