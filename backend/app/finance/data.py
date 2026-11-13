"""Load the finance spine (accounts, actuals, payroll, plan) for one tenant as DataFrames."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass
class FinanceData:
    plan: dict
    accounts: pd.DataFrame  # code, name, pl_group, kind, department
    actual: pd.DataFrame  # month, account, amount
    plan_lines: pd.DataFrame  # month, account, amount
    drivers: pd.DataFrame  # month, new_customers, arpa_new, revenue_churn, net_expansion
    plan_payroll: pd.DataFrame  # month, ref_type, ref_id, department, amount
    payroll: pd.DataFrame  # month, employee_id, amount
    employees: pd.DataFrame
    hires: pd.DataFrame
    cash: pd.DataFrame  # month, cash

    @property
    def fy_start(self) -> pd.Timestamp:
        return pd.Timestamp(year=int(self.plan["fiscal_year"]), month=1, day=1)

    @property
    def closed_months(self) -> list[pd.Timestamp]:
        """Months with books closed inside the plan year."""
        ms = sorted(self.actual["month"].unique())
        return [pd.Timestamp(m) for m in ms if pd.Timestamp(m) >= self.fy_start]


def _q(conn: Connection, sql: str, t: str) -> pd.DataFrame:
    return pd.read_sql(text(sql), conn, params={"t": t})


def load_finance(conn: Connection, tenant_id: str) -> FinanceData | None:
    # The plan for the year whose books are open; a freshly published plan for next year waits until January.
    plan = conn.execute(text("""SELECT id, name, fiscal_year, approved_on, assumptions FROM plan
                                WHERE tenant_id = :t AND status = 'approved'
                                ORDER BY (fiscal_year = (SELECT extract(year FROM max(month))::int FROM gl_actual
                                                         WHERE tenant_id = :t)) DESC NULLS LAST,
                                         fiscal_year DESC LIMIT 1"""), {"t": tenant_id}).mappings().first()
    if not plan:
        return None
    pid = plan["id"]
    fd = FinanceData(
        plan=dict(plan),
        accounts=_q(conn, "SELECT code, name, pl_group, kind, department FROM gl_account WHERE tenant_id = :t "
                          "ORDER BY code", tenant_id),
        actual=_q(conn, "SELECT month, account, amount::float AS amount FROM gl_actual WHERE tenant_id = :t", tenant_id),
        plan_lines=pd.read_sql(text("SELECT month, account, amount::float AS amount FROM plan_line WHERE plan_id = :p"),
                               conn, params={"p": pid}),
        drivers=pd.read_sql(text("""SELECT month, new_customers::float, arpa_new::float, revenue_churn::float,
                                           net_expansion::float FROM plan_driver WHERE plan_id = :p ORDER BY month"""),
                            conn, params={"p": pid}),
        plan_payroll=pd.read_sql(text("""SELECT month, ref_type, ref_id::text AS ref_id, department,
                                                amount::float AS amount FROM plan_payroll WHERE plan_id = :p"""),
                                 conn, params={"p": pid}),
        payroll=_q(conn, "SELECT month, employee_id::text AS employee_id, amount::float AS amount FROM payroll "
                         "WHERE tenant_id = :t", tenant_id),
        employees=_q(conn, """SELECT id::text AS employee_id, name, title, department, location, start_date, end_date,
                                     salary_annual::float FROM employee WHERE tenant_id = :t""", tenant_id),
        hires=pd.read_sql(text("""SELECT id::text AS hire_id, role, department, location, planned_start,
                                         salary_annual::float, status, employee_id::text AS employee_id
                                  FROM plan_hire WHERE plan_id = :p ORDER BY planned_start"""),
                          conn, params={"p": pid}),
        cash=_q(conn, "SELECT month, cash::float AS cash FROM cash_balance WHERE tenant_id = :t ORDER BY month",
                tenant_id),
    )
    for df, cols in ((fd.actual, ["month"]), (fd.plan_lines, ["month"]), (fd.drivers, ["month"]),
                     (fd.plan_payroll, ["month"]), (fd.payroll, ["month"]),
                     (fd.employees, ["start_date", "end_date"]), (fd.hires, ["planned_start"]), (fd.cash, ["month"])):
        for c in cols:
            df[c] = pd.to_datetime(df[c])
    return fd
