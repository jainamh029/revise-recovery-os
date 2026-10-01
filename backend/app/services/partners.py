"""Transparent partner scorecard: every component and weight is returned with the score."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CohortBudgetVersion, DeviceCohort, Partner
from .economics import clamp, d, safe_div
from .finance import cohort_actuals
from .variance import collection_lag, reforecast

D = Decimal
WEIGHTS = {"margin": D("0.40"), "quality": D("0.20"), "cash": D("0.20"), "plan_adherence": D("0.20")}
LIVE = (
    "in_intake",
    "in_processing",
    "exception_review",
    "quality_control",
    "listed_for_sale",
    "partially_sold",
    "closed",
)


def scorecard(db: Session, partner: Partner, as_of: date, cohort_ids: set[str] | None = None) -> dict:
    cohorts = list(db.scalars(select(DeviceCohort).where(DeviceCohort.partner_id == partner.id)))
    if cohort_ids is not None:  # dashboard filters: score only the cohorts in scope
        cohorts = [c for c in cohorts if c.id in cohort_ids]
    live = [c for c in cohorts if c.status in LIVE]
    base = {
        "partner_id": partner.id,
        "name": partner.name,
        "partner_type": partner.partner_type,
        "status": partner.status,
        "cohorts": [{"id": c.id, "code": c.cohort_code, "status": c.status} for c in cohorts],
        "weights": {k: float(v) for k, v in WEIGHTS.items()},
    }
    if not live:
        declined = [c for c in cohorts if c.status == "declined"]
        return {
            **base,
            "score": None,
            "recommendation": "no_history" if not declined else "decline_pattern",
            "note": "Only declined / pending cohorts -- no operating history."
            if declined
            else "No operating history yet.",
            "components": {},
            "metrics": {},
        }
    cm = exc_num = started = fc_cm = plan_cm = recv = D(0)
    fc_rev = D(0)
    fpy_num = fpy_den = D(0)
    lags, lag_w = D(0), D(0)
    for c in live:
        act = cohort_actuals(db, c.id, as_of)
        fc = reforecast(db, c, as_of)
        v = db.get(CohortBudgetVersion, c.approved_budget_version_id)
        u = act["units"]
        exc_num += u["exceptions"]
        started += u["started"]
        recv += u["received"]
        fpy_num += u["first_pass"]
        fpy_den += u["completed"]
        if fc:
            fc_cm += D(str(fc["economics"]["contribution_margin"]))
            fc_rev += D(str(fc["economics"]["revenue"]))
            plan_cm += d(v.expected_contribution_margin)
        cm += act["contribution_margin"]
        lag = collection_lag(db, c.id, as_of)
        if lag is not None and act["revenue"] > 0:
            lags += lag * act["revenue"]
            lag_w += act["revenue"]
    cm_pct = safe_div(fc_cm, fc_rev, D(0))
    exc = safe_div(exc_num, started, D(0))
    lag = safe_div(lags, lag_w)
    adherence = safe_div(fc_cm, plan_cm, D(0)) if plan_cm > 0 else D(0)
    comp = {
        "margin": clamp(cm_pct / D("0.40")),
        "quality": clamp(D(1) - exc / D("0.35")),
        "cash": clamp(D(1) - max((lag or D(30)) - D(30), D(0)) / D(60)),
        "plan_adherence": clamp(adherence),
    }
    score = sum(comp[k] * WEIGHTS[k] for k in comp) * 100
    rec = "expand" if score >= 75 else "renegotiate" if score >= 50 else "reduce_or_stop"
    return {
        **base,
        "score": float(round(score, 1)),
        "recommendation": rec,
        "components": {k: float(round(v * 100, 1)) for k, v in comp.items()},
        "metrics": {
            "forecast_cm_pct": float(cm_pct),
            "forecast_cm": float(fc_cm),
            "plan_cm": float(plan_cm),
            "realized_cm_to_date": float(cm),
            "devices_received": int(recv),
            "forecast_margin_per_device": float(safe_div(fc_cm, recv, D(0))) if recv else None,
            "realized_margin_per_device": float(safe_div(cm, recv, D(0))) if recv else None,
            "exception_rate": float(exc),
            "first_pass_yield": float(safe_div(fpy_num, fpy_den, D(0))),
            "avg_collection_lag_days": float(lag) if lag is not None else None,
        },
    }
