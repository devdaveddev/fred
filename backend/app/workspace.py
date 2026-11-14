"""Load everything the analyst team needs for one tenant, pinned to one snapshot."""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .metrics import MetricSet, TenantData, compute_metrics, ensure_snapshot, load_tenant_data

PAID_CHANNELS = ["paid_search", "linkedin", "events", "content"]


@dataclass
class Workspace:
    tenant: dict
    data: TenantData
    metrics: MetricSet
    accounts: pd.DataFrame
    leads: pd.DataFrame
    usage: pd.DataFrame
    spend_daily: pd.DataFrame  # day, channel, amount (S&M only)
    feedback: pd.DataFrame  # account_id, created_at, kind, score, body

    @property
    def snapshot_id(self) -> str:
        return self.metrics.snapshot_id

    @property
    def as_of(self):
        return self.metrics.as_of

    def mrr_frame(self) -> pd.DataFrame:
        rows = [(a, m, v) for a, s in self.data.mrr.items() for m, v in s.items()]
        df = pd.DataFrame(rows, columns=["account_id", "month", "mrr"])
        df["month"] = pd.to_datetime(df["month"])
        return df


def _hash(*frames: pd.DataFrame) -> str:
    h = hashlib.sha256()
    for f in frames:
        h.update(str(len(f)).encode())
        if len(f):
            h.update(str(int(pd.util.hash_pandas_object(f, index=False).sum())).encode())
    return h.hexdigest()[:16]


def _load(conn: Connection, tenant: dict) -> Workspace:
    t = {"t": tenant["id"]}
    accounts = pd.read_sql(text("""
        SELECT id::text AS account_id, name, segment, acquisition_channel, employees, industry, country, created_at,
               description
        FROM account WHERE tenant_id = :t ORDER BY id"""), conn, params=t)
    leads = pd.read_sql(text("""
        SELECT id::text AS lead_id, created_at, source_channel, company, contact_name, industry, employees, country,
               seniority, pricing_views, docs_views, email_engagement, demo_requested, webinar, status, converted_at,
               description
        FROM lead WHERE tenant_id = :t ORDER BY id"""), conn, params=t)
    usage = pd.read_sql(text("""
        SELECT account_id::text AS account_id, week, seats, active_users, events
        FROM usage_weekly WHERE tenant_id = :t ORDER BY account_id, week"""), conn, params=t)
    spend = pd.read_sql(text("""
        SELECT date_trunc('day', ts)::date AS day, channel, sum(amount)::float AS amount
        FROM spend WHERE tenant_id = :t AND category = 'sm'
          -- channel detail from ad platforms (or demo), plus people cost from the books for loaded CAC
          AND (source IN ('ads', 'demo') OR channel = 'sales_team')
        GROUP BY 1, 2 ORDER BY 1, 2"""), conn, params=t)
    feedback = pd.read_sql(text("""
        SELECT account_id::text AS account_id, created_at, kind, score, body
        FROM feedback WHERE tenant_id = :t ORDER BY created_at"""), conn, params=t)
    feedback["created_at"] = pd.to_datetime(feedback["created_at"], utc=True).dt.tz_localize(None)
    # Timestamps -> naive UTC; dates -> datetime64. Keeps every downstream comparison simple.
    for df, c in ((leads, "created_at"), (leads, "converted_at"), (accounts, "created_at")):
        df[c] = pd.to_datetime(df[c], utc=True).dt.tz_localize(None)
    usage["week"] = pd.to_datetime(usage["week"])
    spend["day"] = pd.to_datetime(spend["day"])

    data = load_tenant_data(conn, tenant)
    fin = conn.execute(text("""
        SELECT (SELECT count(*) || ':' || coalesce(sum(amount), 0) FROM gl_actual WHERE tenant_id = :t) || '|' ||
               (SELECT count(*) || ':' || coalesce(sum(amount), 0) FROM plan_line WHERE tenant_id = :t) || '|' ||
               (SELECT count(*) || ':' || coalesce(sum(amount), 0) FROM payroll WHERE tenant_id = :t)"""), t).scalar()
    data.extra_hash = _hash(accounts.drop(columns=["created_at"]), leads[["lead_id", "status"]],
                            usage[["account_id", "active_users"]], feedback[["kind", "body"]],
                            spend.assign(day=spend["day"].astype(str))) \
        + hashlib.sha256(str(fin).encode()).hexdigest()[:8]
    # Own transaction: the snapshot row must exist even if this request's transaction rolls back (a page that
    # answers "still analysing" does), because the cached workspace and later receipts refer to it.
    from .db import engine

    with engine.begin() as c:
        ensure_snapshot(c, data)
    return Workspace(tenant=tenant, data=data, metrics=compute_metrics(data), accounts=accounts, leads=leads,
                     usage=usage, spend_daily=spend, feedback=feedback)


