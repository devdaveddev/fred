"""Data sources API: connect Stripe/HubSpot, upload CSVs, review the account mapping, see what each role needs."""
from __future__ import annotations

import json
import logging
from functools import lru_cache

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import audit, team
from .auth import Ctx, current, tenant_row
from .config import get_settings
from .connectors import csv_import, oauth, pipeline, registry, samples, secrets, writeback
from .connectors.http import SourceError
from .connectors.mapping import DEPARTMENTS
from .db import engine, get_conn
from .workspace import load_workspace

log = logging.getLogger("fred.sources")
router = APIRouter()
MAX_UPLOAD = 20 * 1024 * 1024


def admin(ctx: Ctx = Depends(current)) -> Ctx:
    if ctx.role not in ("owner", "admin"):
        raise HTTPException(403, "Only workspace owners and admins can change data sources.")
    return ctx


# ------------------------------------------------------------------ overview
@router.get("/sources")
def sources(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    t = tenant_row(conn, ctx)
    conns = [dict(r) | {"created_at": str(r["created_at"])} for r in conn.execute(text("""
        SELECT kind, secret_hint, config, created_at FROM connection WHERE tenant_id = :t ORDER BY kind"""),
        {"t": ctx.tenant_id}).mappings()]
    runs = [dict(r) | {"started_at": str(r["started_at"]), "finished_at": str(r["finished_at"]) if r["finished_at"] else None}
            for r in conn.execute(text("""
        SELECT id::text, source, status, started_at, finished_at, stats, warnings, error FROM sync_run
        WHERE tenant_id = :t ORDER BY started_at DESC LIMIT 30"""), {"t": ctx.tenant_id}).mappings()]
    last_ok = {}
    for r in reversed(runs):
        if r["status"] == "ok":
            last_ok[r["source"]] = r["finished_at"]
    readiness = {}
    if conn.execute(text("SELECT 1 FROM subscription_mrr WHERE tenant_id = :t LIMIT 1"), {"t": ctx.tenant_id}).first():
        readiness = team.readiness(load_workspace(conn, t))
    review = conn.execute(text("""SELECT count(*) FROM gl_account WHERE tenant_id = :t
                                  AND mapped_by IN ('model', 'default')"""), {"t": ctx.tenant_id}).scalar()
    return {"data_source": t["data_source"], "connections": conns, "runs": runs, "last_ok": last_ok,
            "readiness": readiness, "templates": csv_import.TEMPLATES, "mapping_to_review": review,
            "can_store_keys": bool(get_settings().secret_key),
            "catalog": [{"kind": x.kind, "name": x.name, "category": x.category, "gives": x.gives, "auth": x.auth,
                         "how": x.how, "key_label": x.key_label, "fields": x.fields, "writeback": x.writeback,
                         "available": registry.available(x.kind)} for x in registry.SOURCES.values()]}


# ------------------------------------------------------------------ API connectors
class KeyIn(BaseModel):
    key: str = Field(..., min_length=8, max_length=500)
    config: dict[str, str] = Field(default_factory=dict)


def _source(kind: str) -> registry.Source:
    if kind not in registry.SOURCES:
        raise HTTPException(404, "Unknown source")
    return registry.SOURCES[kind]


def run_sync(tid: str, kind: str, user_id: str | None) -> dict:
    """Fetch (no transaction open), then write everything in one transaction. Records the outcome either way."""
    src = registry.SOURCES[kind]
    with engine.begin() as conn:
        run_id = pipeline.start_run(conn, tid, kind, user_id)
    try:
        with engine.begin() as conn:
            secret, cfg = registry.load_credentials(conn, tid, kind)
        payload = src.fetch(tid, secret, cfg)
        with engine.begin() as conn:
            removed_demo = pipeline.ensure_real(conn, tid)
            stats, warnings = src.write(conn, tid, payload)
            if removed_demo:
                warnings.append("The demo company was removed now that your own data is connected.")
            pipeline.finish_run(conn, run_id, True, stats, warnings)
        if cfg.get("writeback") and src.writeback:
            run_writeback(tid, kind, user_id)
        from .team_api import warm_tenant

        warm_tenant(tid)
        return {"ok": True, "stats": stats}
    except Exception as e:  # recorded for the user; never leaves a half-written sync
        log.warning("sync %s for %s failed: %s", kind, tid, e)
        msg = str(e) if isinstance(e, SourceError | RuntimeError) else f"{type(e).__name__}: {e}"
        with engine.begin() as conn:
            pipeline.finish_run(conn, run_id, False, error=msg[:500])
        return {"ok": False, "error": msg}


@router.post("/sources/{kind}/connect")
def connect(kind: str, body: KeyIn, background: BackgroundTasks, ctx: Ctx = Depends(admin),
            conn: Connection = Depends(get_conn)) -> dict:
    src = _source(kind)
    if src.auth != "key":
        raise HTTPException(400, f"{src.name} connects by signing in, not with a key.")
    if not get_settings().secret_key:
        raise HTTPException(503, "This server has no FRED_SECRET_KEY set, so it cannot store credentials safely.")
    key = body.key.strip()
    cfg = {f["name"]: (body.config.get(f["name"]) or "").strip() for f in src.fields}
    missing = [f["label"] for f in src.fields if f.get("required") and not cfg[f["name"]]]
    if missing:
        raise HTTPException(422, f"Fill in: {', '.join(missing)}")
    try:
        src.check({"key": key}, cfg)  # fail fast with a helpful message before storing anything
    except (SourceError, RuntimeError) as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"Could not reach {src.name}: {e}") from e
    with engine.begin() as c:  # committed before the background sync starts
        c.execute(text("""INSERT INTO connection (tenant_id, kind, secret_enc, secret_hint, config, created_by)
                          VALUES (:t, :k, :s, :h, CAST(:c AS JSONB), :u)
                          ON CONFLICT (tenant_id, kind) DO UPDATE SET secret_enc = EXCLUDED.secret_enc,
                            secret_hint = EXCLUDED.secret_hint, config = connection.config || EXCLUDED.config,
                            created_at = now()"""),
                  {"t": ctx.tenant_id, "k": kind, "s": secrets.encrypt(json.dumps({"key": key})), "h": secrets.hint(key),
                   "c": json.dumps(cfg), "u": ctx.user_id})
        audit.by(c, ctx, "source_connected", kind, {"key_hint": secrets.hint(key)})
    background.add_task(run_sync, ctx.tenant_id, kind, ctx.user_id)
    return {"ok": True, "syncing": True}


