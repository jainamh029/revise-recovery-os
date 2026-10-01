"""Serial-level traceability, sanitization gating and disposition.

Non-negotiable gates (enforced here, never in the UI):
  * LISTED requires sanitization_status=SANITIZED, quality_control_status=PASSED, final_disposition=RESALE
  * SANITIZATION_FAILED => QUARANTINED; quarantined devices cannot be processed, listed, sold or shipped
  * SOLD requires LISTED, an existing sales transaction, and unsold quantity left on that transaction
  * RECYCLED / DESTROYED require a reason code and an operator reference (timestamp is set by the system)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import DomainError, Invalid, NotFound
from ..models import Device, DeviceCohort, DeviceCustodyEvent, ResaleListing, SalesTransaction, utcnow

STATUSES = [
    "RECEIVED", "QUARANTINED", "TRIAGED", "AWAITING_SANITIZATION", "SANITIZATION_FAILED", "SANITIZED", "IN_ROBOT_PROCESSING",
    "MANUAL_REVIEW", "UNDER_REPAIR", "QUALITY_CONTROL", "READY_FOR_LISTING", "LISTED", "SOLD", "RETURNED", "RECYCLED", "DESTROYED",
]  # fmt: skip
TERMINAL = {"SOLD", "RECYCLED", "DESTROYED"}  # states that close a device out for cohort closeout purposes
BLOCKED = {"QUARANTINED", "SANITIZATION_FAILED"}
REASON_CODES = {
    "DATA_SANITIZATION_FAILED",
    "BEYOND_ECONOMIC_REPAIR",
    "CONDITION_TOO_POOR",
    "SECURITY_POLICY",
    "PARTNER_REQUEST",
    "END_OF_LIFE",
    "OTHER",
}

NEXT: dict[str, set[str]] = {
    "RECEIVED": {"TRIAGED", "QUARANTINED"},
    "QUARANTINED": {"AWAITING_SANITIZATION", "RECYCLED", "DESTROYED"},
    "TRIAGED": {"AWAITING_SANITIZATION", "QUARANTINED", "RECYCLED", "DESTROYED"},
    "AWAITING_SANITIZATION": {"SANITIZED", "SANITIZATION_FAILED", "QUARANTINED", "RECYCLED", "DESTROYED"},
    "SANITIZATION_FAILED": {"QUARANTINED"},
    "SANITIZED": {"IN_ROBOT_PROCESSING", "QUALITY_CONTROL", "QUARANTINED"},
    "IN_ROBOT_PROCESSING": {"MANUAL_REVIEW", "QUALITY_CONTROL", "QUARANTINED"},
    "MANUAL_REVIEW": {"UNDER_REPAIR", "QUALITY_CONTROL", "RECYCLED", "DESTROYED", "QUARANTINED"},
    "UNDER_REPAIR": {"QUALITY_CONTROL", "MANUAL_REVIEW", "RECYCLED", "DESTROYED", "QUARANTINED"},
    "QUALITY_CONTROL": {"READY_FOR_LISTING", "MANUAL_REVIEW", "RECYCLED", "DESTROYED", "QUARANTINED"},
    "READY_FOR_LISTING": {"LISTED", "QUALITY_CONTROL", "RECYCLED", "DESTROYED", "QUARANTINED"},
    "LISTED": {"SOLD", "READY_FOR_LISTING"},
    "SOLD": {"RETURNED"},
    "RETURNED": {"QUALITY_CONTROL", "RECYCLED", "DESTROYED"},
    "RECYCLED": set(),
    "DESTROYED": set(),
}


def norm_serial(s: str) -> str:
    s = (s or "").strip().upper()
    if not s:
        raise Invalid("Serial number is required.")
    return s


def get(db: Session, device_id: str) -> Device:
    d = db.get(Device, device_id)
    if not d:
        raise NotFound("Device")
    return d


def _event(
    db: Session, d: Device, etype: str, *, frm=None, to=None, detail=None, at: datetime | None = None
) -> None:
    db.add(DeviceCustodyEvent(device_id=d.id, event_type=etype, from_status=frm, to_status=to, location=d.current_location,
                              custodian=d.current_custodian, detail=detail, occurred_at=at or utcnow()))  # fmt: skip


def _move(db: Session, d: Device, to: str, *, etype: str = "status_change", detail=None) -> None:
    if to not in NEXT.get(d.current_status, set()):
        raise DomainError(
            f"Illegal device transition {d.current_status} -> {to}.", code="bad_device_transition"
        )
    if d.current_status in BLOCKED and to in {"IN_ROBOT_PROCESSING", "LISTED", "SOLD", "READY_FOR_LISTING"}:
        raise DomainError(
            "Quarantined devices are blocked from processing, resale and shipment.", code="quarantined"
        )
    frm, d.current_status = d.current_status, to
    _event(db, d, etype, frm=frm, to=to, detail=detail)


def register(db: Session, cohort_id: str, *, serial_number: str, current_location: str = "Receiving dock", current_custodian: str = "intake.operator",
             storage_type: str = "ssd", data_sanitization_required: bool = True, **extra) -> Device:  # fmt: skip
    cohort = db.get(DeviceCohort, cohort_id)
    if not cohort:
        raise NotFound("Cohort")
    if cohort.status in ("declined", "cancelled", "draft", "under_review"):
        raise DomainError(
            f"Cannot receive devices into a cohort in status '{cohort.status}'.", code="bad_status"
        )
    serial = norm_serial(serial_number)
    if db.scalar(select(Device.id).where(Device.serial_number == serial)):
        raise DomainError(f"Serial number {serial} already exists.", code="duplicate_serial")
    if storage_type not in {"ssd", "hdd", "nvme", "emmc", "none"}:
        raise Invalid("storage_type must be one of ssd, hdd, nvme, emmc, none.")
    if not data_sanitization_required and storage_type != "none":
        raise Invalid("Sanitization can only be waived for devices with no storage (storage_type='none').")
    d = Device(cohort_id=cohort_id, serial_number=serial, storage_type=storage_type, current_location=current_location, current_custodian=current_custodian,
               data_sanitization_required=data_sanitization_required,
               sanitization_status="PENDING" if data_sanitization_required else "NOT_REQUIRED", **extra)  # fmt: skip
    db.add(d)
    try:
        db.flush()
    except IntegrityError as exc:  # race on the unique constraint
        db.rollback()
        raise DomainError(f"Serial number {serial} already exists.", code="duplicate_serial") from exc
    _event(db, d, "received", to="RECEIVED", detail={"serial": serial})
    return d


def transfer(db: Session, d: Device, *, location: str, custodian: str, note: str | None = None) -> None:
    if d.current_status in {"RECYCLED", "DESTROYED"}:
        raise DomainError("Device is in a terminal state; custody can no longer change.", code="terminal")
    old = (d.current_location, d.current_custodian)
    d.current_location, d.current_custodian = location, custodian
    _event(db, d, "transfer", detail={"from_location": old[0], "from_custodian": old[1], "note": note})


def advance(db: Session, d: Device, to: str) -> None:
    """Generic, gated progression for steps that have no dedicated endpoint (triage, robot processing, repair...)."""
    if to in {
        "SANITIZED",
        "SANITIZATION_FAILED",
        "LISTED",
        "SOLD",
        "RETURNED",
        "RECYCLED",
        "DESTROYED",
        "READY_FOR_LISTING",
    }:
        raise DomainError(
            f"{to} can only be reached through its dedicated, gated action.", code="use_dedicated_action"
        )
    if to == "IN_ROBOT_PROCESSING" and d.sanitization_status != "SANITIZED":
        raise DomainError("Robot processing requires a completed sanitization.", code="sanitization_required")
    if to == "QUALITY_CONTROL" and d.data_sanitization_required and d.sanitization_status != "SANITIZED":
        raise DomainError("Quality control requires a completed sanitization.", code="sanitization_required")
    if to == "AWAITING_SANITIZATION" and d.current_status == "QUARANTINED":
        d.sanitization_status = "PENDING"  # release from quarantine only into a fresh wipe
    _move(db, d, to)


def record_sanitization(db: Session, d: Device, *, passed: bool, method: str, certificate_id: str | None = None,
                        completed_at: datetime | None = None) -> None:  # fmt: skip
    if d.current_status != "AWAITING_SANITIZATION":
        raise DomainError(
            f"Sanitization can only be recorded while AWAITING_SANITIZATION (now {d.current_status}).",
            code="bad_device_transition",
        )
    if not method:
        raise Invalid("Sanitization method is required.")
    at = completed_at or utcnow()
    d.sanitization_method = method
    if passed:
        if not certificate_id or not certificate_id.strip():
            raise Invalid("A certificate / result ID is required to record a passed sanitization.")
        d.sanitization_status, d.sanitization_completed_at, d.sanitization_certificate_id = (
            "SANITIZED",
            at,
            certificate_id.strip(),
        )
        _move(
            db,
            d,
            "SANITIZED",
            etype="sanitization",
            detail={"result": "PASSED", "method": method, "certificate_id": certificate_id},
        )
    else:
        d.sanitization_status, d.sanitization_completed_at, d.sanitization_certificate_id = "FAILED", at, None
        d.quality_control_status = "PENDING"
        _move(
            db, d, "SANITIZATION_FAILED", etype="sanitization", detail={"result": "FAILED", "method": method}
        )
        _move(db, d, "QUARANTINED", detail={"reason": "sanitization failed"})


def record_qc(db: Session, d: Device, *, passed: bool, notes: str | None = None) -> None:
    if d.current_status != "QUALITY_CONTROL":
        raise DomainError(
            f"Quality control can only be recorded while in QUALITY_CONTROL (now {d.current_status}).",
            code="bad_device_transition",
        )
    if d.data_sanitization_required and d.sanitization_status != "SANITIZED":
        raise DomainError(
            "Cannot pass quality control without a completed sanitization.", code="sanitization_required"
        )
    d.quality_control_status = "PASSED" if passed else "FAILED"
    _move(
        db,
        d,
        "READY_FOR_LISTING" if passed else "MANUAL_REVIEW",
        etype="quality_control",
        detail={"result": d.quality_control_status, "notes": notes},
    )


def set_disposition(
    db: Session,
    d: Device,
    disposition: str,
    *,
    reason_code: str | None = None,
    operator_ref: str | None = None,
) -> None:
    disposition = disposition.upper()
    if disposition not in {"RESALE", "RECYCLE", "DESTROY"}:
        raise Invalid("disposition must be RESALE, RECYCLE or DESTROY.")
    if d.current_status in {"LISTED", "SOLD"}:
        raise DomainError(
            "Delist or return the device before changing its disposition.", code="bad_device_transition"
        )
    if disposition == "RESALE":
        if d.current_status in BLOCKED:
            raise DomainError("Quarantined devices cannot be designated for resale.", code="quarantined")
        d.final_disposition = "RESALE"
        _event(db, d, "disposition", detail={"disposition": "RESALE"})
        return
    if not reason_code or reason_code not in REASON_CODES:
        raise Invalid(f"A valid reason_code is required: {sorted(REASON_CODES)}")
    if not operator_ref or not operator_ref.strip():
        raise Invalid("An operator / custodian reference is required to recycle or destroy a device.")
    d.final_disposition, d.disposition_reason_code, d.disposition_operator, d.disposition_at = (
        disposition,
        reason_code,
        operator_ref.strip(),
        utcnow(),
    )
    d.current_custodian = operator_ref.strip()
    _move(db, d, "RECYCLED" if disposition == "RECYCLE" else "DESTROYED", etype="disposition",
          detail={"reason_code": reason_code, "operator": operator_ref})  # fmt: skip


def list_device(db: Session, d: Device, listing_id: str) -> None:
    problems = []
    if d.current_status in BLOCKED:
        problems.append("device is quarantined")
    if d.sanitization_status != "SANITIZED":
        problems.append(f"sanitization_status is {d.sanitization_status}, must be SANITIZED")
    if d.quality_control_status != "PASSED":
        problems.append(f"quality_control_status is {d.quality_control_status}, must be PASSED")
    if d.final_disposition != "RESALE":
        problems.append(f"final_disposition is {d.final_disposition}, must be RESALE")
    if d.current_status != "READY_FOR_LISTING":
        problems.append(f"status is {d.current_status}, must be READY_FOR_LISTING")
    if problems:
        raise DomainError(
            "Cannot list device: " + "; ".join(problems) + ".",
            code="listing_gate",
            details={"problems": problems},
        )
    ls = db.get(ResaleListing, listing_id)
    if not ls or ls.cohort_id != d.cohort_id:
        raise DomainError("Listing not found for this device's cohort.", code="bad_listing")
    n_listed = (
        db.scalar(select(func.count()).select_from(Device).where(Device.resale_listing_id == ls.id)) or 0
    )
    if n_listed >= ls.device_count:
        raise DomainError(f"Listing is full ({n_listed}/{ls.device_count} devices).", code="listing_full")
    d.resale_listing_id = ls.id
    _move(db, d, "LISTED", etype="listing", detail={"listing_id": ls.id})


def delist(db: Session, d: Device) -> None:
    d.resale_listing_id = None
    _move(db, d, "READY_FOR_LISTING", etype="listing", detail={"action": "delist"})


def sell_device(db: Session, d: Device, sale_id: str) -> None:
    if d.current_status != "LISTED":
        raise DomainError(f"Only LISTED devices can be sold (now {d.current_status}).", code="not_listed")
    tx = db.get(SalesTransaction, sale_id)
    if not tx or tx.cohort_id != d.cohort_id:
        raise DomainError("Sales transaction not found for this device's cohort.", code="no_sale")
    if d.resale_listing_id and tx.resale_listing_id and tx.resale_listing_id != d.resale_listing_id:
        raise DomainError("Sale belongs to a different listing.", code="bad_listing")
    sold = (
        db.scalar(select(func.count()).select_from(Device).where(Device.sales_transaction_id == tx.id)) or 0
    )
    if sold >= tx.units_sold:
        raise DomainError(
            f"Sale already covers {sold}/{tx.units_sold} devices; no unsold quantity remains on it.",
            code="sale_quantity_exceeded",
        )
    d.sales_transaction_id = tx.id
    _move(db, d, "SOLD", etype="sale", detail={"sales_transaction_id": tx.id})


def return_device(db: Session, d: Device) -> None:
    if d.current_status != "SOLD" or not d.sales_transaction_id:
        raise DomainError("Only SOLD devices can be returned.", code="not_sold")
    tx = db.get(SalesTransaction, d.sales_transaction_id)
    returned = (
        db.scalar(
            select(func.count())
            .select_from(Device)
            .where(Device.sales_transaction_id == tx.id, Device.current_status == "RETURNED")
        )
        or 0
    )
    if returned >= tx.units_returned:
        raise DomainError(
            "Record the return on the sale (refund/return cost) before marking devices returned.",
            code="return_not_booked",
        )
    d.quality_control_status = "PENDING"
    _move(db, d, "RETURNED", etype="return", detail={"sales_transaction_id": tx.id})


def reconciliation(db: Session, cohort: DeviceCohort) -> dict:
    counts = dict(
        db.execute(
            select(Device.current_status, func.count())
            .where(Device.cohort_id == cohort.id)
            .group_by(Device.current_status)
        ).all()
    )
    registered = sum(counts.values())
    g = lambda *ks: sum(counts.get(k, 0) for k in ks)  # noqa: E731
    sold, recycled, destroyed = g("SOLD"), g("RECYCLED"), g("DESTROYED")
    active = registered - sold - recycled - destroyed
    return {
        "cohort_id": cohort.id, "registered": registered, "received_per_operations": cohort.actual_device_count,
        "unregistered": cohort.actual_device_count - registered, "by_status": {s: counts.get(s, 0) for s in STATUSES},
        "sold": sold, "recycled": recycled, "destroyed": destroyed, "active_inventory": active,
        "reconciles": sold + recycled + destroyed + active == registered,
        "matches_intake": registered == cohort.actual_device_count,
        "quarantined": g("QUARANTINED", "SANITIZATION_FAILED"),
    }  # fmt: skip


def export(db: Session, d: Device) -> dict:
    events = list(
        db.scalars(
            select(DeviceCustodyEvent)
            .where(DeviceCustodyEvent.device_id == d.id)
            .order_by(DeviceCustodyEvent.occurred_at, DeviceCustodyEvent.id)
        )
    )
    cohort = db.get(DeviceCohort, d.cohort_id)
    return {
        "serial_number": d.serial_number, "asset_tag": d.asset_tag, "cohort": cohort.cohort_code, "manufacturer": d.manufacturer, "model": d.model,
        "intake_timestamp": d.intake_timestamp.isoformat(), "current_status": d.current_status, "current_location": d.current_location,
        "current_custodian": d.current_custodian,
        "sanitization": {"required": d.data_sanitization_required, "status": d.sanitization_status, "method": d.sanitization_method,
                         "completed_at": d.sanitization_completed_at.isoformat() if d.sanitization_completed_at else None,
                         "certificate_id": d.sanitization_certificate_id},
        "quality_control_status": d.quality_control_status,
        "disposition": {"final": d.final_disposition, "reason_code": d.disposition_reason_code, "operator": d.disposition_operator,
                        "at": d.disposition_at.isoformat() if d.disposition_at else None},
        "resale_listing_id": d.resale_listing_id, "sales_transaction_id": d.sales_transaction_id,
        "custody_events": [{"at": e.occurred_at.isoformat(), "type": e.event_type, "from": e.from_status, "to": e.to_status, "location": e.location,
                            "custodian": e.custodian, "detail": e.detail} for e in events],
    }  # fmt: skip
