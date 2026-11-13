"""Versioned knowledge base: benchmarks + playbooks as data, evaluated by code."""
from __future__ import annotations

import operator
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).parent
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


@lru_cache
def benchmarks() -> dict:
    return yaml.safe_load((HERE / "benchmarks.yaml").read_text())


@lru_cache
def playbooks() -> dict:
    return yaml.safe_load((HERE / "playbooks.yaml").read_text())


def stage_for(arr: float) -> dict:
    for s in benchmarks()["stages"]:
        if arr >= s.get("arr_min", 0) and arr < s.get("arr_max", float("inf")):
            return s
    return benchmarks()["stages"][-1]


def position(metric: str, value: float | None, stage: str) -> dict | None:
    spec = benchmarks()["metrics"].get(metric)
    if spec is None or value is None:
        return None
    band = spec["bands"][stage]
    up = spec["direction"] == "up"
    if (value >= band["target"]) if up else (value <= band["target"]):
        status = "at_or_above_target"
    elif (value < band["concern"]) if up else (value > band["concern"]):
        status = "below_concern"
    else:
        status = "between"
    return {"metric": metric, "value": value, "target": band["target"], "concern": band["concern"],
            "direction": spec["direction"], "status": status, "basis": spec["basis"]}


def staleness(today: date | None = None) -> dict:
    today = today or date.today()
    out = {}
    for name, kb in (("benchmarks", benchmarks()), ("playbooks", playbooks())):
        as_of = date.fromisoformat(str(kb["as_of"]))
        age = (today - as_of).days
        limit = benchmarks().get("review_every_days", 180)
        out[name] = {"version": kb["version"], "as_of": str(as_of), "age_days": age, "stale": age > limit,
                     "review_every_days": limit}
    return out


def matching_playbooks(facts: dict[str, Any]) -> list[dict]:
    hits = []
    for pb in playbooks()["playbooks"]:
        ok = True
        for fact, op, val in pb["when"]:
            v = facts.get(fact)
            if v is None or not OPS[op](v, val):
                ok = False
                break
        if ok:
            safe = {k: v for k, v in facts.items() if isinstance(v, (int, float))}
            hits.append({**pb, "title": pb["title"].format(**safe)})
    return hits
