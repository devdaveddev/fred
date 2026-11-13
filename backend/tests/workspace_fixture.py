"""Offline Workspace built from the demo generator (no database)."""
import pandas as pd

from app.metrics import TenantData, compute_metrics
from app.seed import GM, generate
from app.workspace import Workspace


def build_workspace() -> Workspace:
    g = generate(today=pd.Timestamp("2026-11-12").date())
    mrr: dict = {}
    for r in g.mrr:
        mrr.setdefault(r["a"], {})[r["m"]] = r["v"]
    sp = pd.DataFrame(g.spend)
    sp["ts"] = pd.to_datetime(sp["ts"]).dt.tz_localize(None)
    sp["month"] = sp["ts"].dt.to_period("M").dt.to_timestamp()
    monthly = lambda cat: {k.date(): v for k, v in sp[sp.c == cat].groupby("month")["amt"].sum().items()}
    d = TenantData("t", "acme", "Acme", "USD", GM, 0.14, mrr, monthly("sm"), monthly("other_opex"),
                   {r["m"]: r["c"] for r in g.cash})
    acc = pd.DataFrame(g.accounts).rename(columns={"id": "account_id"})
    acc["created_at"] = pd.to_datetime(acc["created_at"]).dt.tz_localize(None)
    ld = pd.DataFrame(g.leads).rename(columns={"id": "lead_id"})
    for c in ("created_at", "converted_at"):
        ld[c] = pd.to_datetime(ld[c], utc=True).dt.tz_localize(None)
    us = pd.DataFrame(g.usage).rename(columns={"a": "account_id", "w": "week", "s": "seats", "au": "active_users",
                                               "e": "events"})
    us["week"] = pd.to_datetime(us["week"])
    spd = (sp[sp.c == "sm"].assign(day=lambda x: x["ts"].dt.normalize()).groupby(["day", "ch"])["amt"].sum()
           .reset_index().rename(columns={"ch": "channel", "amt": "amount"}))
    fb = pd.DataFrame(g.feedback).rename(columns={"a": "account_id", "ts": "created_at"})
    fb["created_at"] = pd.to_datetime(fb["created_at"]).dt.tz_localize(None)
    ws = Workspace({"id": "t", "slug": "acme"}, d, compute_metrics(d), acc, ld, us, spd, fb)
    return ws
