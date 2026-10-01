"""Capacity planning: calendar, overbooking prevention, SLA feasibility, review capacity, ranking."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..errors import DomainError, Invalid, NotFound
from ..models import CapacityCalendar, CohortAssignment, DeviceCohort, RobotCell
from .economics import d

D = Decimal
BLOCKED_CELL_STATES = {"maintenance", "offline", "blocked"}
EPS = D("0.005")


def ensure_calendar(
    db: Session,
    cell: RobotCell,
    start: date,
    end: date,
    hours: Decimal = D(10),
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4),
) -> int:
    existing = {
        r.calendar_date
        for r in db.scalars(
            select(CapacityCalendar).where(
                CapacityCalendar.robot_cell_id == cell.id, CapacityCalendar.calendar_date.between(start, end)
            )
        )
    }
    n, day = 0, start
    while day <= end:
        if day.weekday() in weekdays and day not in existing:
            db.add(
                CapacityCalendar(
                    robot_cell_id=cell.id,
                    calendar_date=day,
                    scheduled_hours=hours,
                    maintenance_hours=D(0),
                    downtime_hours=D(0),
                    assigned_hours=D(0),
                    available_hours=hours,
                )
            )
            n += 1
        day += timedelta(days=1)
    db.flush()
    return n


def refresh_available(row: CapacityCalendar) -> None:
    row.available_hours = max(d(row.scheduled_hours) - d(row.maintenance_hours) - d(row.downtime_hours), D(0))


def calendar_rows(
    db: Session, start: date, end: date, cell_ids: list[str] | None = None, lock: bool = False
) -> list[CapacityCalendar]:
    q = select(CapacityCalendar).where(CapacityCalendar.calendar_date.between(start, end))
    if cell_ids:
        q = q.where(CapacityCalendar.robot_cell_id.in_(cell_ids))
    if (
        lock
    ):  # row locks on PostgreSQL: two concurrent requests cannot both claim the same hours (no-op on SQLite)
        q = q.with_for_update()
    return list(db.scalars(q.order_by(CapacityCalendar.calendar_date)))


def free_hours(db: Session, start: date, end: date) -> Decimal:
    """Unassigned scheduled hours on cells that can accept work."""
    cells = {c.id for c in db.scalars(select(RobotCell)) if c.status not in BLOCKED_CELL_STATES}
    return sum(
        (
            max(d(r.available_hours) - d(r.assigned_hours), D(0))
            for r in calendar_rows(db, start, end, list(cells))
        ),
        D(0),
    )


def _distribute(rows: list[CapacityCalendar], hours: Decimal) -> tuple[dict[date, Decimal], Decimal]:
    """Spread `hours` (already rounded to 0.01) evenly over days with room. Daily figures are 0.01-rounded and the
    rounding residue is pushed into the last days, so sum(daily) == hours exactly (no drift on long assignments)."""
    cent = D("0.01")
    room = {r.calendar_date: max(d(r.available_hours) - d(r.assigned_hours), D(0)) for r in rows}
    alloc = {k: D(0) for k in room}
    remaining = hours
    for _ in range(len(room) + 1):
        open_days = [k for k, v in room.items() if v - alloc[k] > EPS]
        if remaining <= EPS or not open_days:
            break
        share = remaining / len(open_days)
        for k in open_days:
            take = min(share, room[k] - alloc[k])
            alloc[k] += take
            remaining -= take
    alloc = {k: v for k, v in alloc.items() if v > 0}
    if remaining > EPS:
        return {k: v.quantize(cent) for k, v in alloc.items()}, remaining
    days = sorted(alloc)
    rounded = {k: alloc[k].quantize(cent) for k in days}
    residue = hours - sum(rounded.values())
    for k in reversed(days):  # absorb the residue where there is room
        if residue == 0:
            break
        adj = (
            min(max(residue, -rounded[k]), room[k] - rounded[k]) if residue > 0 else max(residue, -rounded[k])
        )
        rounded[k] += adj
        residue -= adj
    return {k: v for k, v in rounded.items() if v > 0}, max(residue, D(0))


def schedule_assignment(
    db: Session,
    cohort: DeviceCohort,
    cell: RobotCell,
    start: date,
    end: date,
    planned_hours: Decimal,
    planned_units: int,
) -> CohortAssignment:
    if end < start:
        raise Invalid("Assignment end date precedes start date.")
    if planned_hours <= 0:
        raise Invalid("Planned hours must be positive.")
    if cohort.status not in ("approved", "scheduled", "in_intake", "in_processing", "exception_review"):
        raise DomainError(
            "Cohort must be approved before robot capacity can be assigned.", code="not_approved"
        )
    if cell.status in BLOCKED_CELL_STATES:
        raise DomainError(
            f"Robot cell {cell.cell_name} is {cell.status} and cannot receive assignments.",
            code="cell_blocked",
        )

    planned_hours = d(planned_hours).quantize(D("0.01"))
    rows = calendar_rows(db, start, end, [cell.id], lock=True)
    alloc, shortfall = _distribute(rows, planned_hours)
    if shortfall > EPS:
        free = sum((max(d(r.available_hours) - d(r.assigned_hours), D(0)) for r in rows), D(0))
        raise DomainError(
            f"Overbooked: {cell.cell_name} has {float(free):.1f}h free between {start} and {end}, "
            f"but {float(planned_hours):.1f}h were requested ({float(shortfall):.1f}h short).",
            code="overbooked",
            details={"free_hours": float(free), "requested_hours": float(planned_hours)},
        )
    if not alloc:
        raise DomainError(
            f"{cell.cell_name} has no schedulable days between {start} and {end}.", code="no_capacity"
        )
    last = max(alloc)
    if cohort.target_completion_date and last > cohort.target_completion_date:
        raise DomainError(
            f"SLA breach: work would finish {last}, after the target completion date {cohort.target_completion_date}.",
            code="sla_breach",
            details={"projected_completion": last.isoformat()},
        )

    # Atomic claim: each day is updated only if the hours still fit, in the same statement that checks the room.
    # Two concurrent requests therefore cannot both succeed on the same hours (SQLite serialises writers; PostgreSQL
    # takes a row lock). Any failed claim aborts the request, rolling back the claims already made.
    by_date = {r.calendar_date: r for r in rows}
    for day, hrs in alloc.items():
        res = db.execute(
            update(CapacityCalendar)
            .where(
                CapacityCalendar.id == by_date[day].id,
                CapacityCalendar.assigned_hours + hrs <= CapacityCalendar.available_hours + EPS,
            )
            .values(assigned_hours=CapacityCalendar.assigned_hours + hrs)
        )
        if res.rowcount != 1:
            db.rollback()
            raise DomainError(
                f"Overbooked: {cell.cell_name} no longer has {float(hrs):.2f}h free on {day} (claimed by another request).",
                code="overbooked",
                details={"date": day.isoformat()},
            )
        db.expire(by_date[day])
    a = CohortAssignment(
        cohort_id=cohort.id,
        robot_cell_id=cell.id,
        start_date=start,
        end_date=end,
        planned_hours=d(planned_hours),
        planned_units=planned_units,
        status="planned",
        daily_plan={k.isoformat(): float(v) for k, v in alloc.items()},
    )
    db.add(a)
    if cohort.status == "approved":
        cohort.status = "scheduled"
    db.flush()
    return a


def cancel_assignment(db: Session, assignment: CohortAssignment) -> None:
    if assignment.status == "cancelled":
        raise DomainError("Assignment is already cancelled.", code="already_cancelled")
    for iso, hrs in (assignment.daily_plan or {}).items():
        row = db.scalar(
            select(CapacityCalendar).where(
                CapacityCalendar.robot_cell_id == assignment.robot_cell_id,
                CapacityCalendar.calendar_date == date.fromisoformat(iso),
            )
        )
        if row:
            row.assigned_hours = max(d(row.assigned_hours) - d(hrs), D(0))
    assignment.status = "cancelled"


def sla_feasibility(db: Session, hours_needed: Decimal, start: date, target: date | None) -> dict:
    if not target:
        return {"feasible": None, "free_hours": None}
    free = free_hours(db, start, target)
    return {"feasible": hours_needed <= free, "free_hours": float(free), "hours_needed": float(hours_needed)}


def review_capacity_available(
    db: Session,
    policy,
    start: date,
    end: date,
    exclude_cohort_id: str | None = None,
    exceptions_by_cohort: dict[str, Decimal] | None = None,
) -> Decimal:
    """Manual-review slots left in [start,end] after other assigned cohorts' pro-rated exception load."""
    days = sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)
    total = D(policy.review_capacity_per_day) * days
    booked = D(0)
    for a in db.scalars(
        select(CohortAssignment).where(
            CohortAssignment.status != "cancelled",
            CohortAssignment.end_date >= start,
            CohortAssignment.start_date <= end,
        )
    ):
        if a.cohort_id == exclude_cohort_id:
            continue
        exc = (exceptions_by_cohort or {}).get(a.cohort_id, D(0))
        span = max((a.end_date - a.start_date).days + 1, 1)
        overlap = max((min(a.end_date, end) - max(a.start_date, start)).days + 1, 0)
        booked += exc * D(overlap) / D(span)
    return max(total - booked, D(0))


def cell_or_404(db: Session, cell_id: str) -> RobotCell:
    c = db.get(RobotCell, cell_id)
    if not c:
        raise NotFound("Robot cell")
    return c
