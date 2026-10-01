from datetime import date
from decimal import Decimal

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ..config import today
from ..errors import NotFound
from ..models import DeviceCohort
from ..policy import Policy, get_policy

AS_OF = today


def row(obj, exclude: tuple[str, ...] = ()) -> dict:
    out = {}
    for c in inspect(obj).mapper.column_attrs:
        if c.key in exclude:
            continue
        v = getattr(obj, c.key)
        out[c.key] = (
            v.isoformat()
            if isinstance(v, date) and not isinstance(v, Decimal) and hasattr(v, "isoformat")
            else v
        )
    return out


def cohort_or_404(db: Session, cohort_id: str) -> DeviceCohort:
    c = db.get(DeviceCohort, cohort_id)
    if not c:
        raise NotFound("Cohort")
    return c


def pol(db: Session) -> Policy:
    return get_policy(db)


def writable_cohort(db: Session, cohort_id: str) -> DeviceCohort:
    """Cohort for a write. Closed cohorts are frozen: corrections need a (not yet built) controlled adjustment workflow."""
    from ..errors import DomainError

    c = cohort_or_404(db, cohort_id)
    if c.status == "closed":
        raise DomainError(
            f"Cohort {c.cohort_code} is closed; changes require a controlled adjustment.",
            code="closed_cohort",
        )
    return c
