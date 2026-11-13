"""FactBook + verifier: the rule that no number reaches a document unless it came from computed data.

Writers never format numbers themselves. They call `fb.money("arr", value, receipt_id)` and get back
the display string; the FactBook records (string -> fact). The verifier then scans the finished text
and reports any numeric token that no fact produced. That guard is what will let a language model
polish wording later without being able to invent a number.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

NUM_RE = re.compile(r"(?<![\w.])[-+−]?\$?\d[\d,]*(?:\.\d+)?(?:\s?[KMB%x])?(?![\w])")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


@dataclass
class Fact:
    key: str
    value: float | None
    unit: str
    receipt_id: str | None
    label: str
    shown: list[str] = field(default_factory=list)


class FactBook:
    def __init__(self) -> None:
        self.facts: dict[str, Fact] = {}
        self._allowed: dict[str, str] = {}  # normalised display string -> fact key

    # ---- formatting (the only place numbers become text)
    def _record(self, key: str, value, unit: str, rid: str | None, label: str, text: str) -> str:
        f = self.facts.setdefault(key, Fact(key, None if value is None else float(value), unit, rid, label))
        f.shown.append(text)
        for tok in NUM_RE.findall(text):
            self._allowed[_norm(tok)] = key
        return text

    def money(self, key: str, v: float, rid: str | None = None, label: str = "", signed: bool = False) -> str:
        a = abs(v)
        s = f"${a / 1e6:.2f}M" if a >= 1e6 else (f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:,.0f}")
        if signed:
            s = ("+" if v >= 0 else "−") + s
        elif v < 0:
            s = "−" + s
        return self._record(key, v, "currency", rid, label, s)

    def pct(self, key: str, v: float, rid: str | None = None, label: str = "", signed: bool = False, digits: int = 0) -> str:
        s = f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"
        return self._record(key, v, "percent", rid, label, s.replace("-", "−"))

    def num(self, key: str, v: float, rid: str | None = None, label: str = "", digits: int = 0, suffix: str = "") -> str:
        s = f"{v:,.{digits}f}{suffix}"
        return self._record(key, v, "number", rid, label, s.replace("-", "−"))

    def months(self, key: str, v: float, rid: str | None = None, label: str = "") -> str:
        if v is None or not math.isfinite(v):
            return self._record(key, None, "months", rid, label, "not burning")
        return self._record(key, v, "months", rid, label, f"{v:.0f} months")

    def multiple(self, key: str, v: float, rid: str | None = None, label: str = "") -> str:
        return self._record(key, v, "multiple", rid, label, f"{v:.1f}x")

    def const(self, key: str, v: float, text: str) -> str:
        """Numbers that are definitions, not data (e.g. 'a 3-month horizon'). Still recorded."""
        return self._record(key, v, "constant", None, "definition", text)

    def prose(self, key: str, text: str, source: str) -> str:
        """Text written by a person or fixed wording (task titles, playbook steps). Its numbers are not data
        claims, so they are allowed, and labelled with where they came from."""
        return self._record(key, None, source, None, source, text)

    def public(self) -> list[dict]:
        return [{"key": f.key, "value": f.value, "unit": f.unit, "receipt_id": f.receipt_id, "label": f.label,
                 "shown": sorted(set(f.shown))} for f in self.facts.values()]

    # ---- verification
    def verify(self, texts: list[str]) -> dict:
        found, untraced = 0, []
        for t in texts:
            for tok in NUM_RE.findall(t):
                n = _norm(tok)
                if YEAR_RE.match(n):
                    continue
                found += 1
                if n not in self._allowed:
                    untraced.append(tok)
        with_receipt = sum(1 for f in self.facts.values() if f.receipt_id)
        return {"numbers": found, "traced": found - len(untraced), "untraced": sorted(set(untraced)),
                "facts": len(self.facts), "facts_with_receipt": with_receipt, "ok": not untraced}


def _norm(tok: str) -> str:
    return tok.replace("−", "-").replace(" ", "").lstrip("+-")


def walk_text(node) -> list[str]:
    """Every string inside a document body (sections, bullets, tables)."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [t for k, v in node.items() if k not in ("receipt_id", "kind", "id") for t in walk_text(v)]
    if isinstance(node, list):
        return [t for v in node for t in walk_text(v)]
    return []
