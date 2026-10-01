from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import require
from ..config import today
from ..db import get_db
from ..errors import Invalid, NotFound
from ..models import (
    CohortAssignment,
    CohortBudgetVersion,
    DailyOperation,
    DeviceCohort,
    ExceptionEvent,
    RobotCell,
    utcnow,
)
from ..services import alerts as alert_svc
from ..services import capacity, operations
from ..services.analytics import robot_performance
from ..services.cohort_metrics import expected_exceptions_by_cohort
from ..services.economics import d
from .util import pol, row, writable_cohort

router = APIRouter(prefix="/api", tags=["capacity-operations"])


@router.get("/capacity/calendar")
def calendar(start: date | None = None, end: date | None = None, db: Session = Depends(get_db)):
    start = start or today() - timedelta(days=7)
    end = end or today() + timedelta(days=28)
    cells = list(db.scalars(select(RobotCell).order_by(RobotCell.cell_name)))
    rows = capacity.calendar_rows(db, start, end)
    asg = list(
        db.scalars(
            select(CohortAssignment).where(
                CohortAssignment.status != "cancelled",
                CohortAssignment.end_date >= start,
                CohortAssignment.start_date <= end,
            )
        )
    )
    codes = {c.id: c.cohort_code for c in db.scalars(select(DeviceCohort))}
    by_cell: dict[str, list] = {c.id: [] for c in cells}
    for r in rows:
        by_cell[r.robot_cell_id].append(
            {
                "date": r.calendar_date.isoformat(),
                "scheduled": r.scheduled_hours,
                "maintenance": r.maintenance_hours,
                "downtime": r.downtime_hours,
                "assigned": r.assigned_hours,
                "available": r.available_hours,
                "free": max(d(r.available_hours) - d(r.assigned_hours), 0),
            }
        )
    # per-day per-cohort allocation for the stacked view
    alloc: dict[str, dict[str, dict[str, float]]] = {}
    for a in asg:
        for iso, h in (a.daily_plan or {}).items():
            alloc.setdefault(a.robot_cell_id, {}).setdefault(iso, {})[codes[a.cohort_id]] = h
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "today": today().isoformat(),
        "cells": [
            {
                **row(c),
                "days": by_cell[c.id],
                "allocation": alloc.get(c.id, {}),
                "free_hours": sum(
                    float(x["free"]) for x in by_cell[c.id] if x["date"] >= today().isoformat()
                ),
            }
            for c in cells
        ],
        "assignments": [
            {
                **row(a, exclude=("daily_plan",)),
                "cohort_code": codes[a.cohort_id],
                "cell_name": a.robot_cell.cell_name,
            }
            for a in asg
        ],
    }


@router.post("/capacity/calendar", status_code=201, dependencies=[Depends(require("ops_write"))])
def upsert_calendar(body: schemas.CalendarIn, db: Session = Depends(get_db)):
    cell = capacity.cell_or_404(db, body.robot_cell_id)
    if body.end_date < body.start_date:
        raise Invalid("End date precedes start date.")
    if body.maintenance_hours > body.scheduled_hours:
        raise Invalid("Maintenance hours cannot exceed scheduled hours.")
    capacity.ensure_calendar(
        db,
        cell,
        body.start_date,
        body.end_date,
        body.scheduled_hours,
        (0, 1, 2, 3, 4) if body.weekdays_only else tuple(range(7)),
    )
    for r in capacity.calendar_rows(db, body.start_date, body.end_date, [cell.id]):
        r.scheduled_hours, r.maintenance_hours = body.scheduled_hours, body.maintenance_hours
        capacity.refresh_available(r)
    db.commit()
    return {"status": "ok"}


