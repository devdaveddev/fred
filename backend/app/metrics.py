"""Metric computation over canonical tables.

`compute_metrics` is pure (TenantData in, receipts out) so it can be tested against hand-computed
fixtures. `load_tenant_data` is the only place that touches SQL.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import revcore as rc
from .provenance import Receipt

WINDOW = 3  # trailing months for flow metrics (CAC, churn, burn)


@dataclass
class TenantData:
    tenant_id: str
    slug: str
    name: str
    currency: str
    gross_margin: float
    annual_wacc: float
    mrr: dict[str, dict[date, float]]  # account -> month -> mrr
    sm: dict[date, float]
    opex: dict[date, float]
    cash: dict[date, float]
    months: list[date] = field(default_factory=list)
    extra_hash: str = ""  # hash of non-billing inputs (leads, usage) so snapshots pin those too

    def __post_init__(self) -> None:
        if not self.months:
            self.months = sorted({m for series in self.mrr.values() for m in series})


@dataclass
class MetricSet:
    snapshot_id: str
    as_of: date
    receipts: dict[str, Receipt]
    mrr_series: list[Receipt]
    baseline: rc.Baseline | None
    warnings: list[str]
    data_coverage: float


def snapshot_id_for(d: TenantData) -> tuple[str, str, dict[str, int]]:
    payload = json.dumps(
        {
            "mrr": {a: {str(m): v for m, v in sorted(s.items())} for a, s in sorted(d.mrr.items())},
            "sm": {str(m): v for m, v in sorted(d.sm.items())},
            "opex": {str(m): v for m, v in sorted(d.opex.items())},
            "cash": {str(m): v for m, v in sorted(d.cash.items())},
            "gm": d.gross_margin, "wacc": d.annual_wacc, "extra": d.extra_hash,
        },
        sort_keys=True,
    )
    h = hashlib.sha256(payload.encode()).hexdigest()
    as_of = d.months[-1] if d.months else date.today().replace(day=1)
    counts = {
        "accounts": len(d.mrr),
        "mrr_rows": sum(len(s) for s in d.mrr.values()),
        "spend_months": len(d.sm),
        "cash_months": len(d.cash),
    }
    return f"snap_{as_of:%Y-%m}_{d.slug}_{h[:8]}", h, counts


def compute_metrics(d: TenantData) -> MetricSet:
    snap, _, _ = snapshot_id_for(d)
    months = d.months
    warnings: list[str] = []
    if not months:
        return MetricSet(snap, date.today(), {}, [], None, ["No billing data loaded yet."], 0.0)

    idx = len(months) - 1
    as_of = months[idx]

    def mrr_at(i: int) -> float:
        m = months[i]
        return sum(s.get(m, 0.0) for s in d.mrr.values())

    def active(i: int) -> set[str]:
        m = months[i]
        return {a for a, s in d.mrr.items() if s.get(m, 0.0) > 0}

    first_paid = {a: min(m for m, v in s.items() if v > 0) for a, s in d.mrr.items() if any(v > 0 for v in s.values())}
    window = list(range(max(1, idx - WINDOW + 1), idx + 1))
    window_months = [months[i] for i in window]

    R: dict[str, Receipt] = {}

    def rec(metric: str, value: float | None, **inputs) -> Receipt:
        r = Receipt(metric=metric, value=value, snapshot_id=snap, inputs={"as_of": str(as_of), **inputs})
        R[metric] = r
        return r

    # --- size
    mrr_now = mrr_at(idx)
    act_now = active(idx)
    rec("mrr", mrr_now, month=str(as_of), active_accounts=len(act_now))
    rec("arr", mrr_now * 12, mrr=mrr_now)
    rec("active_customers", len(act_now), month=str(as_of))
    arpa = mrr_now / len(act_now) if act_now else 0.0
    rec("arpa", arpa, mrr=mrr_now, active_customers=len(act_now))

    # --- acquisition
    new_customers = sum(1 for m in first_paid.values() if m in window_months)
    sm_window = sum(d.sm.get(m, 0.0) for m in window_months)
    rec("new_customers", new_customers, window=[str(m) for m in window_months])
    cac_v = rc.cac(sm_window, new_customers)
    rec("cac", cac_v, sm_spend=sm_window, new_customers=new_customers, window_months=len(window))

    # --- retention flow
    churn_rates, exp_rates = [], []
    for i in window:
        prev, cur = active(i - 1), active(i)
        if prev:
            churn_rates.append(len(prev - cur) / len(prev))
        prev_mrr = mrr_at(i - 1)
        if prev_mrr:
            net_exp = sum(d.mrr[a].get(months[i], 0.0) - d.mrr[a].get(months[i - 1], 0.0) for a in prev & cur)
            exp_rates.append(net_exp / prev_mrr)
    churn = sum(churn_rates) / len(churn_rates) if churn_rates else 0.0
    expansion = sum(exp_rates) / len(exp_rates) if exp_rates else 0.0
    rec("monthly_logo_churn", churn, monthly_rates=churn_rates)
    rec("monthly_net_expansion", expansion, monthly_rates=exp_rates)

    # --- unit economics
    gm, wacc = d.gross_margin, d.annual_wacc
    rec("cac_payback_months", rc.cac_payback_months(cac_v, arpa, gm), cac=cac_v, arpa=arpa, gross_margin=gm)
    ltv = rc.ltv_wacc(arpa, gm, churn, wacc)
    rec("ltv_wacc", ltv, arpa=arpa, gross_margin=gm, monthly_churn=churn, annual_wacc=wacc, horizon_months=120)
    rec("ltv_cac", rc.ltv_cac(ltv, cac_v), ltv=ltv, cac=cac_v)

    # --- cohort retention (12 months)
    if idx >= 12:
        start_i = idx - 12
        cohort = active(start_i)
        sm_, em_ = months[start_i], as_of
        start = sum(d.mrr[a][sm_] for a in cohort)
        exp_ = con_ = churned = 0.0
        for a in cohort:
            s0, s1 = d.mrr[a][sm_], d.mrr[a].get(em_, 0.0)
            if s1 == 0:
                churned += s0
            elif s1 > s0:
                exp_ += s1 - s0
            else:
                con_ += s0 - s1
        parts = dict(start_mrr=start, expansion=exp_, contraction=con_, churned=churned,
                     cohort_month=str(sm_), cohort_accounts=len(cohort))
        rec("nrr_12m", rc.nrr(start, exp_, con_, churned), **parts)
        rec("grr_12m", rc.grr(start, con_, churned), **parts)
    else:
        warnings.append("Less than 13 months of billing history: 12-month NRR and GRR are unavailable.")

    # --- efficiency
    if idx >= 5:
        rev_q = sum(mrr_at(i) for i in range(idx - 2, idx + 1))
        rev_pq = sum(mrr_at(i) for i in range(idx - 5, idx - 2))
        sm_pq = sum(d.sm.get(months[i], 0.0) for i in range(idx - 5, idx - 2))
        rec("magic_number", rc.magic_number(rev_q, rev_pq, sm_pq),
            revenue_q=rev_q, revenue_prev_q=rev_pq, sm_spend_prev_q=sm_pq)
    else:
        warnings.append("Less than 6 months of history: Magic Number is unavailable.")

    burns = [d.sm.get(m, 0.0) + d.opex.get(m, 0.0) - mrr_at(i) * gm for i, m in zip(window, window_months)]
    net_burn_monthly = sum(burns) / len(burns)
    rec("net_burn_monthly", net_burn_monthly, monthly_burns=burns, gross_margin=gm)
    base_i = max(0, idx - len(window))
    net_new_arr = (mrr_now - mrr_at(base_i)) * 12
    rec("burn_multiple", rc.burn_multiple(sum(burns), net_new_arr),
        net_burn=sum(burns), net_new_arr=net_new_arr, window_months=len(window))

    cash_now = d.cash.get(as_of)
    if cash_now is None:
        warnings.append(f"No cash balance for {as_of:%b %Y}: runway is unavailable.")
    else:
        rec("runway_months", rc.runway_months(cash_now, net_burn_monthly),
            cash=cash_now, net_burn_monthly=net_burn_monthly)

    # --- coverage: share of window months that have spend data
    covered = sum(1 for m in window_months if m in d.sm and m in d.opex)
    coverage = covered / len(window_months)
    if coverage < 0.8:
        warnings.append("Spend data is missing for part of the window: CAC and burn are understated.")

    series = [
        Receipt(metric="mrr", value=mrr_at(i), snapshot_id=snap, inputs={"as_of": str(as_of), "month": str(months[i])})
        for i in range(len(months))
    ]

    baseline = rc.Baseline(
        customers=len(act_now), arpa_monthly=arpa, gross_margin=gm, monthly_churn=churn,
        monthly_expansion=expansion, new_customers_monthly=new_customers / len(window),
        sm_spend_monthly=sm_window / len(window),
        other_opex_monthly=sum(d.opex.get(m, 0.0) for m in window_months) / len(window),
        cash=cash_now or 0.0, annual_wacc=wacc,
    )
    return MetricSet(snap, as_of, R, series, baseline, warnings, coverage)


# ---------- SQL loading ----------
def get_tenant(conn: Connection, slug: str) -> dict | None:
    row = conn.execute(
        text("SELECT id::text AS id, slug, name, currency, gross_margin, annual_wacc FROM tenant WHERE slug = :s"),
        {"s": slug},
    ).mappings().first()
    return dict(row) if row else None


def load_tenant_data(conn: Connection, tenant: dict) -> TenantData:
    t = tenant["id"]
    mrr: dict[str, dict[date, float]] = {}
    for a, m, v in conn.execute(
        text("SELECT account_id::text, month, mrr FROM subscription_mrr WHERE tenant_id = :t"), {"t": t}
    ):
        mrr.setdefault(a, {})[m] = float(v)

    sm: dict[date, float] = {}
    opex: dict[date, float] = {}
    for cat, m, v in conn.execute(
        text("""
            SELECT category, date_trunc('month', ts)::date AS month, sum(amount)
            FROM spend WHERE tenant_id = :t
              -- the books are the source of truth for totals: when GL-derived spend exists, use only that
              AND (source = 'gl' OR NOT EXISTS (SELECT 1 FROM spend g WHERE g.tenant_id = :t AND g.source = 'gl'))
            GROUP BY 1, 2
        """),
        {"t": t},
    ):
        (sm if cat == "sm" else opex)[m] = float(v)

    cash = {m: float(v) for m, v in conn.execute(
        text("SELECT month, cash FROM cash_balance WHERE tenant_id = :t"), {"t": t}
    )}
    return TenantData(
        tenant_id=t, slug=tenant["slug"], name=tenant["name"], currency=tenant["currency"],
        gross_margin=float(tenant["gross_margin"]), annual_wacc=float(tenant["annual_wacc"]),
        mrr=mrr, sm=sm, opex=opex, cash=cash,
    )


def ensure_snapshot(conn: Connection, d: TenantData) -> str:
    snap, h, counts = snapshot_id_for(d)
    conn.execute(
        text("""
            INSERT INTO snapshot (id, tenant_id, as_of_month, content_hash, row_counts)
            VALUES (:id, :t, :as_of, :h, CAST(:c AS JSONB)) ON CONFLICT (id) DO NOTHING
        """),
        {"id": snap, "t": d.tenant_id, "as_of": d.months[-1] if d.months else date.today().replace(day=1),
         "h": h, "c": json.dumps(counts)},
    )
    return snap
