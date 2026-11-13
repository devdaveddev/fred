"""The work loop: findings become owned tasks; documents are generated, verified and kept.

Tasks remember the finding's impact when they were created; on every new data snapshot the tracker
compares it with today's impact for the same finding, so you can see whether the fix worked.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import audit, modules
from .auth import Ctx, current, require_module
from .db import get_conn
from .deliverables import documents as D
from .finance.bva import headcount
from .finance.data import load_finance
from .provenance import _clean, save_receipts
from .team_api import analysis, collect_findings

router = APIRouter()


# ------------------------------------------------------------------ tasks
class TaskIn(BaseModel):
    finding_id: str = Field(..., max_length=120)
    owner: str = Field(..., min_length=1, max_length=120)
    due_date: date | None = None
    title: str | None = Field(None, max_length=300)


class TaskPatch(BaseModel):
    status: str | None = Field(None, pattern="^(open|in_progress|done|cancelled)$")
    owner: str | None = Field(None, min_length=1, max_length=120)
    due_date: date | None = None


class NoteIn(BaseModel):
    finding_id: str = Field(..., max_length=120)
    note: str = Field(..., min_length=3, max_length=500)
    days: int = Field(90, ge=7, le=365)


def resolve_owner(conn: Connection, tid: str, owner: str) -> str | None:
    """Link a typed owner to a workspace member by email or exact name (case-insensitive)."""
    return conn.execute(text("""SELECT u.id::text FROM membership m JOIN app_user u ON u.id = m.user_id
                               WHERE m.tenant_id = :t AND (lower(u.email) = lower(:o) OR lower(u.name) = lower(:o))
                               ORDER BY (lower(u.email) = lower(:o)) DESC LIMIT 1"""),
                        {"t": tid, "o": owner.strip()}).scalar()


def _progress(task: dict, current: dict) -> dict:
    f = current.get(task["finding_id"])
    base = task["baseline_impact"]
    if f is None:
        return {"state": "resolved", "text": "No longer flagged in the latest data."}
    now = f.impact_usd
    if base is None or now is None or base == 0:
        return {"state": "open", "text": "Still flagged."}
    change = now / base - 1
    if task["baseline_snapshot"] == current["_snapshot"]:
        return {"state": "waiting", "text": "Waiting for the next data refresh to measure progress."}
    if change <= -0.1:
        return {"state": "improving", "text": f"Improving: impact down {abs(change):.0%} since assigned.", "change": change}
    if change >= 0.1:
        return {"state": "worse", "text": f"Getting worse: impact up {change:.0%} since assigned.", "change": change}
    return {"state": "flat", "text": "No measurable change yet.", "change": change}


@router.get("/tasks")
def list_tasks(conn: Connection = Depends(get_conn), dep=Depends(analysis)) -> dict:
    a, ctx = dep
    findings, notes, _ = collect_findings(conn, a, ctx, keep_muted=True)
    muted_ids = {n["finding_id"] for n in notes}
    names = {f.id: D.plain_title(f) for f in findings}
    findings = [f for f in findings if f.id not in muted_ids]
    current = {f.id: f for f in findings} | {"_snapshot": a.snapshot_id}
    rows = [dict(r) for r in conn.execute(text("""
        SELECT id::text, finding_id, module, title, steps, owner, owner_user_id::text, due_date, status,
               baseline_impact, baseline_snapshot, created_at, completed_at FROM task WHERE tenant_id = :t
        ORDER BY (status IN ('done','cancelled')), due_date"""), {"t": ctx["tenant"]["id"]}).mappings()]
    today = date.today()
    for r in rows:
        r["overdue"] = r["status"] in ("open", "in_progress") and r["due_date"] < today
        r["progress"] = _progress(r, current)
        r["due_date"], r["created_at"] = str(r["due_date"]), str(r["created_at"])
        r["completed_at"] = str(r["completed_at"]) if r["completed_at"] else None
    return _clean({"tasks": rows, "muted": [{**n, "label": names.get(n["finding_id"], n["finding_id"]),
                                              "suppress_until": str(n["suppress_until"]),
                                              "created_at": str(n["created_at"])} for n in notes]})


@router.post("/tasks")
def create_task(body: TaskIn, conn: Connection = Depends(get_conn), dep=Depends(analysis)) -> dict:
    a, ctx = dep
    findings, _, _ = collect_findings(conn, a, ctx)
    f = next((x for x in findings if x.id == body.finding_id), None)
    if f is None:
        raise HTTPException(404, "That finding is no longer in the latest data.")
    due = body.due_date or (date.today() + timedelta(days=7))
    row = conn.execute(text("""
        INSERT INTO task (tenant_id, finding_id, module, title, steps, owner, owner_user_id, due_date, baseline_impact,
                          baseline_snapshot, created_by)
        VALUES (:t, :fid, :m, :title, CAST(:steps AS JSONB), :owner, CAST(:oid AS UUID), :due, :impact, :snap, :u)
        RETURNING id::text"""),
        {"t": ctx["tenant"]["id"], "fid": f.id, "m": f.role, "title": body.title or D.plain_title(f),
         "steps": json.dumps(f.actions), "owner": body.owner.strip(),
         "oid": resolve_owner(conn, ctx["tenant"]["id"], body.owner), "due": due, "impact": f.impact_usd,
         "snap": a.snapshot_id, "u": ctx["ctx"].user_id}).scalar()
    return {"id": row}


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: TaskPatch, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    sets, params = [], {"id": task_id, "t": ctx.tenant_id}
    if body.status:
        sets.append("status = :status")
        sets.append("completed_at = CASE WHEN :status IN ('done','cancelled') THEN now() ELSE NULL END")
        params["status"] = body.status
    if body.owner:
        sets.append("owner = :owner")
        sets.append("owner_user_id = CAST(:oid AS UUID)")
        params["owner"] = body.owner.strip()
        params["oid"] = resolve_owner(conn, ctx.tenant_id, body.owner)
    if body.due_date:
        sets.append("due_date = :due")
        params["due"] = body.due_date
    if not sets:
        raise HTTPException(422, "Nothing to update.")
    n = conn.execute(text(f"UPDATE task SET {', '.join(sets)} WHERE id = CAST(:id AS UUID) AND tenant_id = :t"),
                     params).rowcount
    if not n:
        raise HTTPException(404, "Task not found.")
    return {"ok": True}


@router.post("/findings/mute")
def mute(body: NoteIn, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    """Business memory: 'this is expected' stops a finding from being flagged, with the reason kept."""
    conn.execute(text("""INSERT INTO finding_note (tenant_id, finding_id, note, suppress_until, created_by)
                         VALUES (:t, :f, :n, :u, :by)"""),
                 {"t": ctx.tenant_id, "f": body.finding_id, "n": body.note.strip(),
                  "u": date.today() + timedelta(days=body.days), "by": ctx.user_id})
    return {"ok": True}


@router.post("/findings/{finding_id}/unmute")
def unmute(finding_id: str, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    conn.execute(text("UPDATE finding_note SET suppress_until = current_date - 1 WHERE tenant_id = :t AND finding_id = :f"),
                 {"t": ctx.tenant_id, "f": finding_id})
    return {"ok": True}


# ------------------------------------------------------------------ documents
class DocIn(BaseModel):
    kind: str = Field(..., pattern="^(investor_update|board_pack|decision_memo)$")
    memo_type: str | None = Field(None, pattern="^(hire|price|spend)$")
    params: dict = Field(default_factory=dict)


def _ctx_for_docs(conn: Connection, a, ctx: dict) -> D.Ctx:
    ws = ctx["ws"]
    _, _, fin = collect_findings(conn, a, ctx)
    review, extra = (fin if fin else (None, None))
    if extra is None:
        fd = load_finance(conn, ctx["tenant"]["id"])
        extra = {"headcount": headcount(fd, fd.closed_months[-1])} if fd else {}
    tasks = [dict(r) for r in conn.execute(text("""SELECT id::text, finding_id, title, owner, due_date, status
                                                   FROM task WHERE tenant_id = :t ORDER BY due_date"""),
                                           {"t": ctx["tenant"]["id"]}).mappings()]
    return D.Ctx(ws, a, review, extra, tasks, ctx["tenant"]["name"])


@router.post("/documents", dependencies=[Depends(require_module("founders_office"))])
def create_document(body: DocIn, conn: Connection = Depends(get_conn), dep=Depends(analysis)) -> dict:
    a, ctx = dep
    c = _ctx_for_docs(conn, a, ctx)
    try:
        if body.kind == "investor_update":
            doc, check = D.investor_update(c)
        elif body.kind == "board_pack":
            doc, check = D.board_pack(c)
        else:
            if not body.memo_type:
                raise HTTPException(422, "Choose what the memo is about.")
            doc, check = D.decision_memo(c, body.memo_type, body.params)
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(422, f"Missing or invalid input: {e}") from e
    if not check["ok"]:  # never store a document with an untraceable number
        raise HTTPException(500, f"Refused to publish: untraceable numbers {check['untraced']}")
    save_receipts(conn, ctx["tenant"]["id"], c.receipts)
    did = conn.execute(text("""
        INSERT INTO document (tenant_id, kind, title, period, snapshot_id, body, facts, verification, created_by)
        VALUES (:t, :k, :title, :period, :snap, CAST(:body AS JSONB), CAST(:facts AS JSONB), CAST(:ver AS JSONB), :u)
        RETURNING id::text"""),
        {"t": ctx["tenant"]["id"], "k": body.kind, "title": doc["title"], "period": c.month_label,
         "snap": a.snapshot_id, "body": json.dumps(_clean(doc)), "facts": json.dumps(_clean(c.fb.public())),
         "ver": json.dumps(check), "u": ctx["ctx"].user_id}).scalar()
    audit.by(conn, ctx["ctx"], "document_created", did, {"kind": body.kind})
    return {"id": did, "verification": check}


@router.get("/documents")
def list_documents(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    rows = conn.execute(text("""SELECT d.id::text, d.kind, d.title, d.period, d.created_at, d.verification,
                                       u.name AS author
                                FROM document d LEFT JOIN app_user u ON u.id = d.created_by
                                WHERE d.tenant_id = :t ORDER BY d.created_at DESC LIMIT 100"""),
                        {"t": ctx.tenant_id}).mappings().all()
    return {"documents": [{**dict(r), "created_at": str(r["created_at"])} for r in rows],
            "can_create": "founders_office" in modules.hired(conn, ctx.tenant_id)}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    try:
        row = conn.execute(text("""SELECT id::text, kind, title, period, snapshot_id, body, facts, verification,
                                          created_at FROM document WHERE id = CAST(:id AS UUID) AND tenant_id = :t"""),
                           {"id": doc_id, "t": ctx.tenant_id}).mappings().first()
    except Exception as e:
        raise HTTPException(404, "Document not found.") from e
    if not row:
        raise HTTPException(404, "Document not found.")
    return {**dict(row), "created_at": str(row["created_at"])}


def to_markdown(doc: dict) -> str:
    out = [f"# {doc['title']}", f"_{doc['subtitle']}_", ""]
    for s in doc["sections"]:
        out.append(f"## {s['heading']}")
        for b in s["blocks"]:
            if b["type"] in ("p", "callout"):
                out += [b["text"], ""]
            elif b["type"] == "bullets":
                out += [f"- {x}" for x in b["items"]] + [""]
            elif b["type"] == "kpis":
                out += [f"- **{k['label']}:** {k['value']}" for k in b["items"]] + [""]
            elif b["type"] == "table":
                out.append("| " + " | ".join(b["columns"]) + " |")
                out.append("|" + "---|" * len(b["columns"]))
                out += ["| " + " | ".join(str(c) for c in r) + " |" for r in b["rows"]] + [""]
    return "\n".join(out)


@router.get("/documents/{doc_id}/markdown")
def document_markdown(doc_id: str, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    d = get_document(doc_id, ctx, conn)
    return {"markdown": to_markdown(d["body"])}

