"""CSV imports: exact round trip of the demo company through every file format, plus error messages."""
from collections import defaultdict
from datetime import date

import pytest

from app.connectors import csv_import as ci
from app.connectors import samples
from app.seed import generate


@pytest.fixture(scope="module")
def g():
    return generate(today=date(2026, 11, 12))


@pytest.fixture(scope="module")
def files(g):
    return samples.export(g)


def test_quickbooks_style_gl_round_trips_exactly(g, files):
    parsed = ci.gl(files["gl"])
    assert parsed["format"] == "wide"
    assert len(parsed["accounts"]) == 17  # totals and section headers are not accounts
    sections = {a["code"]: a["section"] for a in parsed["accounts"]}
    assert sections["4000"] == "Income" and sections["5000"] == "Cost of Goods Sold" and sections["8100"] == "Expenses"
    got, want = defaultdict(float), defaultdict(float)
    for r in parsed["amounts"]:
        got[(r["account"], r["month"])] += r["amount"]
    for r in g.gl:
        want[(r["acct"], r["m"])] += r["amt"]
    assert set(got) == set(want)
    assert all(abs(got[k] - want[k]) < 0.01 for k in want)


def test_other_files_round_trip(g, files):
    b = ci.billing(files["billing"])
    assert abs(sum(r["mrr"] for r in b) - sum(r["v"] for r in g.mrr)) < 1
    pay = ci.payroll(files["payroll"])
    assert len(pay) == len(g.people.employees) and {p["department"] for p in pay} <= set(ci.DEPTS.values())
    bud = ci.budget(files["budget"])
    assert abs(sum(r["amount"] for r in bud) - sum(r["amount"] for r in g.plan["lines"])) < 1
    assert len(ci.hiring_plan(files["hiring_plan"])) == sum(h["status"] != "cancelled" for h in g.people.planned_hires)
    sp = ci.spend(files["spend"])
    want = sum(r["amt"] for r in g.spend if r["c"] == "sm" and r["ch"] != "sales_team")
    assert abs(sum(r["amount"] for r in sp) - want) < 1
    assert len(ci.feedback(files["feedback"])) == len(g.feedback)
    assert len(ci.usage(files["usage"])) == len(g.usage)
    assert len(ci.cash(files["cash"])) == len(g.cash)


@pytest.mark.parametrize("v,want", [("2026-03", date(2026, 3, 1)), ("Mar 2026", date(2026, 3, 1)),
                                    ("March 2026", date(2026, 3, 1)), ("03/2026", date(2026, 3, 1)),
                                    ("2026-03-15", date(2026, 3, 1)), ("Mar-26", date(2026, 3, 1))])
def test_month_formats(v, want):
    assert ci.parse_month(v) == want


@pytest.mark.parametrize("v,want", [("$1,234.50", 1234.5), ("(1,234)", -1234), ("-12", -12), ("1234-", -1234),
                                    ("€ 99", 99), ("", None), ("12,4O0", None)])
def test_amounts(v, want):
    assert ci.parse_amount(v) == want


def test_errors_say_what_to_fix():
    with pytest.raises(ci.ImportProblem, match="Missing column"):
        ci.billing("customer,month\nx,2026-01\n")
    with pytest.raises(ci.ImportProblem, match="Row 2: mrr '12,4O0'"):
        ci.billing("customer_id,month,mrr\nc1,2026-01,\"12,4O0\"\n")
    with pytest.raises(ci.ImportProblem, match="department 'Wizards'"):
        ci.payroll("name,title,department,start_date,salary_annual\nA,B,Wizards,2025-01-01,100000\n")


def test_mapping_recovers_every_demo_account(files):
    """Round trip through the QuickBooks-style export: every account lands on its original P&L line."""
    from app.connectors.mapping import classify
    from app.seed_finance import ACCOUNTS

    parsed = ci.gl(files["gl"])
    got = classify([a["name"] for a in parsed["accounts"]], [a["section"] for a in parsed["accounts"]])
    truth = {c: (g, k, d) for c, _, g, k, d, _ in ACCOUNTS}
    for a, g in zip(parsed["accounts"], got):
        want = truth[a["code"]]
        assert (g["pl_group"], g["kind"]) == want[:2], (a["name"], g)
        if want[1] == "payroll":
            assert g["department"] == want[2], (a["name"], g)
