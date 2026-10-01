"""Reforecast (actuals-informed re-run of the approved model) and plan-vs-actual variance."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CapacityCalendar,
    CashReceipt,
    CohortBudgetVersion,
    DailyOperation,
    DeviceCohort,
    Invoice,
    ResaleListing,
    SalesTransaction,
)
from ..policy import get_policy
from .economics import Shock, assumptions_from_dict, clamp, compute, d, safe_div, to_plain
from .finance import cash_conversion, cohort_actuals
from .operations import daily_series, plan_to_date, recent_throughput

D = Decimal
ZERO, ONE = D(0), D(1)


def approved_base(db: Session, cohort: DeviceCohort) -> CohortBudgetVersion | None:
    if cohort.approved_budget_version_id:
        return db.get(CohortBudgetVersion, cohort.approved_budget_version_id)
    return db.scalar(
        select(CohortBudgetVersion)
        .where(CohortBudgetVersion.cohort_id == cohort.id, CohortBudgetVersion.scenario_type == "base")
        .order_by(CohortBudgetVersion.version_number.desc())
    )


def _blend(plan: Decimal, obs: Decimal | None, w: Decimal) -> Decimal:
    return plan if obs is None else plan + w * (obs - plan)


def reforecast(db: Session, cohort: DeviceCohort, as_of: date) -> dict | None:
    ver = approved_base(db, cohort)
    if not ver or not ver.assumptions:
        return None
    a = assumptions_from_dict(ver.assumptions)
    plan = compute(a)
    act = cohort_actuals(db, cohort.id, as_of)
    u = act["units"]
    started = D(u["started"])
    w = clamp(
        started / max(D(a.units) * D("0.5"), ONE)
    )  # credibility: full weight once half the cohort is through

    plan_exc = plan["exception_rate"]
    # Exceptions to date are facts; remaining devices are projected at the recent (last 3 operating days) run-rate.
    obs_exc = None
    if started > 0:
        tail = daily_series(db, cohort.id, as_of)[-3:]
        t_started = sum(r["started"] for r in tail)
        recent = (
            safe_div(D(sum(r["exceptions"] for r in tail)), D(t_started))
            if t_started >= 30
            else safe_div(D(u["exceptions"]), started)
        )
        remaining_units = max(D(a.units) - started, ZERO)
        obs_exc = (D(u["exceptions"]) + remaining_units * recent) / max(D(a.units), started)
    exc_mult = safe_div(_blend(plan_exc, obs_exc, w), plan_exc, ONE) if plan_exc > 0 else ONE

    plan_min = safe_div(plan["robot_hours"] * 60, plan["units_processed"], ZERO)
    obs_min = safe_div(act["hours"]["productive"] * 60, started) if started > 0 else None
    process_mult = safe_div(_blend(plan_min, obs_min, w), plan_min, ONE) if plan_min > 0 else ONE

    plan_rep = safe_div(plan["repair_cost"], plan["exception_units"], ZERO)
    obs_rep = (
        safe_div(act["costs_by_category"].get("repair_parts", ZERO), D(u["exceptions"]))
        if u["exceptions"] > 0
        else None
    )
    repair_mult = safe_div(_blend(plan_rep, obs_rep, w), plan_rep, ONE) if plan_rep > 0 else ONE

    obs_up = (
        safe_div(act["hours"]["productive"], act["hours"]["productive"] + act["hours"]["downtime"])
        if act["hours"]["productive"] > 0
        else None
    )
    uptime = _blend(a.planned_uptime, obs_up, w)

    sold = D(act["sales"]["units_sold"])
    w_s = clamp(sold / max(plan["units_sold"] * D("0.25"), ONE))
    obs_price = safe_div(act["sales"]["gross"], sold) if sold >= 10 else None
    price_mult = (
        safe_div(_blend(plan["avg_resale_price"], obs_price, w_s), plan["avg_resale_price"], ONE)
        if plan["avg_resale_price"] > 0
        else ONE
    )
    obs_ret = safe_div(D(act["sales"]["units_returned"]), sold) if sold >= 10 else None
    return_rate = _blend(a.return_rate, obs_ret, w_s)

    shock = Shock(
        price_mult=price_mult,
        exception_mult=exc_mult,
        process_mult=process_mult,
        repair_mult=repair_mult,
        uptime=uptime,
        return_rate=return_rate,
    )
    econ = compute(a, shock)
    floor_pct = d(cohort.margin_floor_pct)
    return {
        "as_of": as_of.isoformat(),
        "economics": to_plain({k: v for k, v in econ.items() if k != "per_line"}),
        "plan_cm": float(ver.expected_contribution_margin),
        "plan_cm_pct": (
            float(d(ver.expected_contribution_margin) / d(ver.expected_revenue))
            if d(ver.expected_revenue) > 0
            else None
        ),
        "delta_cm": float(econ["contribution_margin"] - d(ver.expected_contribution_margin)),
        "margin_floor_pct": float(floor_pct),
        # CM% is on net recognised revenue; an undefined CM% (net revenue <= 0) counts as below the floor.
        "below_floor": bool(econ["cm_pct"] is None or econ["cm_pct"] < floor_pct),
        "negative": bool(econ["contribution_margin"] < get_policy(db).critical_negative_margin_threshold),
        "credibility": float(w),
        "drivers": {
            "exception_mult": float(exc_mult),
            "process_mult": float(process_mult),
            "repair_mult": float(repair_mult),
            "price_mult": float(price_mult),
            "uptime": float(uptime),
            "return_rate": float(return_rate),
            "observed_exception_rate": float(obs_exc) if obs_exc is not None else None,
            "plan_exception_rate": float(plan_exc),
        },
    }


def _status(var_pct: Decimal | None, higher_better: bool, warn: Decimal, bad: Decimal) -> str:
    if var_pct is None:
        return "n/a"
    adverse = -var_pct if higher_better else var_pct
    return "off_track" if adverse > bad else "watch" if adverse > warn else "on_track"


def _row(key, label, unit, plan, actual, higher_better, warn=D("0.05"), bad=D("0.10")) -> dict:
    plan, actual = (None if plan is None else d(plan)), (None if actual is None else d(actual))
    var = None if plan is None or actual is None else actual - plan
    pct = None if var is None or plan in (None, 0) else var / abs(plan)
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "plan": plan,
        "actual": actual,
        "variance": var,
        "variance_pct": pct,
        "status": _status(pct, higher_better, warn, bad),
    }


def collection_lag(db: Session, cohort_id: str, as_of: date) -> Decimal | None:
    """Amount-weighted days from sale/invoice date to cash (or to today if still unpaid)."""
    num = den = ZERO
    for s in db.scalars(
        select(SalesTransaction).where(
            SalesTransaction.cohort_id == cohort_id, SalesTransaction.sale_date <= as_of
        )
    ):
        paid = [r for r in db.scalars(select(CashReceipt).where(CashReceipt.sales_transaction_id == s.id))]
        amt = d(s.net_sale_amount)
        end = (
            max((r.receipt_date for r in paid), default=None)
            if sum((d(r.amount) for r in paid), ZERO) >= amt
            else as_of
        )
        if s.sale_date and amt > 0:
            num += amt * (end - s.sale_date).days
            den += amt
    for i in db.scalars(select(Invoice).where(Invoice.cohort_id == cohort_id, Invoice.invoice_date <= as_of)):
        paid = [r for r in db.scalars(select(CashReceipt).where(CashReceipt.invoice_id == i.id))]
        end = (
            max((r.receipt_date for r in paid), default=None)
            if sum((d(r.amount) for r in paid), ZERO) >= d(i.amount)
            else as_of
        )
        num += d(i.amount) * (end - i.invoice_date).days
        den += d(i.amount)
    return safe_div(num, den)


def variance(db: Session, cohort: DeviceCohort, as_of: date, policy) -> dict:
    ver = approved_base(db, cohort)
    if not ver or not ver.assumptions:
        return {"rows": [], "forecast": None}
    a = assumptions_from_dict(ver.assumptions)
    plan = compute(a)
    act = cohort_actuals(db, cohort.id, as_of)
    u, h = act["units"], act["hours"]
    started = D(u["started"])
    progress = clamp(started / max(D(a.units), ONE))
    p2d = plan_to_date(db, cohort.id, as_of)
    fc = reforecast(db, cohort, as_of)
    series = daily_series(db, cohort.id, as_of)

    # utilisation over cell-days with activity
    sched = ZERO
    cells_days = {
        (o.robot_cell_id, o.operation_date)
        for o in db.scalars(
            select(DailyOperation).where(
                DailyOperation.cohort_id == cohort.id, DailyOperation.operation_date <= as_of
            )
        )
    }
    for cell_id, day in cells_days:
        c = db.scalar(
            select(CapacityCalendar).where(
                CapacityCalendar.robot_cell_id == cell_id, CapacityCalendar.calendar_date == day
            )
        )
        if c:
            sched += d(c.scheduled_hours) - d(c.maintenance_hours)
    util = safe_div(h["productive"], sched) if sched > 0 else None

    # Sell-through is only meaningful once a listing has had time to sell (>= 14 days old).
    mature = [
        x
        for x in db.scalars(select(ResaleListing).where(ResaleListing.cohort_id == cohort.id))
        if x.listing_date and (as_of - x.listing_date).days >= 14
    ]
    listed = sum(x.device_count for x in mature)
    mature_sold = sum(
        s.units_sold - s.units_returned
        for x in mature
        for s in db.scalars(select(SalesTransaction).where(SalesTransaction.resale_listing_id == x.id))
    )
    st_obs = safe_div(D(mature_sold), D(listed)) if listed else None
    gross = act["sales"]["gross"]
    sale_cost_pct = (
        safe_div(act["sales"]["marketplace_fees"] + act["sales"]["shipping"], gross) if gross > 0 else None
    )
    plan_cost_pct = (
        safe_div(plan["marketplace_fees"] + plan["merchant_shipping_cost"], plan["gross_sale_proceeds"])
        if plan["gross_sale_proceeds"] > 0
        else None
    )
    lag = collection_lag(db, cohort.id, as_of)
    tp_warn, tp_bad = policy.throughput_miss_warning / 2, policy.throughput_miss_warning
    co_warn, co_bad = policy.cost_overrun_warning / 2, policy.cost_overrun_warning

    rows = [
        _row(
            "volume",
            "Device volume received",
            "units",
            D(a.units),
            D(u["received"]),
            True,
            D("0.10"),
            D("0.20"),
        ),
        _row(
            "throughput",
            "Throughput (devices started, to date)",
            "units",
            p2d["units"] if p2d["total_units"] > 0 else None,
            started,
            True,
            tp_warn,
            tp_bad,
        ),
        _row(
            "robot_hours",
            "Robot hours (productive)",
            "hours",
            plan["robot_hours"] * progress,
            h["productive"],
            False,
            co_warn,
            co_bad,
        ),
        _row(
            "utilization", "Productive utilization", "%", a.planned_uptime, util, True, D("0.05"), D("0.10")
        ),
        _row(
            "fpy",
            "First-pass yield",
            "%",
            plan["first_pass_yield"],
            safe_div(D(u["first_pass"]), D(u["completed"])) if u["completed"] else None,
            True,
            D("0.03"),
            D("0.06"),
        ),
        _row(
            "exception_rate",
            "Exception rate",
            "%",
            plan["exception_rate"],
            safe_div(D(u["exceptions"]), started) if started else None,
            False,
            D("0.10"),
            policy.exception_alert_multiple - 1,
        ),
        _row(
            "repair_cost",
            "Repair parts cost",
            "$",
            plan["repair_cost"] * progress,
            act["costs_by_category"].get("repair_parts", ZERO),
            False,
            co_warn,
            co_bad,
        ),
        _row(
            "labor_cost",
            "Labour cost",
            "$",
            plan["manual_labor_cost"] * progress,
            act["costs_by_category"].get("technician_labor", ZERO),
            False,
            co_warn,
            co_bad,
        ),
        _row(
            "realized_price",
            "Realized resale price",
            "$",
            plan["avg_resale_price"] if plan["avg_resale_price"] > 0 else None,
            act["realized_price"],
            True,
            D("0.05"),
            D("0.10"),
        ),
        _row(
            "sell_through",
            "Sell-through (sold / listed)",
            "%",
            plan["sell_through"] if listed else None,
            st_obs,
            True,
            D("0.05"),
            D("0.10"),
        ),
        _row(
            "sales_cost",
            "Marketplace + shipping (% of gross)",
            "%",
            plan_cost_pct,
            sale_cost_pct,
            False,
            co_warn,
            co_bad,
        ),
        _row(
            "cm",
            "Contribution margin (reforecast vs budget)",
            "$",
            d(ver.expected_contribution_margin),
            D(str(fc["economics"]["contribution_margin"])) if fc else None,
            True,
            D("0.05"),
            D("0.15"),
        ),
        _row(
            "collection_lag",
            "Cash collection lag (days)",
            "days",
            D(a.payout_days),
            lag,
            False,
            D("0.15"),
            D("0.40"),
        ),
    ]
    tput = recent_throughput(series)
    remaining = max(D(a.units) - started, ZERO)
    forecast_completion = None
    if tput > 0:
        days_left, cur, n = int((remaining / tput).to_integral_value(rounding="ROUND_CEILING")), as_of, 0
        while n < days_left:
            cur += timedelta(days=1)
            if cur.weekday() < 5:
                n += 1
        forecast_completion = cur
    conv = cash_conversion(db, cohort.id, as_of)
    return {
        "rows": rows,
        "progress": progress,
        "reforecast": fc,
        "recent_daily_throughput": tput,
        "remaining_units": remaining,
        "forecast_completion": forecast_completion.isoformat() if forecast_completion else None,
        "target_completion": cohort.target_completion_date.isoformat()
        if cohort.target_completion_date
        else None,
        "cash_conversion": conv,
        "actual_utilization": util,
    }
