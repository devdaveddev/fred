"""Build FinanceData + MRR frame from the generator without a database."""
from datetime import date

import pandas as pd

from app.finance.data import FinanceData
from app.seed import generate
from app.seed_finance import ACCOUNTS


def build(today: date = date(2026, 11, 12)):
    g = generate(today=today)
    p, plan = g.people, g.plan
    ts = lambda df, cols: df.assign(**{c: pd.to_datetime(df[c]) for c in cols})
    fd = FinanceData(
        plan={"id": plan["id"], "name": plan["name"], "fiscal_year": plan["fiscal_year"],
              "approved_on": plan["approved_on"], "assumptions": plan["assumptions"]},
        accounts=pd.DataFrame([{"code": c, "name": n, "pl_group": gr, "kind": k, "department": d}
                               for c, n, gr, k, d, _ in ACCOUNTS]),
        actual=ts(pd.DataFrame(g.gl).rename(columns={"m": "month", "acct": "account", "amt": "amount"}), ["month"]),
        plan_lines=ts(pd.DataFrame(plan["lines"]), ["month"]),
        drivers=ts(pd.DataFrame(plan["drivers"]), ["month"]),
        plan_payroll=ts(pd.DataFrame(plan["payroll"]), ["month"]),
        payroll=ts(pd.DataFrame(g.payroll).rename(columns={"m": "month", "e": "employee_id", "amt": "amount"}),
                   ["month"]),
        employees=ts(pd.DataFrame(p.employees).rename(columns={"id": "employee_id"}), ["start_date", "end_date"]),
        hires=ts(pd.DataFrame(p.planned_hires).rename(columns={"id": "hire_id"}), ["planned_start"]),
        cash=ts(pd.DataFrame(g.cash).rename(columns={"m": "month", "c": "cash"}), ["month"]),
    )
    mrr = pd.DataFrame([(r["a"], r["m"], r["v"]) for r in g.mrr], columns=["account_id", "month", "mrr"])
    mrr["month"] = pd.to_datetime(mrr["month"])
    return fd, mrr, g
