"""Documents: every number must trace to a computed fact; the guard must catch invented ones."""
import pandas as pd
import pytest
from finance_fixture import build
from workspace_fixture import build_workspace

from app import team
from app.deliverables import documents as D
from app.deliverables.facts import FactBook, walk_text
from app.finance.bva import headcount, review


@pytest.fixture(scope="module")
def ctx_factory():
    ws = build_workspace()
    a = team.run(ws)
    fd, mrr, _ = build()
    r = review(fd, mrr)
    for i, row in enumerate(r["pnl"]):
        row["receipt_id"] = f"r_pnl{i}"
    for i, f in enumerate(r["findings"]):
        f["receipt_id"] = f"r_f{i}"
    extra = {"headcount": headcount(fd, pd.Timestamp(r["month"]))}
    tasks = [{"id": "t1", "finding_id": "churn_risk", "title": "Call the top 10 at-risk accounts", "owner": "Priya",
              "due_date": "2026-11-20", "status": "open"}]
    return lambda: D.Ctx(ws, a, r, extra, tasks, "Testco")


@pytest.mark.parametrize("make", [
    lambda c: D.investor_update(c),
    lambda c: D.board_pack(c),
    lambda c: D.decision_memo(c, "hire", {"role": "Account Executive", "salary_annual": 115000, "count": 2}),
    lambda c: D.decision_memo(c, "price", {"price_change": 0.15, "churn_multiplier": 1.1}),
    lambda c: D.decision_memo(c, "spend", {"spend_change": 0.3}),
])
def test_every_number_traces_to_a_fact(ctx_factory, make):
    c = ctx_factory()
    _body, check = make(c)
    assert check["ok"], check["untraced"]
    assert check["numbers"] > 5
    data_facts = [f for f in c.fb.facts.values() if f.unit not in ("constant", "user-written task", "playbook wording")]
    assert all(f.receipt_id for f in data_facts if f.key not in ("price_change", "spend_change", "elasticity",
                                                                  "churn_mult")), "data facts need receipts"


def test_documents_are_deterministic(ctx_factory):
    a, _ = D.board_pack(ctx_factory())
    b, _ = D.board_pack(ctx_factory())
    assert a == b


def test_guard_catches_invented_numbers():
    fb = FactBook()
    real = fb.money("arr", 7_080_000, "r_1")
    body = {"sections": [{"blocks": [{"type": "p", "text": f"ARR is {real}, and we expect $9.5M next year."}]}]}
    check = fb.verify(walk_text(body))
    assert not check["ok"] and "$9.5M" in check["untraced"]


def test_hire_memo_recommendation_tracks_runway(ctx_factory):
    small, _ = D.decision_memo(ctx_factory(), "hire", {"role": "SDR", "salary_annual": 65000, "count": 1})
    huge, _ = D.decision_memo(ctx_factory(), "hire", {"role": "Engineer", "salary_annual": 200000, "count": 15})
    tone = lambda d: next(b for s in d["sections"] for b in s["blocks"] if b["type"] == "callout")["tone"]
    assert tone(huge) == "bad" and tone(small) in ("good", "watch")


def test_plain_titles_carry_no_numbers(ctx_factory):
    import re

    from app.finance_api import team_findings

    c = ctx_factory()
    findings = list(c.a.findings) + team_findings(c.fin, limit=20)
    for f in findings:
        t = D.plain_title(f)
        assert t and not re.search(r"\d", t), (f.id, t)
