from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import require
from ..config import DEMO_DISCLAIMER
from ..db import get_db
from ..errors import DomainError, NotFound
from ..models import DeviceModelProfile, Partner, PartnerContract, RobotCell
from ..policy import get_policy, policy_to_json, update_policy
from ..services import audit
from .util import row

router = APIRouter(prefix="/api", tags=["master"])


@router.get("/meta")
def meta():
    return {"disclaimer": DEMO_DISCLAIMER, "product": "Revise Recovery OS"}


@router.get("/partners")
def list_partners(db: Session = Depends(get_db)):
    return [row(p) for p in db.scalars(select(Partner).order_by(Partner.name))]


@router.post("/partners", status_code=201, dependencies=[Depends(require("draft"))])
def create_partner(body: schemas.PartnerIn, db: Session = Depends(get_db)):
    if body.partner_type not in schemas.PARTNER_TYPES:
        raise DomainError(f"partner_type must be one of {sorted(schemas.PARTNER_TYPES)}", code="invalid")
    p = Partner(**body.model_dump())
    db.add(p)
    db.flush()
    audit.log(db, "partner", p.id, "created", None, {"name": p.name})
    db.commit()
    return row(p)


@router.patch("/partners/{partner_id}", dependencies=[Depends(require("draft"))])
def patch_partner(partner_id: str, body: dict, db: Session = Depends(get_db)):
    p = db.get(Partner, partner_id)
    if not p:
        raise NotFound("Partner")
    allowed = set(schemas.PartnerIn.model_fields)
    for k, v in body.items():
        if k in allowed:
            setattr(p, k, v)
    db.commit()
    return row(p)


@router.get("/contracts")
def list_contracts(partner_id: str | None = None, db: Session = Depends(get_db)):
    q = select(PartnerContract).order_by(PartnerContract.contract_name)
    if partner_id:
        q = q.where(PartnerContract.partner_id == partner_id)
    return [row(c) for c in db.scalars(q)]


@router.post("/contracts", status_code=201, dependencies=[Depends(require("draft"))])
def create_contract(body: schemas.ContractIn, db: Session = Depends(get_db)):
    if not db.get(Partner, body.partner_id):
        raise NotFound("Partner")
    c = PartnerContract(**body.model_dump())
    db.add(c)
    db.flush()
    audit.log(db, "contract", c.id, "created", None, {"name": c.contract_name})
    db.commit()
    return row(c)


@router.get("/device-models")
def list_models(db: Session = Depends(get_db)):
    return [
        row(m)
        for m in db.scalars(
            select(DeviceModelProfile)
            .where(DeviceModelProfile.active.is_(True))
            .order_by(DeviceModelProfile.manufacturer, DeviceModelProfile.model_name)
        )
    ]


@router.get("/robot-cells")
def list_cells(db: Session = Depends(get_db)):
    return [row(c) for c in db.scalars(select(RobotCell).order_by(RobotCell.cell_name))]


@router.patch("/robot-cells/{cell_id}", dependencies=[Depends(require("ops_write"))])
def patch_cell(cell_id: str, body: dict, db: Session = Depends(get_db)):
    c = db.get(RobotCell, cell_id)
    if not c:
        raise NotFound("Robot cell")
    if "status" in body:
        if body["status"] not in ("available", "maintenance", "offline", "blocked"):
            raise DomainError("Invalid cell status.", code="invalid")
        c.status = body["status"]
    db.commit()
    return row(c)


@router.get("/policy")
def read_policy(db: Session = Depends(get_db)):
    return policy_to_json(get_policy(db))


@router.patch("/policy", dependencies=[Depends(require("config"))])
def patch_policy(body: dict, db: Session = Depends(get_db)):
    try:
        p = update_policy(db, body)
    except (ValueError, ArithmeticError) as e:
        raise DomainError(str(e), code="invalid") from e
    audit.log(db, "policy", "1", "updated", None, body)
    db.commit()
    return policy_to_json(p)
