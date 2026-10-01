"""Cross-cohort helpers (portfolio exposure, expected exception load)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import today
from ..models import CohortBudgetVersion, DeviceCohort
from .economics import d
from .finance import cohort_actuals

ACTIVE = (
    "in_intake",
    "in_processing",
    "exception_review",
    "quality_control",
    "listed_for_sale",
    "partially_sold",
)
COMMITTED = ("approved", "scheduled")
ZERO = Decimal(0)


def portfolio_cash_exposure(db: Session, exclude_cohort_id: str | None = None) -> Decimal:
    """Cash out minus cash collected on live cohorts + budgeted cash requirement of approved-not-started cohorts."""
    total = ZERO
    for c in db.scalars(select(DeviceCohort).where(DeviceCohort.status.in_(ACTIVE + COMMITTED))):
        if c.id == exclude_cohort_id:
            continue
        if c.status in COMMITTED:
            v = (
                db.get(CohortBudgetVersion, c.approved_budget_version_id)
                if c.approved_budget_version_id
                else None
            )
            total += d(v.expected_cash_exposure) if v else ZERO
        else:
            total += max(cohort_actuals(db, c.id, today())["cash_exposure"], ZERO)
    return total


def expected_exceptions_by_cohort(db: Session) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for c in db.scalars(select(DeviceCohort).where(DeviceCohort.approved_budget_version_id.is_not(None))):
        v = db.get(CohortBudgetVersion, c.approved_budget_version_id)
        if v and v.economics:
            out[c.id] = d(v.economics.get("exception_units", 0))
    return out
