from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Unit = Literal["currency", "ratio", "percent", "months", "count", "multiple"]
Status = Literal["good", "watch", "bad", "neutral"]


class ReceiptOut(BaseModel):
    id: str
    metric: str
    value: float | None
    formula: str
    formula_version: str
    input_snapshot: str
    inputs: dict[str, Any]
    seed: int | None = None
    model_version: str | None = None


class MetricOut(BaseModel):
    id: str
    label: str
    group: str
    unit: Unit
    value: float | None
    status: Status
    benchmark: str | None
    help: str
    receipt: ReceiptOut


class SeriesPoint(BaseModel):
    month: str
    value: float
    receipt_id: str


class Envelope(BaseModel):
    """Every tool/endpoint answer has this shape (blueprint section 7)."""
    warnings: list[str] = []
    data_coverage: float = 1.0
    snapshot_id: str


class TenantOut(BaseModel):
    slug: str
    name: str
    currency: str


class OverviewOut(Envelope):
    tenant: TenantOut
    as_of: str
    metrics: list[MetricOut]
    mrr_series: list[SeriesPoint]


class ShockIn(BaseModel):
    price_change: float = Field(0.0, ge=-0.9, le=3)
    churn_multiplier: float = Field(1.0, ge=0.1, le=5)
    sm_spend_change: float = Field(0.0, ge=-1, le=5)
    cac_change: float = Field(0.0, ge=-0.9, le=5)


class ScenarioIn(BaseModel):
    shock: ShockIn
    price_elasticity: float = Field(-0.6, ge=-3, le=0, description="% change in new-logo volume per % price change")
    months: int = Field(24, ge=6, le=60)
    runs: int = Field(5000, ge=500, le=50000)
    seed: int = 42


class SimOutcome(BaseModel):
    arr_end: dict[str, float]
    arr_path: list[dict[str, float]]
    nrr_12m: dict[str, float]
    p_cash_out: float
    runway_months_p50: float
    cac: float | None
    cac_payback_months: float | None
    ltv_cac_wacc: float | None
    receipts: dict[str, ReceiptOut]


class ScenarioOut(Envelope):
    assumptions: dict[str, Any]
    baseline: SimOutcome
    scenario: SimOutcome
