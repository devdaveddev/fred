"""GET /accuracy: the "Fred vs your last six closes" track record, cached per data snapshot."""
from __future__ import annotations

from collections import OrderedDict

from fastapi import APIRouter, Depends
from sqlalchemy.engine import Connection

from . import accuracy
from .db import get_conn
from .finance.data import load_finance
from .provenance import _clean
from .team_api import analysis

router = APIRouter()
_cache: OrderedDict[str, dict] = OrderedDict()


@router.get("/accuracy")
def track_record(conn: Connection = Depends(get_conn), dep=Depends(analysis)) -> dict:
    a, ctx = dep
    key = f"{ctx['tenant']['id']}:{a.snapshot_id}"
    if key not in _cache:
        _cache[key] = _clean(accuracy.report(ctx["ws"], a, load_finance(conn, ctx["tenant"]["id"])))
        while len(_cache) > 16:
            _cache.popitem(last=False)
    return {"snapshot_id": a.snapshot_id, "as_of": a.as_of, **_cache[key]}
