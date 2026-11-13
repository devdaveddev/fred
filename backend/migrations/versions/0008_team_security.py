"""Increment 8: team invites, audit log, persistent login throttling, session metadata, SSO (OIDC).

Revision ID: 0008
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE invite (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  email        TEXT NOT NULL,
  role         TEXT NOT NULL CHECK (role IN ('admin', 'member')),
  token_hash   TEXT NOT NULL UNIQUE,
  invited_by   UUID REFERENCES app_user(id) ON DELETE SET NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at   TIMESTAMPTZ NOT NULL,
  accepted_at  TIMESTAMPTZ,
  revoked_at   TIMESTAMPTZ
);
CREATE INDEX ON invite (tenant_id, created_at DESC);

-- Task owners can be real people (owner text stays for display and for people outside Fred)
ALTER TABLE task ADD COLUMN owner_user_id UUID REFERENCES app_user(id) ON DELETE SET NULL;

-- Append-only record of security-relevant actions (SOC 2 CC6/CC7 evidence)
CREATE TABLE audit_event (
  id           BIGSERIAL PRIMARY KEY,
  tenant_id    UUID REFERENCES tenant(id) ON DELETE CASCADE,
  user_id      UUID,
  actor        TEXT,
  action       TEXT NOT NULL,
  target       TEXT,
  detail       JSONB NOT NULL DEFAULT '{}',
  ip           TEXT,
  user_agent   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_event (tenant_id, created_at DESC);
CREATE INDEX ON audit_event (action, created_at DESC);

-- Failed logins survive restarts and are shared by every API replica
CREATE TABLE login_attempt (
  key         TEXT NOT NULL,
  at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON login_attempt (key, at);

ALTER TABLE user_session ADD COLUMN id UUID NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE user_session ADD COLUMN ip TEXT;
ALTER TABLE user_session ADD COLUMN last_seen_at TIMESTAMPTZ;
ALTER TABLE user_session ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'password';
CREATE UNIQUE INDEX ON user_session (id);

-- Single sign-on via any OpenID Connect provider (Google Workspace, Okta, Microsoft Entra ID, ...)
CREATE TABLE sso_config (
  tenant_id      UUID PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
  issuer         TEXT NOT NULL,
  client_id      TEXT NOT NULL,
  secret_enc     TEXT NOT NULL,
  domains        TEXT[] NOT NULL,
  default_role   TEXT NOT NULL DEFAULT 'member' CHECK (default_role IN ('admin', 'member')),
  enforce        BOOLEAN NOT NULL DEFAULT false,
  enabled        BOOLEAN NOT NULL DEFAULT true,
  verified_at    TIMESTAMPTZ,              -- first successful SSO sign-in; enforcing needs it
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- one workspace per email domain
CREATE TABLE sso_domain (
  domain     TEXT PRIMARY KEY,
  tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE
);
CREATE TABLE sso_state (
  state_hash  TEXT PRIMARY KEY,
  tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  nonce       TEXT NOT NULL,
  verifier    TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS sso_state, sso_domain, sso_config, login_attempt, audit_event, invite CASCADE;
ALTER TABLE task DROP COLUMN IF EXISTS owner_user_id;
ALTER TABLE user_session DROP COLUMN IF EXISTS id, DROP COLUMN IF EXISTS ip, DROP COLUMN IF EXISTS last_seen_at,
  DROP COLUMN IF EXISTS auth_method;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