@router.get("/capacity/queue")
def queue(db: Session = Depends(get_db)):
    """Cohorts competing for robot hours, ranked by priority score (CM per robot hour dominant)."""
    out = []
    exc = expected_exceptions_by_cohort(db)
    for c in db.scalars(
        select(DeviceCohort).where(DeviceCohort.status.in_(("under_review", "approved", "scheduled")))
    ):
        v = (
            db.get(CohortBudgetVersion, c.approved_budget_version_id)
            if c.approved_budget_version_id
            else db.scalar(
                select(CohortBudgetVersion)
                .where(CohortBudgetVersion.cohort_id == c.id, CohortBudgetVersion.scenario_type == "base")
                .order_by(CohortBudgetVersion.version_number.desc())
            )
        )
        if not v:
            continue
        ec = v.economics or {}
        assigned = sum(
            (
                d(a.planned_hours)
                for a in db.scalars(
                    select(CohortAssignment).where(
                        CohortAssignment.cohort_id == c.id, CohortAssignment.status != "cancelled"
                    )
                )
            ),
            d(0),
        )
        out.append(
            {
                "id": c.id,
                "code": c.cohort_code,
                "name": c.cohort_name,
                "status": c.status,
                "priority_score": c.priority_score,
                "scheduled_hours_needed": ec.get("scheduled_robot_hours"),
                "assigned_hours": assigned,
                "cm_per_robot_hour": ec.get("cm_per_robot_hour"),
                "cm_pct": ec.get("cm_pct"),
                "exception_rate": ec.get("exception_rate"),
                "cash_required": ec.get("cash_required"),
                "target_completion_date": c.target_completion_date,
                "intake_date": c.intake_date,
                "expected_exceptions": exc.get(c.id),
                "review_required": (ec.get("exception_units", 0) or 0) > pol(db).review_capacity_per_day * 5,
                "sla_feasible": capacity.sla_feasibility(
                    db,
                    d(ec.get("scheduled_robot_hours", 0)) - assigned,
                    max(c.intake_date or today(), today()),
                    c.target_completion_date,
                )["feasible"]
                if c.target_completion_date
                else None,
            }
        )
    return sorted(out, key=lambda r: -(float(r["priority_score"]) if r["priority_score"] is not None else 0))


@router.get("/robot-cells/{cell_id}/performance")
def cell_perf(cell_id: str, db: Session = Depends(get_db)):
    capacity.cell_or_404(db, cell_id)
    return next(r for r in robot_performance(db, today()) if r["id"] == cell_id)


@router.get("/analytics/robot-performance")
def robots(db: Session = Depends(get_db)):
    return robot_performance(db, today())


@router.post("/operations/daily", status_code=201, dependencies=[Depends(require("ops_write"))])
def post_daily(body: schemas.DailyOpIn, db: Session = Depends(get_db)):
    writable_cohort(db, body.cohort_id)
    policy = pol(db)
    r = operations.post_daily(db, body.model_dump(), policy)
    alert_svc.evaluate(db, today(), policy)
    db.commit()
    return row(r)


@router.get("/operations/daily")
def list_daily(
    cohort_id: str | None = None,
    robot_cell_id: str | None = None,
    limit: int = 60,
    db: Session = Depends(get_db),
):
    q = select(DailyOperation).order_by(DailyOperation.operation_date.desc()).limit(limit)
    if cohort_id:
        q = q.where(DailyOperation.cohort_id == cohort_id)
    if robot_cell_id:
        q = q.where(DailyOperation.robot_cell_id == robot_cell_id)
    return [row(o) for o in db.scalars(q)]


@router.post("/exceptions", status_code=201, dependencies=[Depends(require("ops_write"))])
def create_exception(body: schemas.ExceptionIn, db: Session = Depends(get_db)):
    writable_cohort(db, body.cohort_id)
    ev = operations.log_exception_event(db, body.model_dump())
    alert_svc.evaluate(db, today(), pol(db))
    db.commit()
    return row(ev)


@router.get("/exceptions")
def list_exceptions(cohort_id: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    q = select(ExceptionEvent).order_by(ExceptionEvent.opened_at.desc()).limit(200)
    if cohort_id:
        q = q.where(ExceptionEvent.cohort_id == cohort_id)
    if status:
        q = q.where(ExceptionEvent.status == status)
    return [row(e) for e in db.scalars(q)]


@router.patch("/exceptions/{exception_id}", dependencies=[Depends(require("ops_write"))])
def patch_exception(exception_id: str, body: schemas.ExceptionPatch, db: Session = Depends(get_db)):

    e = db.get(ExceptionEvent, exception_id)
    if not e:
        raise NotFound("Exception")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(e, k, v)
    if data.get("status") == "resolved":
        e.resolved_at = utcnow()
    alert_svc.evaluate(db, today(), pol(db))
    db.commit()
    return row(e)
