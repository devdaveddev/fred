"""Receipts: content-addressed records of how every number was produced.

A receipt id is a hash of (metric, value, inputs, snapshot, formula version, seed), so the same
question over the same data always yields the same receipt id. Writes are idempotent.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .revcore import FORMULA_VERSION, FORMULAS


def _clean(v: Any) -> Any:
    """JSON-safe: inf/nan become null, numpy scalars become floats."""
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


@dataclass
class Receipt:
    metric: str
    value: float | None
    snapshot_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    model_version: str | None = None
    formula_version: str = FORMULA_VERSION

    @property
    def formula(self) -> str:
        return FORMULAS.get(self.metric, self.metric)

    @property
    def id(self) -> str:
        payload = json.dumps(
            [self.metric, _clean(self.value), _clean(self.inputs), self.snapshot_id,
             self.formula_version, self.seed, self.model_version],
            sort_keys=True, default=str,
        )
        return "r_" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "metric": self.metric, "value": _clean(self.value),
            "formula": self.formula, "formula_version": self.formula_version,
            "input_snapshot": self.snapshot_id, "inputs": _clean(self.inputs),
            "seed": self.seed, "model_version": self.model_version,
        }


def save_receipts(conn: Connection, tenant_id: str, receipts: list[Receipt]) -> None:
    if not receipts:
        return
    conn.execute(
        text("""
            INSERT INTO receipt (id, tenant_id, snapshot_id, metric, value, formula_version, formula,
                                 inputs, seed, model_version)
            VALUES (:id, :tenant_id, :snapshot_id, :metric, :value, :formula_version, :formula,
                    CAST(:inputs AS JSONB), :seed, :model_version)
            ON CONFLICT (id) DO NOTHING
        """),
        [
            {
                "id": r.id, "tenant_id": tenant_id, "snapshot_id": r.snapshot_id, "metric": r.metric,
                "value": _clean(r.value), "formula_version": r.formula_version, "formula": r.formula,
                "inputs": json.dumps(_clean(r.inputs), default=str), "seed": r.seed,
                "model_version": r.model_version,
            }
            for r in receipts
        ],
    )


def get_receipt(conn: Connection, tenant_id: str, receipt_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        text("""
            SELECT r.id, r.metric, r.value, r.formula, r.formula_version, r.snapshot_id AS input_snapshot,
                   r.inputs, r.seed, r.model_version, r.created_at, s.as_of_month, s.row_counts
            FROM receipt r JOIN snapshot s ON s.id = r.snapshot_id
            WHERE r.tenant_id = :t AND r.id = :id
        """),
        {"t": tenant_id, "id": receipt_id},
    ).mappings().first()
    return dict(row) if row else None