# ---------------- cache: reload only when the data changed ----------------
# A cheap fingerprint (counts and sums computed inside Postgres, a few ms) stands in for re-reading ~70k rows
# and re-hashing them on every request. Any write that changes the data changes the fingerprint.
_FINGERPRINT = text("""
SELECT concat_ws('|',
  (SELECT count(*) || ':' || coalesce(sum(mrr), 0) FROM subscription_mrr WHERE tenant_id = :t),
  (SELECT count(*) || ':' || coalesce(sum(length(name) + length(coalesce(description, '')) + coalesce(employees, 0)
          + length(coalesce(industry, '')) + length(coalesce(segment, '')) + length(coalesce(acquisition_channel, ''))), 0)
     FROM account WHERE tenant_id = :t),
  (SELECT count(*) || ':' || count(*) FILTER (WHERE status = 'converted') || ':' || count(*) FILTER (WHERE status = 'open')
          || ':' || coalesce(max(created_at)::text, '') || ':' || coalesce(sum(length(coalesce(description, ''))), 0)
     FROM lead WHERE tenant_id = :t),
  (SELECT count(*) || ':' || coalesce(sum(active_users), 0) FROM usage_weekly WHERE tenant_id = :t),
  (SELECT count(*) || ':' || coalesce(sum(amount), 0) FROM spend WHERE tenant_id = :t),
  (SELECT count(*) || ':' || coalesce(sum(length(body)), 0) FROM feedback WHERE tenant_id = :t),
  (SELECT count(*) || ':' || coalesce(sum(amount), 0) FROM gl_actual WHERE tenant_id = :t),
  (SELECT count(*) || ':' || coalesce(sum(length(pl_group || kind || coalesce(department, ''))), 0)
     FROM gl_account WHERE tenant_id = :t),
  (SELECT count(*) || ':' || coalesce(sum(amount), 0) FROM plan_line WHERE tenant_id = :t),
  (SELECT count(*) || ':' || coalesce(sum(amount), 0) FROM payroll WHERE tenant_id = :t),
  (SELECT count(*) || ':' || coalesce(sum(cash), 0) FROM cash_balance WHERE tenant_id = :t),
  (SELECT gross_margin || ':' || annual_wacc || ':' || data_source FROM tenant WHERE id = :t),
  current_date)""")
_ws_cache: dict[str, tuple[str, Workspace]] = {}
_ws_lock = threading.Lock()


def load_workspace(conn: Connection, tenant: dict) -> Workspace:
    """The workspace's data, re-read from the database only when its fingerprint changed."""
    key = conn.execute(_FINGERPRINT, {"t": tenant["id"]}).scalar() + "|" + str(sorted(tenant.items()))
    with _ws_lock:
        hit = _ws_cache.get(tenant["id"])
    if hit and hit[0] == key:
        return hit[1]
    ws = _load(conn, tenant)
    with _ws_lock:
        if len(_ws_cache) >= 8 and tenant["id"] not in _ws_cache:
            _ws_cache.pop(next(iter(_ws_cache)))
        _ws_cache[tenant["id"]] = (key, ws)
    return ws
