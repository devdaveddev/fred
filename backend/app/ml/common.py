"""Shared training + evaluation: gradient boosting, isotonic calibration, time-split holdout."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

ALGORITHM = "HistGradientBoosting + isotonic calibration"
PARAMS = {"max_iter": 250, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 30,
          "l2_regularization": 1.0, "random_state": 0}


@dataclass
class ModelCard:
    kind: str
    version: str
    algorithm: str
    snapshot_id: str
    features: list[str]
    n_train: int
    n_test: int
    metrics: dict[str, float]
    params: dict[str, Any] = field(default_factory=lambda: dict(PARAMS))
    notes: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return {k: getattr(self, k) for k in
                ("kind", "version", "algorithm", "snapshot_id", "features", "n_train", "n_test", "metrics", "params",
                 "notes")}


def _model() -> CalibratedClassifierCV:
    return CalibratedClassifierCV(HistGradientBoostingClassifier(**PARAMS), method="isotonic", cv=3)


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error: |predicted - observed| averaged over probability bins."""
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    err = 0.0
    for b in range(bins):
        mask = idx == b
        if mask.any():
            err += mask.mean() * abs(p[mask].mean() - y[mask].mean())
    return float(err)


def evaluate(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    order = np.argsort(-p)
    top = order[: max(1, len(p) // 10)]
    base = float(y.mean())
    return {
        "auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else float("nan"),
        "brier": float(brier_score_loss(y, p)),
        "calibration_error": ece(y, p),
        "base_rate": base,
        "top_decile_rate": float(y[top].mean()),
        "top_decile_lift": float(y[top].mean() / base) if base else float("nan"),
    }


def fit_time_split(X: pd.DataFrame, y: np.ndarray, t: pd.Series, test_frac: float = 0.25):
    """Train on the past, test on the most recent slice (no look-ahead), then refit on everything."""
    cut = t.quantile(1 - test_frac)
    tr, te = (t < cut).to_numpy(), (t >= cut).to_numpy()
    m = _model().fit(X[tr], y[tr])
    metrics = evaluate(y[te], m.predict_proba(X[te])[:, 1])
    final = _model().fit(X, y)
    return final, metrics, int(tr.sum()), int(te.sum())


def version_of(kind: str, snapshot_id: str, features: list[str]) -> str:
    raw = json.dumps([kind, snapshot_id, features, PARAMS], sort_keys=True)
    return f"{kind}-" + hashlib.sha256(raw.encode()).hexdigest()[:10]


def contributions(model, X: pd.DataFrame, groups: dict[str, list[str]], reference: pd.Series) -> pd.DataFrame:
    """Reason codes by counterfactual: how much does p move if this feature group were 'typical'?

    Model-agnostic, exact for the calibrated model, and cheap (one batch predict per group).
    Returns a frame of p - p_if_reference per group (positive = this feature pushes the score up).
    """
    p = model.predict_proba(X)[:, 1]
    out = {}
    for g, cols in groups.items():
        Xr = X.copy()
        for c in cols:
            Xr[c] = reference[c]
        out[g] = p - model.predict_proba(Xr)[:, 1]
    return pd.DataFrame(out, index=X.index)
