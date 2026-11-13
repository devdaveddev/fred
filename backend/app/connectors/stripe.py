"""Stripe: customers + invoices -> accounts + monthly MRR per account.

Uses a restricted, read-only key (Customers: read, Invoices: read). MRR is rebuilt from invoice lines
the way subscription analytics tools do it:
  * only recurring lines count (subscription items); one-time charges and prorations are excluded
  * the line amount, net of discounts, is spread evenly over the months its service period covers
    (an annual invoice adds 1/12 to each of 12 months; a quarterly invoice 1/3 to each of 3)
  * draft, void and uncollectible invoices are ignored; months after the last closed month are dropped
  * invoices in currencies other than the dominant one are skipped and reported (no silent FX)
"""
from __future__ import annotations

import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import UTC, date, datetime

import httpx

FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "proton.me", "protonmail.com",
             "aol.com", "live.com", "me.com"}


class StripeError(RuntimeError):
    pass


class StripeClient:
    def __init__(self, key: str, base: str = "https://api.stripe.com/v1", transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=30,
                                 transport=transport)

    def get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(5):
            r = self.http.get(path, params=params)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(2 ** attempt, 20))
                continue
            if r.status_code == 401:
                raise StripeError("Stripe rejected the key. Use a restricted key with read access to Customers and Invoices.")
            if r.status_code == 403:
                raise StripeError("The key is missing a permission. It needs read access to Customers and Invoices.")
            if r.status_code >= 400:
                msg = r.json().get("error", {}).get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
                raise StripeError(f"Stripe error {r.status_code}: {msg}")
            return r.json()
        raise StripeError("Stripe kept rate-limiting the sync; try again in a few minutes.")

    def paginate(self, path: str, params: dict | None = None) -> Iterator[dict]:
        params = {"limit": 100, **(params or {})}
        while True:
            page = self.get(path, params)
            yield from page.get("data", [])
            if not page.get("has_more") or not page.get("data"):
                return
            params["starting_after"] = page["data"][-1]["id"]

    def check(self) -> None:
        self.get("/customers", {"limit": 1})
        self.get("/invoices", {"limit": 1})


def fetch(client: StripeClient, since: date) -> dict:
    created = int(datetime(since.year, since.month, 1, tzinfo=UTC).timestamp())
    customers = list(client.paginate("/customers"))
    invoices = []
    for inv in client.paginate("/invoices", {"created[gte]": created}):
        lines = inv.get("lines", {})
        if lines.get("has_more"):
            inv["lines"]["data"] = list(client.paginate(f"/invoices/{inv['id']}/lines"))
        invoices.append(inv)
    return {"customers": customers, "invoices": invoices}


# ------------------------------------------------------------------ normalisation (pure)
def _month(ts: int) -> date:
    d = datetime.fromtimestamp(ts, tz=UTC)
    return date(d.year, d.month, 1)


def _add(m: date, n: int) -> date:
    y, mo = divmod(m.month - 1 + n, 12)
    return date(m.year + y, mo + 1, 1)


def _is_recurring(line: dict) -> bool:
    if line.get("proration"):
        return False
    if line.get("type") == "subscription":
        return True
    parent = line.get("parent") or {}
    if parent.get("type") == "subscription_item_details":
        return not (parent.get("subscription_item_details") or {}).get("proration", False)
    price = line.get("price") or (line.get("pricing") or {}).get("price_details") or {}
    return bool(price.get("recurring"))


def _months_covered(line: dict) -> int:
    price = line.get("price") or {}
    rec = price.get("recurring") or {}
    if rec.get("interval"):
        per = {"day": 1 / 30.437, "week": 7 / 30.437, "month": 1, "year": 12}[rec["interval"]]
        return max(1, round(per * rec.get("interval_count", 1)))
    p = line.get("period") or {}
    days = (p.get("end", 0) - p.get("start", 0)) / 86400
    return max(1, round(days / 30.437))


def _net_amount(line: dict) -> int:
    disc = sum(d.get("amount", 0) for d in line.get("discount_amounts") or [])
    return int(line.get("amount", 0)) - int(disc)


def domain_of(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    d = email.split("@", 1)[1].lower().strip()
    return None if d in FREE_MAIL else d


def normalise(raw: dict, tenant_id: str, last_closed: date) -> dict:
    ns = uuid.UUID(tenant_id)
    warnings: list[str] = []
    invoices = [i for i in raw["invoices"] if i.get("status") in ("paid", "open")]
    currencies = Counter(i.get("currency", "usd") for i in invoices)
    base_ccy = currencies.most_common(1)[0][0] if currencies else "usd"
    skipped = [i for i in invoices if i.get("currency", "usd") != base_ccy]
    if skipped:
        warnings.append(f"Skipped {len(skipped)} invoices in other currencies ({', '.join(sorted({i['currency'].upper() for i in skipped}))}); "
                        f"all revenue is reported in {base_ccy.upper()}.")
    mrr: dict[tuple[str, date], float] = defaultdict(float)
    one_time = 0
    for inv in invoices:
        if inv.get("currency", "usd") != base_ccy or not inv.get("customer"):
            continue
        for line in (inv.get("lines") or {}).get("data", []):
            if not _is_recurring(line):
                one_time += 1
                continue
            amt = _net_amount(line) / 100
            if amt <= 0:
                continue
            n = _months_covered(line)
            start = _month((line.get("period") or {}).get("start") or inv["created"])
            for k in range(n):
                m = _add(start, k)
                if m <= last_closed:
                    mrr[(inv["customer"], m)] += amt / n
    custs = {c["id"]: c for c in raw["customers"]}
    paying = {cid for cid, _ in mrr}
    accounts = []
    for cid in paying:
        c = custs.get(cid, {"id": cid})
        email = c.get("email")
        name = c.get("name") or (domain_of(email) or cid)
        country = ((c.get("address") or {}).get("country")) or None
        accounts.append({"id": str(uuid.uuid5(ns, f"stripe:{cid}")), "external_id": cid, "name": name,
                         "domain": domain_of(email), "country": country,
                         "created_at": datetime.fromtimestamp(c.get("created", 0) or 0, tz=UTC)})
    rows = [{"a": str(uuid.uuid5(ns, f"stripe:{cid}")), "m": m, "v": round(v, 2)} for (cid, m), v in mrr.items() if v > 0]
    return {"accounts": accounts, "mrr": rows, "currency": base_ccy.upper(), "warnings": warnings,
            "stats": {"customers": len(raw["customers"]), "paying_customers": len(accounts),
                      "invoices": len(invoices), "excluded_one_time_lines": one_time,
                      "months": len({m for _, m in mrr})}}