@router.post("/sources/{kind}/oauth/start")
def oauth_start(kind: str, ctx: Ctx = Depends(admin), conn: Connection = Depends(get_conn)) -> dict:
    src = _source(kind)
    if src.auth != "oauth":
        raise HTTPException(400, f"{src.name} connects with a key.")
    if not oauth.configured(kind):
        raise HTTPException(503, f"{src.name} isn't enabled on this server yet: the operator needs to register a "
                                 f"{src.name} app and set {kind.upper()}_CLIENT_ID and {kind.upper()}_CLIENT_SECRET.")
    if not get_settings().secret_key:
        raise HTTPException(503, "This server has no FRED_SECRET_KEY set, so it cannot store credentials safely.")
    return {"redirect": oauth.start(conn, ctx.tenant_id, ctx.user_id, kind)}


@router.get("/sources/oauth/callback")
def oauth_callback(request: Request, background: BackgroundTasks, conn: Connection = Depends(get_conn)):
    base = get_settings().public_url.rstrip("/") + "/settings/data"
    q = dict(request.query_params)
    st = oauth.take_state(conn, q.get("state", "")) if q.get("state") else None
    if not st:
        return RedirectResponse(f"{base}?source_error=This+connection+link+expired.+Try+again.", 303)
    tid, kind, user_id, verifier = st
    name = registry.SOURCES[kind].name
    if q.get("error"):
        return RedirectResponse(f"{base}?source_error={name}+connection+was+cancelled.", 303)
    try:
        tok = oauth.exchange(kind, q.get("code", ""), verifier)
        cfg = registry.after_oauth(kind, tok, q)
    except SourceError as e:
        from urllib.parse import quote

        return RedirectResponse(f"{base}?source_error={quote(str(e))}", 303)
    with engine.begin() as c:  # committed before the background sync starts, so the sync can see it
        oauth.save(c, tid, kind, tok, cfg, user_id)
        audit.record(c, "source_connected", tenant_id=tid, user_id=user_id, target=kind, detail={"method": "oauth"})
    needs_more = [f for f in registry.SOURCES[kind].fields if f.get("required") and not cfg.get(f["name"])]
    if not needs_more:
        background.add_task(run_sync, tid, kind, user_id)
    return RedirectResponse(f"{base}?connected={kind}", 303)


class ConfigIn(BaseModel):
    config: dict[str, str | bool] = Field(default_factory=dict)


