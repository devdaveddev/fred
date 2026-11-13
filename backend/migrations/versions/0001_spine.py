"""Increment 1 spine: tenants, canonical billing + spend, snapshots, receipts.

Revision ID: 0001
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = """
__TIMESCALE__

CREATE TABLE tenant (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug          TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  currency      TEXT NOT NULL DEFAULT 'USD',
  timezone      TEXT NOT NULL DEFAULT 'UTC',
  gross_margin  NUMERIC(5,4) NOT NULL,
  annual_wacc   NUMERIC(5,4) NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE account (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  name        TEXT NOT NULL,
  domain      TEXT,
  segment     TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON account (tenant_id);

-- account x month, already normalized (annual / 12, one-time fees excluded)
CREATE TABLE subscription_mrr (
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  account_id  UUID NOT NULL REFERENCES account(id),
  month       DATE NOT NULL,
  mrr         NUMERIC(14,2) NOT NULL CHECK (mrr >= 0),
  PRIMARY KEY (tenant_id, account_id, month)
);
CREATE INDEX ON subscription_mrr (tenant_id, month);

-- channel x day, in tenant base currency
CREATE TABLE spend (
  ts          TIMESTAMPTZ NOT NULL,
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  category    TEXT NOT NULL CHECK (category IN ('sm', 'other_opex')),
  channel     TEXT NOT NULL,
  amount      NUMERIC(14,2) NOT NULL
);
__HYPERTABLE__
CREATE INDEX ON spend (tenant_id, ts DESC);

CREATE TABLE cash_balance (
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  month       DATE NOT NULL,
  cash        NUMERIC(16,2) NOT NULL,
  PRIMARY KEY (tenant_id, month)
);

-- A snapshot pins the exact input rows a set of receipts was computed from.
CREATE TABLE snapshot (
  id            TEXT PRIMARY KEY,
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  as_of_month   DATE NOT NULL,
  content_hash  TEXT NOT NULL,
  row_counts    JSONB NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The provenance rule: every number that leaves the compute layer has one of these.
CREATE TABLE receipt (
  id               TEXT PRIMARY KEY,
  tenant_id        UUID NOT NULL REFERENCES tenant(id),
  snapshot_id      TEXT NOT NULL REFERENCES snapshot(id),
  metric           TEXT NOT NULL,
  value            DOUBLE PRECISION,
  formula_version  TEXT NOT NULL,
  formula          TEXT NOT NULL,
  inputs           JSONB NOT NULL,
  seed             INT,
  model_version    TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON receipt (tenant_id, snapshot_id);

-- Read-only role for the orchestrator (Increment 2). Row-level security lands with auth.
-- Managed Postgres (Render, RDS...) may not allow CREATE ROLE: then there is no read-only role, and that's fine.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    CREATE ROLE fred_readonly NOLOGIN;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'no permission to create role fred_readonly; skipping';
END $$;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS receipt, snapshot, cash_balance, spend, subscription_mrr, account, tenant CASCADE;
"""


def upgrade() -> None:
    # TimescaleDB is used when the server has it (the bundled image does); managed Postgres without it gets a
    # plain table. Fred's queries work on both. FRED_TIMESCALE=off forces the plain path.
    import os

    bind = op.get_bind()
    has_ts = os.environ.get("FRED_TIMESCALE", "auto") != "off" and bind.exec_driver_sql(
        "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'").first() is not None
    sql = UPGRADE.replace("__TIMESCALE__", "CREATE EXTENSION IF NOT EXISTS timescaledb;" if has_ts else "")
    sql = sql.replace("__HYPERTABLE__", "SELECT create_hypertable('spend', 'ts', chunk_time_interval => INTERVAL '30 days');"
                      if has_ts else "")
    op.execute(sql)


def downgrade() -> None:
    op.execute(DOWNGRADE)
