import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import require
from ..config import today
from ..db import get_db
from ..errors import DomainError
from ..models import Device, DeviceCustodyEvent
from ..services import alerts as alert_svc
from ..services import devices as svc
from .util import cohort_or_404, pol, row

router = APIRouter(prefix="/api", tags=["devices"])
W = [Depends(require("ops_write"))]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceIn(Strict):
    cohort_id: str
    serial_number: str
    asset_tag: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    storage_type: str = "ssd"
    intake_condition_grade: str | None = None
    current_location: str = "Receiving dock"
    current_custodian: str = "intake.operator"
    data_sanitization_required: bool = True


class BulkIn(Strict):
    cohort_id: str
    devices: list[DeviceIn] = Field(min_length=1, max_length=2000)


class TransferIn(Strict):
    location: str
    custodian: str
    note: str | None = None


class AdvanceIn(Strict):
    to: str


class SanitizeIn(Strict):
    passed: bool
    method: str = "NIST 800-88 Clear (software overwrite)"
    certificate_id: str | None = None
    completed_at: datetime | None = None


class QcIn(Strict):
    passed: bool
    notes: str | None = None


class DispositionIn(Strict):
    disposition: str
    reason_code: str | None = None
    operator_ref: str | None = None


class ListIn(Strict):
    resale_listing_id: str


class SellIn(Strict):
    sales_transaction_id: str


def _refresh(db: Session) -> None:
    alert_svc.evaluate(db, today(), pol(db))


def _detail(db: Session, d: Device) -> dict:
    ev = db.scalars(
        select(DeviceCustodyEvent)
        .where(DeviceCustodyEvent.device_id == d.id)
        .order_by(DeviceCustodyEvent.occurred_at, DeviceCustodyEvent.id)
    )
    return {**row(d), "custody_events": [row(e) for e in ev]}


@router.post("/devices", status_code=201, dependencies=W)
def create_device(body: DeviceIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    d = svc.register(db, data.pop("cohort_id"), **data)
    db.commit()
    return row(d)


@router.post("/devices/bulk", status_code=201, dependencies=W)
def bulk(body: BulkIn, db: Session = Depends(get_db)):
    out = []
    for item in body.devices:
        data = item.model_dump()
        data.pop("cohort_id")
        out.append(svc.register(db, body.cohort_id, **data).id)
    db.commit()  # all-or-nothing: one duplicate serial aborts the whole batch
    return {"created": len(out)}


@router.get("/devices")
def list_devices(cohort_id: str | None = None, status: str | None = None, q: str | None = None, limit: int = 200, offset: int = 0,
                 db: Session = Depends(get_db)):  # fmt: skip
    qry = select(Device).order_by(Device.serial_number).limit(min(limit, 1000)).offset(offset)
    if cohort_id:
        qry = qry.where(Device.cohort_id == cohort_id)
    if status:
        qry = qry.where(Device.current_status == status.upper())
    if q:
        qry = qry.where(or_(Device.serial_number.ilike(f"%{q}%"), Device.asset_tag.ilike(f"%{q}%")))
    return [row(d) for d in db.scalars(qry)]


@router.get("/devices/{device_id}")
def get_device(device_id: str, db: Session = Depends(get_db)):
    return _detail(db, svc.get(db, device_id))


@router.post("/devices/{device_id}/transfer", dependencies=W)
def transfer(device_id: str, body: TransferIn, db: Session = Depends(get_db)):
    d = svc.get(db, device_id)
    svc.transfer(db, d, **body.model_dump())
    db.commit()
    return _detail(db, d)


@router.post("/devices/{device_id}/advance", dependencies=W)
def advance(device_id: str, body: AdvanceIn, db: Session = Depends(get_db)):
    d = svc.get(db, device_id)
    svc.advance(db, d, body.to.upper())
    _refresh(db)
    db.commit()
    return _detail(db, d)


@router.post("/devices/{device_id}/sanitization", dependencies=W)
def sanitization(device_id: str, body: SanitizeIn, db: Session = Depends(get_db)):
    d = svc.get(db, device_id)
    svc.record_sanitization(db, d, **body.model_dump())
    _refresh(db)
    db.commit()
    return _detail(db, d)


@router.post("/devices/{device_id}/qc", dependencies=W)
def qc(device_id: str, body: QcIn, db: Session = Depends(get_db)):
    d = svc.get(db, device_id)
    svc.record_qc(db, d, **body.model_dump())
    db.commit()
    return _detail(db, d)


@router.post("/devices/{device_id}/disposition", dependencies=W)
def disposition(device_id: str, body: DispositionIn, db: Session = Depends(get_db)):
    d = svc.get(db, device_id)
    svc.set_disposition(db, d, body.disposition, reason_code=body.reason_code, operator_ref=body.operator_ref)
    _refresh(db)
    db.commit()
    return _detail(db, d)


@router.post("/devices/{device_id}/list", dependencies=W)
def list_it(device_id: str, body: ListIn, db: Session = Depends(get_db)):
    d = svc.get(db, device_id)
    svc.list_device(db, d, body.resale_listing_id)
    db.commit()
    return _detail(db, d)


@router.post("/devices/{device_id}/delist", dependencies=W)
def delist(device_id: str, db: Session = Depends(get_db)):
    d = svc.get(db, device_id)
    svc.delist(db, d)
    db.commit()
    return _detail(db, d)


@router.post("/devices/{device_id}/sell", dependencies=W)
def sell(device_id: str, body: SellIn, db: Session = Depends(get_db)):
    d = svc.get(db, device_id)
    svc.sell_device(db, d, body.sales_transaction_id)
    db.commit()
    return _detail(db, d)


@router.post("/devices/{device_id}/return", dependencies=W)
def return_it(device_id: str, db: Session = Depends(get_db)):
    d = svc.get(db, device_id)
    svc.return_device(db, d)
    db.commit()
    return _detail(db, d)


@router.get("/devices/{device_id}/export")
def export(device_id: str, format: str = "json", db: Session = Depends(get_db)):
    data = svc.export(db, svc.get(db, device_id))
    if format == "json":
        return data
    if format != "csv":
        raise DomainError("format must be json or csv", code="invalid")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "serial",
            "cohort",
            "event_at",
            "event_type",
            "from_status",
            "to_status",
            "location",
            "custodian",
            "sanitization",
            "certificate_id",
            "qc",
            "disposition",
        ]
    )
    for e in data["custody_events"]:
        w.writerow([data["serial_number"], data["cohort"], e["at"], e["type"], e["from"], e["to"], e["location"], e["custodian"],
                    data["sanitization"]["status"], data["sanitization"]["certificate_id"], data["quality_control_status"], data["disposition"]["final"]])  # fmt: skip
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


@router.get("/cohorts/{cohort_id}/device-reconciliation")
def reconciliation(cohort_id: str, db: Session = Depends(get_db)):
    return svc.reconciliation(db, cohort_or_404(db, cohort_id))
