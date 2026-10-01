"""Daily operations: posting, derived metrics, plan-to-date."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import DomainError, Invalid, NotFound
from ..models import (
    CapacityCalendar,
    CohortAssignment,
    CostEntry,
    DailyOperation,
    DeviceCohort,
    ExceptionEvent,
    RobotCell,
)
from . import audit
from .capacity import refresh_available
from .economics import assumptions_from_dict, d, safe_div

D = Decimal
ZERO = D(0)
PROCESSING_STATES = {
    "approved",
    "scheduled",
    "in_intake",
    "in_processing",
    "exception_review",
    "quality_control",
}


def _validate(p: dict) -> None:
    for k in (
        "devices_received",
        "devices_started",
        "devices_completed",
        "first_pass_completions",
        "manual_exception_count",
        "quality_approved_count",
        "devices_under_repair",
        "rework_events",
    ):
        if p.get(k, 0) < 0:
            raise Invalid(f"{k} cannot be negative.")
    for k in (
        "productive_robot_hours",
        "downtime_hours",
        "technician_hours",
        "labor_cost",
        "repair_parts_cost",
    ):
        if d(p.get(k, 0)) < 0:
            raise Invalid(f"{k} cannot be negative.")
    if p.get("first_pass_completions", 0) > p.get("devices_completed", 0):
        raise Invalid("First-pass completions cannot exceed devices completed.")
    if p.get("manual_exception_count", 0) > p.get("devices_started", 0) + p.get("devices_under_repair", 0):
        raise Invalid("Manual exceptions cannot exceed devices started (+ under repair).")


def _replace_cost(db: Session, source_ref: str, **f) -> None:
    for old in db.scalars(select(CostEntry).where(CostEntry.source_ref == source_ref)):
        db.delete(old)
    if d(f["amount"]) > 0:
        db.add(CostEntry(source_type="daily_ops", source_ref=source_ref, **f))


def post_daily(db: Session, payload: dict, policy) -> DailyOperation:
    cohort = db.get(DeviceCohort, payload["cohort_id"])
    cell = db.get(RobotCell, payload["robot_cell_id"])
    if not cohort or not cell:
        raise NotFound("Cohort or robot cell")
    if cohort.status not in PROCESSING_STATES:
        raise DomainError(
            f"Cohort in status '{cohort.status}' cannot record operations (needs approval first).",
            code="bad_status",
        )
    if cohort.status == "approved":
        raise DomainError(
            "Cohort has no capacity assignment; schedule robot hours before logging operations.",
            code="not_scheduled",
        )
    _validate(payload)

    row = db.scalar(
        select(DailyOperation).where(
            DailyOperation.cohort_id == cohort.id,
            DailyOperation.robot_cell_id == cell.id,
            DailyOperation.operation_date == payload["operation_date"],
        )
    )
    if row is not None:
        audit.log(
            db,
            "daily_operation",
            row.id,
            "corrected",
            _snapshot(row),
            {
                k: str(v)
                for k, v in payload.items()
                if k not in ("cohort_id", "robot_cell_id", "operation_date")
            },
        )
    if row is None:
        row = DailyOperation(
            cohort_id=cohort.id, robot_cell_id=cell.id, operation_date=payload["operation_date"]
        )
        db.add(row)
    for k, v in payload.items():
        if k not in ("cohort_id", "robot_cell_id", "operation_date"):
            setattr(row, k, v)
    row.labor_cost = (
        d(payload.get("labor_cost")) or d(payload.get("technician_hours", 0)) * policy.labor_rate_per_hour
    )
    db.flush()

    day = row.operation_date
    ref = f"daily_ops:{row.id}"
    cal = db.scalar(
        select(CapacityCalendar).where(
            CapacityCalendar.robot_cell_id == cell.id, CapacityCalendar.calendar_date == day
        )
    )
    if cal:
        cal.downtime_hours = sum(
            (
                d(o.downtime_hours)
                for o in db.scalars(
                    select(DailyOperation).where(
                        DailyOperation.robot_cell_id == cell.id, DailyOperation.operation_date == day
                    )
                )
            ),
            ZERO,
        )
        refresh_available(cal)

    _replace_cost(
        db,
        f"{ref}:robot",
        cohort_id=cohort.id,
        robot_cell_id=cell.id,
        cost_date=day,
        cost_category="robot_operations",
        amount=(d(row.productive_robot_hours) + d(row.downtime_hours)) * policy.robot_cost_per_hour,
        quantity=d(row.productive_robot_hours) + d(row.downtime_hours),
        unit_cost=policy.robot_cost_per_hour,
        description="Robot-cell hours (productive + downtime)",
    )
    _replace_cost(
        db,
        f"{ref}:labor",
        cohort_id=cohort.id,
        robot_cell_id=cell.id,
        cost_date=day,
        cost_category="technician_labor",
        amount=row.labor_cost,
        quantity=d(row.technician_hours),
        description="Technician / exception labour",
    )
    _replace_cost(
        db,
        f"{ref}:repair",
        cohort_id=cohort.id,
        robot_cell_id=cell.id,
        cost_date=day,
        cost_category="repair_parts",
        amount=d(row.repair_parts_cost),
        description="Repair parts",
    )

    # Receiving a batch triggers the contracted acquisition + inbound cash outflow.
    if row.devices_received:
        a = _approved_assumptions(db, cohort)
        if a:
            if a.ownership != "partner_owned":
                _replace_cost(
                    db,
                    f"{ref}:acq",
                    cohort_id=cohort.id,
                    partner_id=cohort.partner_id,
                    cost_date=day,
                    cost_category="device_acquisition",
                    amount=a.acquisition_cost_per_unit * row.devices_received,
                    quantity=D(row.devices_received),
                    unit_cost=a.acquisition_cost_per_unit,
                    description="Device acquisition on receipt",
                )
            _replace_cost(
                db,
                f"{ref}:inbound",
                cohort_id=cohort.id,
                partner_id=cohort.partner_id,
                cost_date=day,
                cost_category="inbound_logistics",
                amount=a.inbound_logistics_per_unit * row.devices_received,
                quantity=D(row.devices_received),
                description="Inbound logistics on receipt",
            )

    cohort.actual_device_count = sum(
        o.devices_received
        for o in db.scalars(select(DailyOperation).where(DailyOperation.cohort_id == cohort.id))
    )
    if cohort.status in ("scheduled", "approved"):
        cohort.status = "in_intake" if row.devices_started == 0 else "in_processing"
    elif cohort.status == "in_intake" and row.devices_started:
        cohort.status = "in_processing"
    db.flush()
    return row


def _approved_assumptions(db: Session, cohort: DeviceCohort):
    from ..models import CohortBudgetVersion

    if not cohort.approved_budget_version_id:
        return None
    v = db.get(CohortBudgetVersion, cohort.approved_budget_version_id)
    return assumptions_from_dict(v.assumptions) if v and v.assumptions else None


def daily_series(db: Session, cohort_id: str, as_of: date) -> list[dict]:
    ops = list(
        db.scalars(
            select(DailyOperation)
            .where(DailyOperation.cohort_id == cohort_id, DailyOperation.operation_date <= as_of)
            .order_by(DailyOperation.operation_date)
        )
    )
    by_day: dict[date, dict] = {}
    for o in ops:
        r = by_day.setdefault(
            o.operation_date,
            {
                "date": o.operation_date,
                "started": 0,
                "completed": 0,
                "first_pass": 0,
                "exceptions": 0,
                "productive_hours": ZERO,
                "downtime_hours": ZERO,
            },
        )
        r["started"] += o.devices_started
        r["completed"] += o.devices_completed
        r["first_pass"] += o.first_pass_completions
        r["exceptions"] += o.manual_exception_count
        r["productive_hours"] += d(o.productive_robot_hours)
        r["downtime_hours"] += d(o.downtime_hours)
    out, cum_s, cum_e = [], 0, 0
    for day in sorted(by_day):
        r = by_day[day]
        cum_s += r["started"]
        cum_e += r["exceptions"]
        r["exception_rate"] = safe_div(D(r["exceptions"]), D(r["started"]), ZERO)
        r["cum_exception_rate"] = safe_div(D(cum_e), D(cum_s), ZERO)
        r["cum_started"] = cum_s
        out.append(r)
    return out


def plan_to_date(db: Session, cohort_id: str, as_of: date) -> dict:
    """Planned units and robot-hours through `as_of`, from the approved assignment schedule."""
    units = hours = ZERO
    total_units = total_hours = ZERO
    for a in db.scalars(
        select(CohortAssignment).where(
            CohortAssignment.cohort_id == cohort_id, CohortAssignment.status != "cancelled"
        )
    ):
        plan = a.daily_plan or {}
        tot = sum((d(v) for v in plan.values()), ZERO)
        done = sum((d(v) for k, v in plan.items() if date.fromisoformat(k) <= as_of), ZERO)
        total_hours += tot
        total_units += D(a.planned_units)
        hours += done
        if tot > 0:
            units += D(a.planned_units) * done / tot
    return {"units": units, "hours": hours, "total_units": total_units, "total_hours": total_hours}


def recent_throughput(series: list[dict], days: int = 5) -> Decimal:
    tail = series[-days:]
    return D(sum(r["started"] for r in tail)) / D(len(tail)) if tail else ZERO


def cell_utilization(db: Session, cell_id: str, start: date, end: date) -> dict:
    cal = {
        r.calendar_date: r
        for r in db.scalars(
            select(CapacityCalendar).where(
                CapacityCalendar.robot_cell_id == cell_id, CapacityCalendar.calendar_date.between(start, end)
            )
        )
    }
    ops: dict[date, dict] = {}
    for o in db.scalars(
        select(DailyOperation).where(
            DailyOperation.robot_cell_id == cell_id, DailyOperation.operation_date.between(start, end)
        )
    ):
        r = ops.setdefault(
            o.operation_date,
            {
                "productive": ZERO,
                "downtime": ZERO,
                "started": 0,
                "completed": 0,
                "first_pass": 0,
                "exceptions": 0,
                "reasons": [],
            },
        )
        r["productive"] += d(o.productive_robot_hours)
        r["downtime"] += d(o.downtime_hours)
        r["started"] += o.devices_started
        r["completed"] += o.devices_completed
        r["first_pass"] += o.first_pass_completions
        r["exceptions"] += o.manual_exception_count
        if o.downtime_reason and d(o.downtime_hours) > 0:
            r["reasons"].append(o.downtime_reason)
    days = []
    for day in sorted(ops):
        c = cal.get(day)
        avail = (d(c.scheduled_hours) - d(c.maintenance_hours)) if c else ZERO
        days.append(
            {
                "date": day,
                "utilization": safe_div(ops[day]["productive"], avail, ZERO) if avail > 0 else ZERO,
                **ops[day],
            }
        )
    return {"days": days}


def log_exception_event(db: Session, p: dict) -> ExceptionEvent:
    if not db.get(DeviceCohort, p["cohort_id"]):
        raise NotFound("Cohort")
    ev = ExceptionEvent(**p)
    db.add(ev)
    db.flush()
    return ev


def _snapshot(row: DailyOperation) -> dict:
    cols = (
        "devices_received",
        "devices_started",
        "devices_completed",
        "first_pass_completions",
        "manual_exception_count",
        "quality_approved_count",
        "productive_robot_hours",
        "downtime_hours",
        "technician_hours",
        "labor_cost",
        "repair_parts_cost",
    )
    return {c: str(getattr(row, c)) for c in cols}
