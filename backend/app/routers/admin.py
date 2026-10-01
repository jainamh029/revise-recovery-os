import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require
from ..config import settings, today
from ..db import get_db
from ..errors import DomainError
from ..models import Device
from ..services.analytics import cohort_rows
from ..services.finance import collections_aging
from .util import pol

router = APIRouter(prefix="/api", tags=["admin"])


def _csv(header: list[str], rows: list[list]) -> PlainTextResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


def _m(v) -> str:
    return "" if v is None else f"{float(v):.2f}"


@router.get("/export/cohorts.csv")
def export_cohorts(db: Session = Depends(get_db)):
    rows = cohort_rows(db, today(), pol(db))
    return _csv(
        ["cohort", "status", "partner", "plan_cm", "forecast_cm", "actual_cm_to_date", "cash_exposure", "inventory_value"],
        [[r["code"], r["status"], r["partner"], _m(r["plan_cm"]), _m(r["forecast_cm"]), _m(r["actual_cm"]), _m(r["cash_exposure"]), _m(r["inventory_value"])] for r in rows],
    )  # fmt: skip


@router.get("/export/collections.csv")
def export_collections(db: Session = Depends(get_db)):
    a = collections_aging(db, today())
    return _csv(
        ["kind", "reference", "cohort_id", "due_date", "days_overdue", "bucket", "amount", "remaining"],
        [[i["kind"], i["ref"], i["cohort_id"], i["due_date"], i["days_overdue"], i["bucket"], _m(i["amount"]), _m(i["remaining"])] for i in a["items"]],
    )  # fmt: skip


@router.get("/export/devices.csv")
def export_devices(cohort_id: str | None = None, db: Session = Depends(get_db)):
    q = select(Device).order_by(Device.serial_number)
    if cohort_id:
        q = q.where(Device.cohort_id == cohort_id)
    return _csv(
        ["serial", "cohort_id", "status", "sanitization_status", "certificate_id", "qc_status", "disposition", "location", "custodian"],
        [[d.serial_number, d.cohort_id, d.current_status, d.sanitization_status, d.sanitization_certificate_id, d.quality_control_status,
          d.final_disposition, d.current_location, d.current_custodian] for d in db.scalars(q)],
    )  # fmt: skip


@router.post("/admin/reseed", dependencies=[Depends(require("config"))])
def reseed():
    """Test-only: restores the pristine demo dataset. Disabled unless ALLOW_RESEED=true."""
    if not settings.allow_reseed:
        raise DomainError("Reseeding is disabled (set ALLOW_RESEED=true).", code="disabled")
    import time

    from sqlalchemy.exc import DBAPIError

    from ..db import engine
    from ..seed import reset_and_seed

    last: Exception | None = None
    for attempt in range(
        1, 6
    ):  # a lock wait / deadlock with in-flight requests is transient: back off and retry
        try:
            reset_and_seed()
            return {"status": "reseeded", "attempts": attempt}
        except DBAPIError as exc:
            last = exc
            engine.dispose()
            time.sleep(0.5 * attempt)
    raise DomainError(
        f"Reseed failed after 5 attempts: {type(last).__name__}: {str(last.orig)[:200] if last else ''}",
        code="reseed_failed",
    )
