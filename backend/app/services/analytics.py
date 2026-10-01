"""Executive dashboard, cohort/robot/model analytics."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Alert,
    CapacityCalendar,
    CashReceipt,
    CohortBudgetVersion,
    DailyOperation,
    DeviceCohort,
    DeviceModelProfile,
    ExceptionEvent,
    Invoice,
    Partner,
    ResaleListing,
    RobotCell,
    SalesTransaction,
)
from ..policy import Policy
from .cohort_metrics import ACTIVE, COMMITTED
from .economics import d, safe_div
from .finance import cohort_actuals, collections_aging, inventory_aging
from .operations import cell_utilization
from .partners import scorecard
from .variance import reforecast

D = Decimal
ZERO = D(0)


def cohort_rows(db: Session, as_of: date, policy: Policy, cohort_ids: set[str] | None = None) -> list[dict]:
    rows = []
    for c in db.scalars(select(DeviceCohort).order_by(DeviceCohort.cohort_code)):
        if cohort_ids is not None and c.id not in cohort_ids:
            continue
        v = (
            db.get(CohortBudgetVersion, c.approved_budget_version_id)
            if c.approved_budget_version_id
            else None
        )
        act = cohort_actuals(db, c.id, as_of) if c.status in ACTIVE + ("closed",) else None
        fc = reforecast(db, c, as_of) if c.status in ACTIVE + ("closed",) else None
        rows.append(
            {
                "id": c.id,
                "code": c.cohort_code,
                "name": c.cohort_name,
                "status": c.status,
                "partner": c.partner.name,
                "partner_id": c.partner_id,
                "partner_type": c.partner.partner_type,
                "devices": c.expected_device_count,
                "received": c.actual_device_count,
                "priority_score": c.priority_score,
                "plan_cm": v.expected_contribution_margin if v else None,
                "plan_cm_pct": safe_div(d(v.expected_contribution_margin), d(v.expected_revenue), None)
                if v
                else None,
                "forecast_cm": D(str(fc["economics"]["contribution_margin"])) if fc else None,
                "forecast_cm_pct": D(str(fc["economics"]["cm_pct"]))
                if fc and fc["economics"]["cm_pct"] is not None
                else None,
                "actual_cm": act["contribution_margin"] if act else None,
                "cash_exposure": act["cash_exposure"] if act else None,
                "inventory_value": act["inventory_value"] if act else None,
                "cm_per_robot_hour": D(str(fc["economics"]["cm_per_robot_hour"])) if fc else None,
                "below_floor": fc["below_floor"] if fc else False,
            }
        )
    return rows


def _window(scope, as_of: date, default_start: date | None, floor: date | None) -> tuple[date | None, date]:
    """Window for one metric family. No date filter -> the metric's own documented default; otherwise the selected range
    (open start -> `floor`; end never later than `as_of`, so future calendar days cannot dilute utilisation)."""
    if not scope.date_explicit:
        return default_start, as_of
    end = min(scope.end, as_of) if scope.end else as_of
    return (scope.start or floor), end


def scope_windows(db: Session, scope, as_of: date) -> dict[str, tuple[date | None, date]]:
    """The four documented date windows (see dashboard_semantics). Shared by the dashboard and every drill-down."""
    first_cal = db.scalar(select(func.min(CapacityCalendar.calendar_date)))
    return {
        "operations": _window(scope, as_of, as_of - timedelta(days=13), first_cal),
        "exceptions": _window(scope, as_of, None, None),
        "cash_collected": _window(scope, as_of, as_of.replace(day=1), None),
        "realized_margin": _window(scope, as_of, None, None),
    }


def collection_in_scope(it: dict, scope) -> bool:
    """Phase-2 rule: cohort/channel/issue-date decide SCOPE; the outstanding balance itself is always current."""
    if scope.channel and it["channel"] != scope.channel:
        return False
    if not ((it["cohort_id"] in scope.cohort_ids) or (it["cohort_id"] is None and not scope.cohort_filtered)):
        return False
    if scope.date_explicit and it["issued"]:
        iss = date.fromisoformat(it["issued"])
        if (scope.start and iss < scope.start) or (scope.end and iss > scope.end):
            return False
    return True


def _alert_in_scope(a, scope) -> bool:
    if not scope.cohort_filtered:
        return True
    if a.cohort_id:
        return a.cohort_id in scope.cohort_ids
    return (
        a.entity_type == "robot_cell" and scope.cell_id == a.entity_id
    )  # portfolio-level alerts hide under cohort filters


def executive(db: Session, as_of: date, policy: Policy, scope=None) -> dict:
    """Executive dashboard. `scope` (filters.Scope) narrows every aggregate server-side by INTERSECTION; with no scope/filters
    this is exactly the original global dashboard. Per-metric date semantics are listed in dashboard_semantics.SEMANTICS."""
    from ..filters import Scope
    from ..models import CohortAssignment
    from .dashboard_semantics import SEMANTICS

    scope = scope or Scope(cohort_ids={c.id for c in db.scalars(select(DeviceCohort))})
    ids = scope.cohort_ids
    cohorts = [c for c in db.scalars(select(DeviceCohort)) if c.id in ids]
    active = [c for c in cohorts if c.status in ACTIVE]
    live = [c for c in cohorts if c.status in ACTIVE or c.status == "closed"]

    w = scope_windows(db, scope, as_of)
    (ops_start, ops_end), (exc_start, exc_end) = w["operations"], w["exceptions"]
    (cash_start, cash_end), (rev_start, rev_end) = w["cash_collected"], w["realized_margin"]

    # ---- cohort-level plan / forecast / realized ------------------------------------------------------------
    plan_cm = fc_cm = act_cm = inventory = ZERO
    realized = {"gross_sale_proceeds": ZERO, "net_recognized_revenue": ZERO, "direct_variable_cost": ZERO}
    for c in live:
        a = cohort_actuals(db, c.id, as_of)
        fc = reforecast(db, c, as_of)
        v = db.get(CohortBudgetVersion, c.approved_budget_version_id)
        plan_cm += d(v.expected_contribution_margin)
        fc_cm += D(str(fc["economics"]["contribution_margin"])) if fc else ZERO
        inventory += a["inventory_value"]
        w = (
            a
            if (rev_start is None and not scope.channel)
            else cohort_actuals(db, c.id, rev_end, start=rev_start, channel=scope.channel)
        )
        act_cm += w["contribution_margin"]
        realized["gross_sale_proceeds"] += w["gross_sale_proceeds"]
        realized["net_recognized_revenue"] += w["net_recognized_revenue"]
        realized["direct_variable_cost"] += w["cost_total"]
    inv_age = inventory_aging(
        db, as_of, policy.target_days_to_sale, ids if scope.cohort_filtered else None, scope.channel
    )
    if scope.channel:  # inventory by channel is only defined from that channel's listings
        inventory = inv_age["total_value"]

    # ---- cash collected (receipt_date) ------------------------------------------------------------------------
    sales_by_id = {x.id: x for x in db.scalars(select(SalesTransaction))}
    inv_by_id = {x.id: x for x in db.scalars(select(Invoice))}
    channel_of = {ls.id: ls.listing_channel for ls in db.scalars(select(ResaleListing))}
    cash = ZERO
    for r in db.scalars(select(CashReceipt)):
        if cash_start and r.receipt_date < cash_start or r.receipt_date > cash_end:
            continue
        if r.sales_transaction_id:
            tx = sales_by_id.get(r.sales_transaction_id)
            cid, ch = tx.cohort_id, channel_of.get(tx.resale_listing_id)
        else:
            inv = inv_by_id.get(r.invoice_id)
            cid, ch = inv.cohort_id, None
        if scope.channel and ch != scope.channel:
            continue
        if (cid in ids) or (cid is None and not scope.cohort_filtered):
            cash += d(r.amount)

    # ---- operations (operation_date): utilisation, throughput, exceptions ---------------------------------------
    ops_all = [
        o
        for o in db.scalars(select(DailyOperation))
        if o.cohort_id in ids and (not scope.cell_id or o.robot_cell_id == scope.cell_id)
    ]

    def ops_in(lo, hi):
        return [o for o in ops_all if (lo is None or o.operation_date >= lo) and o.operation_date <= hi]

    win_ops = ops_in(ops_start, ops_end)
    cells, prod, avail = [], ZERO, ZERO
    for cell in db.scalars(select(RobotCell)):
        if scope.cell_id and cell.id != scope.cell_id:
            continue
        cal = [r for r in db.scalars(select(CapacityCalendar).where(CapacityCalendar.robot_cell_id == cell.id))
               if (ops_start is None or r.calendar_date >= ops_start) and r.calendar_date <= ops_end]  # fmt: skip
        sched = sum((d(r.scheduled_hours) - d(r.maintenance_hours) for r in cal), ZERO)
        mine = [o for o in win_ops if o.robot_cell_id == cell.id]
        p = sum((d(o.productive_robot_hours) for o in mine), ZERO)
        dn = sum((d(o.downtime_hours) for o in mine), ZERO)
        if cell.status == "available":
            prod += p
            avail += sched
        cells.append({"id": cell.id, "name": cell.cell_name, "status": cell.status, "utilization": safe_div(p, sched, ZERO) if sched > 0 else ZERO,
                      "productive_hours": p, "downtime_hours": dn, "scheduled_hours": sched})  # fmt: skip

    start_day = max(ops_start or ops_end, ops_end - timedelta(days=365))
    throughput = []
    plan_by_day: dict[str, Decimal] = {}
    for a_ in db.scalars(select(CohortAssignment).where(CohortAssignment.status != "cancelled")):
        if a_.cohort_id not in ids or (scope.cell_id and a_.robot_cell_id != scope.cell_id):
            continue
        tot = sum((d(v) for v in (a_.daily_plan or {}).values()), ZERO)
        if tot <= 0:
            continue
        for k, v in (a_.daily_plan or {}).items():
            plan_by_day[k] = plan_by_day.get(k, ZERO) + D(a_.planned_units) * d(v) / tot
    started_by_day: dict[date, int] = {}
    for o in win_ops:
        started_by_day[o.operation_date] = started_by_day.get(o.operation_date, 0) + o.devices_started
    day = start_day
    while day <= ops_end:
        if day.weekday() < 5:  # non-operating days would read as false outages
            throughput.append({"date": day.isoformat(), "actual": started_by_day.get(day, 0),
                               "plan": float(round(plan_by_day.get(day.isoformat(), ZERO), 1))})  # fmt: skip
        day += timedelta(days=1)
    actual_w = sum(r["actual"] for r in throughput)
    plan_w = sum(r["plan"] for r in throughput)

    live_ids = {c.id for c in live}
    exc_rows = [o for o in ops_in(exc_start, exc_end) if o.cohort_id in live_ids]
    exc_num, started_tot = (
        sum(o.manual_exception_count for o in exc_rows),
        sum(o.devices_started for o in exc_rows),
    )

    # ---- collections (issue date; balances are CURRENT) -----------------------------------------------------------
    items = [it for it in collections_aging(db, as_of)["items"] if collection_in_scope(it, scope)]
    coll_buckets = {b_: ZERO for b_ in ("current", "1-30", "31-60", "60+")}
    for it in items:
        coll_buckets[it["bucket"]] += it["remaining"]
    overdue = sum((it["remaining"] for it in items if it["days_overdue"] > 0), ZERO)

    # ---- partners, alerts ----------------------------------------------------------------------------------------
    scoped_partner_ids = {c.partner_id for c in cohorts}
    partners = [
        scorecard(db, p, as_of, ids if scope.cohort_filtered else None)
        for p in db.scalars(select(Partner))
        if not scope.cohort_filtered or p.id in scoped_partner_ids
    ]
    scored = sorted(
        [p for p in partners if p["score"] is not None], key=lambda p: -p["metrics"]["forecast_cm_pct"]
    )
    alerts = [a for a in db.scalars(select(Alert).where(Alert.status.in_(("open", "escalated"))).order_by(Alert.created_at.desc()))
              if _alert_in_scope(a, scope)]  # fmt: skip
    sev_rank = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: sev_rank.get(a.severity, 3))
    code = {c.id: c.cohort_code for c in db.scalars(select(DeviceCohort))}

    def fmt(x):
        return x.isoformat() if x else None

    return {
        "filters": {
            "count": scope.active_count,
            "applied": scope.applied,
            "requested": scope.requested,
            "resolved_range": {"range": scope.range, "start": fmt(scope.start), "end": fmt(scope.end)},
            "windows": {
                "operations": {"start": fmt(ops_start), "end": fmt(ops_end)},
                "exceptions": {"start": fmt(exc_start), "end": fmt(exc_end)},
                "cash_collected": {"start": fmt(cash_start), "end": fmt(cash_end)},
                "realized_margin": {"start": fmt(rev_start), "end": fmt(rev_end)},
            },
            "cohorts_in_scope": len(cohorts),
            "realized_basis": "channel_recovery" if scope.channel else "cohort",
        },
        "semantics": SEMANTICS,
        "empty": len(cohorts) == 0,
        "active_cohorts": len(active),
        "committed_cohorts": len([c for c in cohorts if c.status in COMMITTED]),
        "pending_review": len([c for c in cohorts if c.status == "under_review"]),
        "plan_cm": plan_cm,
        "forecast_cm": fc_cm,
        "actual_cm_to_date": act_cm,
        "realized_detail": realized,
        "cash_tied_up_inventory": inventory,
        "cash_collected_this_month": cash,  # legacy key; the window is filters.windows.cash_collected
        "cash_collected": cash,
        "utilization": safe_div(prod, avail, ZERO) if avail > 0 else ZERO,
        "cells": cells,
        "throughput_vs_plan": throughput,
        "throughput_actual_14d": actual_w,  # legacy key names; window is filters.windows.operations
        "throughput_plan_14d": plan_w,
        "throughput_actual": actual_w,
        "throughput_plan": plan_w,
        "exception_rate": safe_div(D(exc_num), D(started_tot), ZERO),
        "exception_detail": {"exceptions": exc_num, "devices_started": started_tot},
        "aged_inventory_value": inv_age["aged_value"],
        "inventory_aging": inv_age["buckets"],
        "overdue_collections": overdue,
        "collections_buckets": coll_buckets,
        "top_partners": scored[:3],
        "bottom_partners": list(reversed(scored[-2:])) if len(scored) > 1 else [],
        "partners": partners,
        "critical_alerts": [
            {
                "id": a.id,
                "severity": a.severity,
                "title": a.title,
                "recommended_action": a.recommended_action,
                "cohort_id": a.cohort_id,
                "cohort_code": code.get(a.cohort_id or ""),
                "alert_type": a.alert_type,
            }
            for a in alerts[:8]
        ],  # fmt: skip
        "alert_counts": {
            "critical": len([a for a in alerts if a.severity == "critical"]),
            "warning": len([a for a in alerts if a.severity == "warning"]),
        },
        "cohorts": cohort_rows(db, as_of, policy, ids if scope.cohort_filtered else None),
    }


def robot_performance(db: Session, as_of: date, days: int = 21) -> list[dict]:
    out = []
    start = as_of - timedelta(days=days)
    for cell in db.scalars(select(RobotCell)):
        ut = cell_utilization(db, cell.id, start, as_of)["days"]
        prod = sum((x["productive"] for x in ut), ZERO)
        down = sum((x["downtime"] for x in ut), ZERO)
        started = sum(x["started"] for x in ut)
        comp = sum(x["completed"] for x in ut)
        fp = sum(x["first_pass"] for x in ut)
        exc = sum(x["exceptions"] for x in ut)
        reasons: dict[str, Decimal] = {}
        for o in db.scalars(
            select(DailyOperation).where(
                DailyOperation.robot_cell_id == cell.id, DailyOperation.operation_date.between(start, as_of)
            )
        ):
            if o.downtime_reason and d(o.downtime_hours) > 0:
                reasons[o.downtime_reason] = reasons.get(o.downtime_reason, ZERO) + d(o.downtime_hours)
        out.append(
            {
                "id": cell.id,
                "name": cell.cell_name,
                "status": cell.status,
                "location": cell.location,
                "avg_utilization": safe_div(sum((x["utilization"] for x in ut), ZERO), D(len(ut)), ZERO)
                if ut
                else ZERO,
                "productive_hours": prod,
                "downtime_hours": down,
                "uptime": safe_div(prod, prod + down, ZERO) if prod + down > 0 else None,
                "devices_started": started,
                "first_pass_yield": safe_div(D(fp), D(comp), ZERO) if comp else None,
                "exception_rate": safe_div(D(exc), D(started), ZERO) if started else None,
                "downtime_reasons": [
                    {"reason": k, "hours": v} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])
                ],
                "daily": [
                    {
                        "date": x["date"].isoformat(),
                        "utilization": x["utilization"],
                        "downtime": x["downtime"],
                        "started": x["started"],
                    }
                    for x in ut
                ],
            }
        )
    return out


def model_burden(db: Session) -> list[dict]:
    """Which device models drive manual rework -- expected vs logged exception events."""
    rows = []
    for m in db.scalars(select(DeviceModelProfile)):
        evs = list(db.scalars(select(ExceptionEvent).where(ExceptionEvent.device_model_profile_id == m.id)))
        cost = sum((d(e.actual_resolution_cost or e.estimated_resolution_cost) for e in evs), ZERO)
        types: dict[str, int] = {}
        for e in evs:
            types[e.exception_type] = types.get(e.exception_type, 0) + 1
        rows.append(
            {
                "id": m.id,
                "model": f"{m.manufacturer} {m.model_name}",
                "expected_exception_rate": m.expected_exception_rate_pct,
                "expected_repair_cost": m.expected_repair_cost,
                "logged_exceptions": len(evs),
                "logged_cost": cost,
                "top_type": max(types, key=types.get) if types else None,
            }
        )
    return sorted(rows, key=lambda r: (-r["logged_exceptions"], -float(r["expected_exception_rate"] or 0)))