@router.put("/sources/{kind}/config")
def set_config(kind: str, body: ConfigIn, background: BackgroundTasks, ctx: Ctx = Depends(admin),
               conn: Connection = Depends(get_conn)) -> dict:
    """Non-secret settings after connecting (Google Ads customer ID) and the write-back switch."""
    src = _source(kind)
    allowed = {f["name"] for f in src.fields} | ({"writeback"} if src.writeback else set())
    cfg = {k: (v.strip() if isinstance(v, str) else v) for k, v in body.config.items() if k in allowed}
    with engine.begin() as c:  # committed before any background sync starts
        n = c.execute(text("""UPDATE connection SET config = config || CAST(:c AS JSONB)
                              WHERE tenant_id = :t AND kind = :k"""),
                      {"c": json.dumps(cfg), "t": ctx.tenant_id, "k": kind}).rowcount
        if n:
            audit.by(c, ctx, "writeback_changed" if "writeback" in cfg else "source_connected", kind, cfg)
    if not n:
        raise HTTPException(404, "Not connected.")
    if any(k != "writeback" for k in cfg):
        background.add_task(run_sync, ctx.tenant_id, kind, ctx.user_id)
    return {"ok": True}


@router.post("/sources/{kind}/sync")
def sync(kind: str, background: BackgroundTasks, ctx: Ctx = Depends(admin)) -> dict:
    _source(kind)
    background.add_task(run_sync, ctx.tenant_id, kind, ctx.user_id)
    return {"ok": True, "syncing": True}


@router.delete("/sources/{kind}")
def disconnect(kind: str, ctx: Ctx = Depends(admin), conn: Connection = Depends(get_conn)) -> dict:
    conn.execute(text("DELETE FROM connection WHERE tenant_id = :t AND kind = :k"), {"t": ctx.tenant_id, "k": kind})
    audit.by(conn, ctx, "source_disconnected", kind)
    return {"ok": True, "note": "Disconnected. Data already imported stays until you replace it."}


# ------------------------------------------------------------------ CRM write-back
def run_writeback(tid: str, kind: str, user_id: str | None, wait: bool = True) -> dict:
    """Push current lead scores and churn risk to the CRM. Logged like a sync, as 'writeback:<kind>'."""
    from . import team

    with engine.begin() as conn:
        run_id = pipeline.start_run(conn, tid, f"writeback:{kind}", user_id)
    try:
        with engine.begin() as conn:
            t = conn.execute(text("""SELECT id::text AS id, slug, name, currency, gross_margin, annual_wacc, data_source
                                     FROM tenant WHERE id = :t"""), {"t": tid}).mappings().first()
            a = team.get(conn, load_workspace(conn, dict(t)), wait=wait)
            lp, ap = writeback.payload(conn, tid, kind, a.scored_leads, a.at_risk)
            secret, _ = registry.load_credentials(conn, tid, kind)
        if kind == "hubspot":
            stats = writeback.hubspot_push(secret["key"], get_settings().hubspot_api_base, lp, ap)
        else:
            stats = writeback.salesforce_push(tid, lp, ap)
        with engine.begin() as conn:
            pipeline.finish_run(conn, run_id, True, stats,
                                [] if lp or ap else [("Nothing to write yet: no scored open leads or at-risk accounts "
                                                     "came from this CRM.")])
        return {"ok": True, "stats": stats}
    except team.NotReady:
        with engine.begin() as conn:  # not a failure: nothing was attempted yet
            conn.execute(text("DELETE FROM sync_run WHERE id = :id"), {"id": run_id})
        raise
    except Exception as e:
        msg = str(e) if isinstance(e, SourceError | RuntimeError) else f"{type(e).__name__}: {e}"
        with engine.begin() as conn:
            pipeline.finish_run(conn, run_id, False, error=msg[:500])
        return {"ok": False, "error": msg}


@router.post("/sources/{kind}/writeback")
def writeback_now(kind: str, ctx: Ctx = Depends(admin)) -> dict:
    if not registry.SOURCES.get(kind) or not registry.SOURCES[kind].writeback:
        raise HTTPException(404, "This source doesn't support write-back.")
    from . import team

    try:
        return run_writeback(ctx.tenant_id, kind, ctx.user_id, wait=False)
    except team.NotReady as e:
        raise HTTPException(409, "Your team is still analysing the latest data. Try again in a minute or two.") from e


