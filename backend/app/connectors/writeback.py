"""Write Fred's lead scores and churn risk back into the CRM, so sales and CS see them where they work.

HubSpot: Fred creates its own properties (fred_score, fred_reason on contacts; fred_churn_risk, fred_churn_reason
on companies) and batch-updates them. Salesforce: custom fields can't be created through the REST API, so an
admin adds them once (Fred lists exactly which); updates go through composite collections.
Only records Fred imported from that CRM are touched, matched by their CRM id.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import oauth, salesforce
from .http import Api, SourceError

HUBSPOT_PROPS = {
    "contacts": [("fred_score", "Fred lead score", "number", "number"), ("fred_reason", "Fred: why", "string", "text")],
    "companies": [("fred_churn_risk", "Fred churn risk (%)", "number", "number"),
                  ("fred_churn_reason", "Fred: churn driver", "string", "text")],
}


def _reason(rs) -> str:
    if isinstance(rs, list | tuple):
        return "; ".join(str(r) for r in rs[:2])[:250]
    return str(rs or "")[:250]


def payload(conn: Connection, tid: str, crm: str, scored: pd.DataFrame, at_risk: pd.DataFrame) -> tuple[list, list]:
    leads = dict(conn.execute(text("""SELECT id::text, external_id FROM lead
                                      WHERE tenant_id = :t AND crm = :c AND external_id IS NOT NULL"""),
                              {"t": tid, "c": crm}).all())
    accts = dict(conn.execute(text("""SELECT id::text, crm_id FROM account
                                      WHERE tenant_id = :t AND crm = :c AND crm_id IS NOT NULL"""),
                              {"t": tid, "c": crm}).all())
    lp = [{"id": leads[r.lead_id], "score": round(float(r.p) * 100), "reason": _reason(r.reasons)}
          for r in scored.itertuples() if r.lead_id in leads] if len(scored) else []
    ap = [{"id": accts[r.account_id], "risk": round(float(r.p) * 100), "reason": _reason(r.reasons)}
          for r in at_risk.itertuples() if r.account_id in accts] if len(at_risk) else []
    return lp, ap


def hubspot_push(token: str, base: str, lp: list, ap: list) -> dict:
    api = Api("HubSpot", base, headers={"Authorization": f"Bearer {token}"},
              hints={403: "HubSpot refused the update. Add the write scopes to the private app: "
                          "crm.objects.contacts.write, crm.objects.companies.write, crm.schemas.contacts.write, "
                          "crm.schemas.companies.write."})
    for obj, props in HUBSPOT_PROPS.items():
        for name, label, typ, field_type in props:
            r = api.http.post(f"/crm/v3/properties/{obj}", json={"name": name, "label": label, "type": typ,
                                                                 "fieldType": field_type, "groupName": f"{obj[:-1]}information"})
            if r.status_code == 403:
                raise SourceError(api.hints[403])
            if r.status_code not in (200, 201, 409):  # 409: already exists
                raise SourceError(f"HubSpot wouldn't create the {name} property: {r.text[:150]}")
    out = {}
    for obj, rows, props in (("contacts", lp, lambda r: {"fred_score": r["score"], "fred_reason": r["reason"]}),
                             ("companies", ap, lambda r: {"fred_churn_risk": r["risk"], "fred_churn_reason": r["reason"]})):
        n = 0
        for i in range(0, len(rows), 100):
            api.post(f"/crm/v3/objects/{obj}/batch/update",
                     json={"inputs": [{"id": r["id"], "properties": props(r)} for r in rows[i:i + 100]]})
            n += len(rows[i:i + 100])
        out[obj] = n
    return out


def salesforce_push(tid: str, lp: list, ap: list) -> dict:
    token, cfg = oauth.access_token(tid, "salesforce")
    api = salesforce.client(token, cfg["instance_url"])
    missing = salesforce.missing_fields(api)
    if missing:
        raise SourceError("Ask a Salesforce admin to add these custom fields (Setup → Object Manager), then run again: "
                          + ", ".join(missing) + ". Use Number for the score/risk fields and Text(255) for the reasons.")
    leads = salesforce.push(api, "Lead", [{"id": r["id"], "Fred_Score__c": r["score"], "Fred_Reason__c": r["reason"]}
                                          for r in lp]) if lp else {"updated": 0}
    accts = salesforce.push(api, "Account", [{"id": r["id"], "Fred_Churn_Risk__c": r["risk"],
                                              "Fred_Churn_Reason__c": r["reason"]} for r in ap]) if ap else {"updated": 0}
    return {"leads": leads["updated"], "accounts": accts["updated"],
            "failed": leads.get("failed", 0) + accts.get("failed", 0)}
