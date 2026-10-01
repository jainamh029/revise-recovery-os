"""Scenario planner: slider overrides + one-at-a-time sensitivity (tornado)."""

from __future__ import annotations

from decimal import Decimal

from .economics import Assumptions, Shock, clamp, compute, d, to_plain

D = Decimal
LEVERS = [
    ("price_mult", "Resale price", "price_mult"),
    ("sell_through_mult", "Sell-through", "sell_through_mult"),
    ("exception_mult", "Exception rate", "exception_mult"),
    ("process_mult", "Process time / device", "process_mult"),
    ("repair_mult", "Repair cost", "repair_mult"),
    ("volume_mult", "Volume", "volume_mult"),
    ("uptime", "Robot uptime", "uptime"),
]


def shock_from(overrides: dict, a: Assumptions) -> Shock:
    kw = {}
    for k in (
        "price_mult",
        "sell_through_mult",
        "exception_mult",
        "process_mult",
        "repair_mult",
        "volume_mult",
    ):
        if overrides.get(k) is not None:
            kw[k] = d(overrides[k])
    if overrides.get("uptime") is not None:
        kw["uptime"] = d(overrides["uptime"])
    if overrides.get("return_rate") is not None:
        kw["return_rate"] = d(overrides["return_rate"])
    return Shock(**kw)


def _slim(e: dict) -> dict:
    return to_plain({k: v for k, v in e.items() if k != "per_line"})


def run(a: Assumptions, overrides: dict, swing: Decimal = D("0.10")) -> dict:
    base = compute(a)
    scen = compute(a, shock_from(overrides, a))
    tornado = []
    for key, label, field in LEVERS:
        lo_kw, hi_kw = {}, {}
        if key == "uptime":
            lo_kw[field], hi_kw[field] = (
                clamp(a.planned_uptime * (1 - swing), D("0.05")),
                clamp(a.planned_uptime * (1 + swing), D("0.05")),
            )
        else:
            lo_kw[field], hi_kw[field] = D(1) - swing, D(1) + swing
        lo, hi = compute(a, Shock(**lo_kw)), compute(a, Shock(**hi_kw))
        tornado.append(
            {
                "lever": key,
                "label": label,
                "cm_low": float(lo["contribution_margin"]),
                "cm_high": float(hi["contribution_margin"]),
                "delta_low": float(lo["contribution_margin"] - base["contribution_margin"]),
                "delta_high": float(hi["contribution_margin"] - base["contribution_margin"]),
                "swing": float(abs(hi["contribution_margin"] - lo["contribution_margin"])),
            }
        )
    tornado.sort(key=lambda r: -r["swing"])
    return {
        "base": _slim(base),
        "scenario": _slim(scen),
        "delta_cm": float(scen["contribution_margin"] - base["contribution_margin"]),
        "tornado": tornado,
        "swing_pct": float(swing),
    }