# ------------------------------------------------------------------ CSV imports
@router.post("/imports/{kind}")
async def import_csv(kind: str, file: UploadFile = File(...), ctx: Ctx = Depends(admin),
                     conn: Connection = Depends(get_conn)) -> dict:
    if kind not in csv_import.PARSERS:
        raise HTTPException(404, "Unknown file type")
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "File is larger than 20 MB. Split it by year and upload each part.")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("latin-1")
    try:
        parsed = csv_import.PARSERS[kind](content)
    except csv_import.ImportProblem as e:
        raise HTTPException(422, str(e)) from e
    run_id = pipeline.start_run(conn, ctx.tenant_id, f"csv:{kind}", ctx.user_id)
    removed_demo = pipeline.ensure_real(conn, ctx.tenant_id)
    tid = ctx.tenant_id
    try:
        if kind == "billing":
            accounts, mrr = pipeline.billing_from_csv(tid, parsed)
            stats = pipeline.write_billing(conn, tid, accounts, mrr)
        elif kind == "gl":
            from .ml.embeddings import embed

            stats = pipeline.write_gl(conn, tid, parsed, embed=embed)
        elif kind == "cash":
            stats = pipeline.write_cash(conn, tid, parsed)
        elif kind == "payroll":
            stats = pipeline.write_payroll(conn, tid, parsed)
        elif kind == "budget":
            stats = pipeline.write_budget(conn, tid, parsed)
        elif kind == "hiring_plan":
            stats = pipeline.write_hiring_plan(conn, tid, parsed)
        elif kind == "spend":
            stats = pipeline.write_spend(conn, tid, parsed)
        elif kind == "feedback":
            stats = pipeline.write_feedback(conn, tid, parsed)
        else:
            stats = pipeline.write_usage(conn, tid, parsed)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # the whole import is one transaction: nothing was written
        log.exception("import %s failed for %s", kind, tid)
        raise HTTPException(500, f"Import failed and nothing was changed: {type(e).__name__}. "
                                 "Check the file against the sample, or contact support.") from e
    warnings = ["The demo company was removed now that your own data is coming in."] if removed_demo else []
    pipeline.finish_run(conn, run_id, True, stats, warnings)
    audit.by(conn, ctx, "file_imported", kind, {"file": file.filename, "bytes": len(raw)})
    return {"ok": True, "stats": stats, "warnings": warnings}


@lru_cache(maxsize=1)
def _samples() -> dict[str, str]:
    return samples.export()


@router.get("/imports/{kind}/sample", response_class=PlainTextResponse)
def sample(kind: str, _: Ctx = Depends(current)) -> PlainTextResponse:
    files = _samples()
    if kind not in files:
        raise HTTPException(404, "Unknown file type")
    return PlainTextResponse(files[kind], media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="fred-sample-{kind}.csv"'})


# ------------------------------------------------------------------ account mapping
class MapIn(BaseModel):
    pl_group: str = Field(..., pattern="^(revenue|cogs|sm|rnd|ga)$")
    kind: str = Field(..., pattern="^(revenue|cogs|payroll|nonpayroll)$")
    department: str | None = None


@router.get("/mapping/accounts")
def accounts(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    rows = [dict(r) for r in conn.execute(text("""
        SELECT g.code, g.name, g.section, g.pl_group, g.kind, g.department, g.mapped_by, g.confidence,
               coalesce(sum(a.amount), 0)::float AS total
        FROM gl_account g LEFT JOIN gl_actual a ON a.tenant_id = g.tenant_id AND a.account = g.code
        WHERE g.tenant_id = :t GROUP BY g.code, g.name, g.section, g.pl_group, g.kind, g.department, g.mapped_by,
                                        g.confidence
        ORDER BY g.code"""), {"t": ctx.tenant_id}).mappings()]
    return {"accounts": rows, "departments": DEPARTMENTS}


@router.put("/mapping/accounts/{code}")
def set_mapping(code: str, body: MapIn, ctx: Ctx = Depends(admin), conn: Connection = Depends(get_conn)) -> dict:
    if body.kind == "payroll" and body.department not in DEPARTMENTS:
        raise HTTPException(422, "Payroll accounts need a department.")
    n = conn.execute(text("""UPDATE gl_account SET pl_group = :g, kind = :k, department = :d, mapped_by = 'user',
                             confidence = 1 WHERE tenant_id = :t AND code = :c"""),
                     {"g": body.pl_group, "k": body.kind, "d": body.department if body.kind == "payroll" else None,
                      "t": ctx.tenant_id, "c": code}).rowcount
    if not n:
        raise HTTPException(404, "Account not found")
    pipeline.derive_spend_from_gl(conn, ctx.tenant_id)
    audit.by(conn, ctx, "mapping_changed", code, body.model_dump())
    return {"ok": True}
