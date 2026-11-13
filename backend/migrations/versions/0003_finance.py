"""Increment 3a: the BizOps/finance spine: chart of accounts, GL actuals, payroll, annual plan.

Revision ID: 0003
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE tenant ADD COLUMN fiscal_year_start_month INT NOT NULL DEFAULT 1;

-- Chart of accounts, mapped to Fred's P&L groups (from QuickBooks/Xero on connect; editable)
CREATE TABLE gl_account (
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  code        TEXT NOT NULL,
  name        TEXT NOT NULL,
  pl_group    TEXT NOT NULL CHECK (pl_group IN ('revenue', 'cogs', 'sm', 'rnd', 'ga')),
  kind        TEXT NOT NULL CHECK (kind IN ('revenue', 'cogs', 'payroll', 'nonpayroll')),
  department  TEXT,
  PRIMARY KEY (tenant_id, code)
);

-- Monthly actuals per account (closed books)
CREATE TABLE gl_actual (
  tenant_id  UUID NOT NULL REFERENCES tenant(id),
  month      DATE NOT NULL,
  account    TEXT NOT NULL,
  amount     NUMERIC(16,2) NOT NULL,
  PRIMARY KEY (tenant_id, month, account),
  FOREIGN KEY (tenant_id, account) REFERENCES gl_account(tenant_id, code)
);

CREATE TABLE employee (
  id             UUID PRIMARY KEY,
  tenant_id      UUID NOT NULL REFERENCES tenant(id),
  name           TEXT NOT NULL,
  title          TEXT NOT NULL,
  department     TEXT NOT NULL,
  location       TEXT NOT NULL,
  start_date     DATE NOT NULL,
  end_date       DATE,
  salary_annual  NUMERIC(12,2) NOT NULL
);

-- Fully loaded monthly cost per person (salary + employer taxes + benefits)
CREATE TABLE payroll (
  tenant_id    UUID NOT NULL REFERENCES tenant(id),
  month        DATE NOT NULL,
  employee_id  UUID NOT NULL REFERENCES employee(id),
  amount       NUMERIC(12,2) NOT NULL,
  PRIMARY KEY (tenant_id, month, employee_id)
);

CREATE TABLE plan (
  id           TEXT PRIMARY KEY,
  tenant_id    UUID NOT NULL REFERENCES tenant(id),
  name         TEXT NOT NULL,
  fiscal_year  INT NOT NULL,
  approved_on  DATE NOT NULL,
  assumptions  JSONB NOT NULL,
  status       TEXT NOT NULL DEFAULT 'approved'
);

CREATE TABLE plan_line (
  tenant_id  UUID NOT NULL REFERENCES tenant(id),
  plan_id    TEXT NOT NULL REFERENCES plan(id),
  month      DATE NOT NULL,
  account    TEXT NOT NULL,
  amount     NUMERIC(16,2) NOT NULL,
  PRIMARY KEY (plan_id, month, account)
);

CREATE TABLE plan_driver (
  tenant_id       UUID NOT NULL REFERENCES tenant(id),
  plan_id         TEXT NOT NULL REFERENCES plan(id),
  month           DATE NOT NULL,
  new_customers   NUMERIC(8,2) NOT NULL,
  arpa_new        NUMERIC(12,2) NOT NULL,
  revenue_churn   NUMERIC(6,4) NOT NULL,
  net_expansion   NUMERIC(6,4) NOT NULL,
  PRIMARY KEY (plan_id, month)
);

CREATE TABLE plan_hire (
  id             UUID PRIMARY KEY,
  tenant_id      UUID NOT NULL REFERENCES tenant(id),
  plan_id        TEXT NOT NULL REFERENCES plan(id),
  role           TEXT NOT NULL,
  department     TEXT NOT NULL,
  location       TEXT NOT NULL,
  planned_start  DATE NOT NULL,
  salary_annual  NUMERIC(12,2) NOT NULL,
  status         TEXT NOT NULL CHECK (status IN ('planned', 'started', 'cancelled')),
  employee_id    UUID REFERENCES employee(id)
);

-- Payroll budget per person / planned hire per month: makes the payroll bridge exact
CREATE TABLE plan_payroll (
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  plan_id     TEXT NOT NULL REFERENCES plan(id),
  month       DATE NOT NULL,
  ref_type    TEXT NOT NULL CHECK (ref_type IN ('employee', 'hire')),
  ref_id      UUID NOT NULL,
  department  TEXT NOT NULL,
  amount      NUMERIC(12,2) NOT NULL,
  PRIMARY KEY (plan_id, month, ref_type, ref_id)
);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fred_readonly') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO fred_readonly;
  END IF;
END $$;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS plan_payroll, plan_hire, plan_driver, plan_line, plan, payroll, employee, gl_actual, gl_account
  CASCADE;
ALTER TABLE tenant DROP COLUMN fiscal_year_start_month;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
