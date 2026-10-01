"""Dashboard filters: one typed schema, validated server-side, resolved into a concrete Scope.

Filters combine by INTERSECTION only. Date filtering has a documented basis per metric family -- see
`services/dashboard_semantics.py` and docs/DASHBOARD_FILTERS.md. Nothing here touches CM% or policy logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import Invalid
from .models import (
    CohortAssignment,
    DailyOperation,
    DeviceCohort,
    Partner,
    PartnerContract,
    ResaleListing,
    RobotCell,
)
from .schemas import CONTRACT_TYPES, OWNERSHIP
from .services.cohorts import TRANSITIONS

DateRange = Literal["all_time", "last_7_days", "last_30_days", "current_month", "custom"]
RANGE_LABEL = {
    "all_time": "All time", "last_7_days": "Last 7 days", "last_30_days": "Last 30 days",
    "current_month": "Current month", "custom": "Custom range",
}  # fmt: skip


class DashboardFilters(BaseModel):
    """Query-string schema for GET /api/dashboard/executive (and, in Phase 3, the drill-down endpoints)."""

    model_config = ConfigDict(extra="forbid")

    range: DateRange | None = None
    start_date: date | None = None
    end_date: date | None = None
    partner_id: str | None = None
    cohort_id: str | None = None
    robot_cell_id: str | None = None
    cohort_status: str | None = None
    contract_type: str | None = None
    ownership_model: str | None = None
    resale_channel: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.cohort_status is not None and self.cohort_status not in TRANSITIONS:
            raise ValueError(f"cohort_status must be one of {sorted(TRANSITIONS)}")
        if self.contract_type is not None and self.contract_type not in CONTRACT_TYPES:
            raise ValueError(f"contract_type must be one of {sorted(CONTRACT_TYPES)}")
        if self.ownership_model is not None and self.ownership_model not in OWNERSHIP:
            raise ValueError(f"ownership_model must be one of {sorted(OWNERSHIP)}")
        if self.range == "all_time" and (self.start_date or self.end_date):
            raise ValueError("range=all_time cannot be combined with start_date / end_date.")
        if self.range not in (None, "custom", "all_time") and (self.start_date or self.end_date):
            raise ValueError("start_date / end_date can only be combined with range=custom (or no range).")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date.")
        return self


@dataclass
class Scope:
    """Resolved filters. `start`/`end` of None mean unbounded on that side. `date_explicit` is False when the caller
    supplied no date filter at all (each metric then uses its own documented default window)."""

    date_explicit: bool = False
    range: str | None = None
    start: date | None = None
    end: date | None = None
    partner_id: str | None = None
    cohort_id: str | None = None
    cell_id: str | None = None
    channel: str | None = None
    cohort_ids: set[str] = field(default_factory=set)
    cohort_filtered: bool = False  # any filter that narrows the cohort set
    applied: list[dict] = field(default_factory=list)
    requested: dict = field(default_factory=dict)

    @property
    def active_count(self) -> int:
        return len(self.applied)


def _known(db: Session, model, value: str | None, name: str):
    if value is None:
        return None
    obj = db.get(model, value)
    if obj is None:
        raise Invalid(f"Unknown {name} '{value}'.")
    return obj


def resolve(db: Session, f: DashboardFilters, as_of: date) -> Scope:
    sc = Scope(
        requested={
            k: v
            for k, v in f.model_dump(exclude_none=True, mode="json").items()
            if k in DashboardFilters.model_fields
        }
    )
    applied: list[dict] = []

    # ---- date range --------------------------------------------------------------------------------------
    rng = f.range or ("custom" if (f.start_date or f.end_date) else None)
    if rng:
        sc.date_explicit, sc.range = True, rng
        if rng == "last_7_days":
            sc.start, sc.end = as_of - timedelta(days=6), as_of
        elif rng == "last_30_days":
            sc.start, sc.end = as_of - timedelta(days=29), as_of
        elif rng == "current_month":
            sc.start, sc.end = as_of.replace(day=1), as_of
        elif rng == "custom":
            sc.start, sc.end = f.start_date, f.end_date
        if rng != "all_time":
            lo, hi = sc.start.isoformat() if sc.start else "…", sc.end.isoformat() if sc.end else "…"
            applied.append({"key": "range", "label": "Date", "value": f"{RANGE_LABEL[rng]} ({lo} → {hi})"})

    # ---- entity filters (existence validated) -------------------------------------------------------------
    partner = _known(db, Partner, f.partner_id, "partner_id")
    cohort = _known(db, DeviceCohort, f.cohort_id, "cohort_id")
    cell = _known(db, RobotCell, f.robot_cell_id, "robot_cell_id")
    if f.resale_channel is not None and not db.scalar(
        select(ResaleListing.id).where(ResaleListing.listing_channel == f.resale_channel)
    ):
        known = sorted({c for c in db.scalars(select(ResaleListing.listing_channel))})
        raise Invalid(
            f"Unknown resale_channel '{f.resale_channel}'. Known channels: {', '.join(known) or 'none'}."
        )
    sc.partner_id, sc.cohort_id, sc.cell_id, sc.channel = (
        f.partner_id,
        f.cohort_id,
        f.robot_cell_id,
        f.resale_channel,
    )

    cohorts = list(db.scalars(select(DeviceCohort)))
    contract_type = {c.id: c.contract_type for c in db.scalars(select(PartnerContract))}
    keep = {c.id for c in cohorts}
    if partner:
        keep &= {c.id for c in cohorts if c.partner_id == partner.id}
        applied.append({"key": "partner_id", "label": "Partner", "value": partner.name})
    if cohort:
        keep &= {cohort.id}
        applied.append({"key": "cohort_id", "label": "Cohort", "value": cohort.cohort_code})
    if f.cohort_status:
        keep &= {c.id for c in cohorts if c.status == f.cohort_status}
        applied.append({"key": "cohort_status", "label": "Status (current)", "value": f.cohort_status})
    if f.contract_type:
        keep &= {c.id for c in cohorts if contract_type.get(c.contract_id or "") == f.contract_type}
        applied.append({"key": "contract_type", "label": "Contract type", "value": f.contract_type})
    if f.ownership_model:
        keep &= {c.id for c in cohorts if c.ownership_model == f.ownership_model}
        applied.append({"key": "ownership_model", "label": "Ownership", "value": f.ownership_model})
    if cell:
        used = set(
            db.scalars(select(DailyOperation.cohort_id).where(DailyOperation.robot_cell_id == cell.id))
        )
        used |= set(
            db.scalars(
                select(CohortAssignment.cohort_id).where(
                    CohortAssignment.robot_cell_id == cell.id, CohortAssignment.status != "cancelled"
                )
            )
        )
        keep &= used
        applied.append({"key": "robot_cell_id", "label": "Robot cell", "value": cell.cell_name})
    if f.resale_channel:
        keep &= set(
            db.scalars(
                select(ResaleListing.cohort_id).where(ResaleListing.listing_channel == f.resale_channel)
            )
        )
        applied.append({"key": "resale_channel", "label": "Resale channel", "value": f.resale_channel})

    sc.cohort_ids = keep
    sc.cohort_filtered = any(a["key"] not in ("range",) for a in applied)
    sc.applied = applied
    return sc


class DrillParams(DashboardFilters):
    """Dashboard filters + the drill-down-specific parameters. Filters are carried over unchanged from the dashboard URL."""

    metric: str | None = (
        None  # used by the page route (/analytics/cohort-profitability?metric=...); ignored by the API path
    )
    status: str | None = None  # overdue | all (collections);  active (cohorts page)
    severity: str | None = None  # critical | warning | info | all (alerts)
    age_gt_days: int | None = None
    table: str | None = None  # CSV: which table of a multi-table drill-down
    verify: bool = True  # also run the dashboard and report whether the detail total matches it

    @model_validator(mode="after")
    def _check_drill(self):
        if self.severity is not None and self.severity not in ("critical", "warning", "info", "all"):
            raise ValueError("severity must be critical, warning, info or all")
        if self.status is not None and self.status not in ("overdue", "all", "open", "active"):
            raise ValueError("status must be overdue, all, open or active")
        if self.age_gt_days is not None and self.age_gt_days < 0:
            raise ValueError("age_gt_days cannot be negative")
        return self
