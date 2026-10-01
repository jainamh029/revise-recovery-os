from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import require
from ..config import today
from ..db import get_db
from ..errors import DomainError, Invalid, NotFound
from ..models import CashReceipt, CostEntry, DeviceCohort, Invoice, Partner, ResaleListing, SalesTransaction
from ..services import alerts as alert_svc
from ..services import cohorts as cohort_svc
from ..services import finance
from .util import pol, row, writable_cohort

router = APIRouter(prefix="/api", tags=["finance"])


def _advance(db: Session, cohort: DeviceCohort, target: str) -> None:
    chain = ["in_processing", "quality_control", "listed_for_sale", "partially_sold"]
    if cohort.status not in chain or target not in chain:
        return
    while chain.index(cohort.status) < chain.index(target):
        cohort_svc.transition(db, cohort, chain[chain.index(cohort.status) + 1])


@router.post("/cost-entries", status_code=201, dependencies=[Depends(require("finance_write"))])
def cost_entry(body: schemas.CostIn, db: Session = Depends(get_db)):
    if body.cost_category not in finance.COST_CATEGORIES:
        raise Invalid(f"cost_category must be one of {sorted(finance.COST_CATEGORIES)}")
    if not body.cohort_id and not body.allocation_rule:
        raise DomainError(
            "Direct costs must be attached to a cohort or explained by an approved allocation rule.",
            code="unallocated_cost",
        )
    if body.cohort_id:
        writable_cohort(db, body.cohort_id)
    e = CostEntry(**body.model_dump())
    db.add(e)
    alert_svc.evaluate(db, today(), pol(db))
    db.commit()
    return row(e)


@router.get("/cost-entries")
def list_costs(cohort_id: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    q = select(CostEntry).order_by(CostEntry.cost_date.desc()).limit(limit)
    if cohort_id:
        q = q.where(CostEntry.cohort_id == cohort_id)
    return [row(c) for c in db.scalars(q)]


@router.post("/resale-listings", status_code=201, dependencies=[Depends(require("commercial"))])
def listing(body: schemas.ListingIn, db: Session = Depends(get_db)):
    c = writable_cohort(db, body.cohort_id)
    ls = ResaleListing(**body.model_dump(), status="active")
    db.add(ls)
    _advance(db, c, "listed_for_sale")
    db.commit()
    return row(ls)


@router.get("/resale-listings")
def list_listings(cohort_id: str | None = None, db: Session = Depends(get_db)):
    q = select(ResaleListing).order_by(ResaleListing.listing_date.desc())
    if cohort_id:
        q = q.where(ResaleListing.cohort_id == cohort_id)
    out = []
    for ls in db.scalars(q):
        sold = sum(
            (
                s.units_sold - s.units_returned
                for s in db.scalars(
                    select(SalesTransaction).where(SalesTransaction.resale_listing_id == ls.id)
                )
            ),
            0,
        )
        out.append(
            {
                **row(ls),
                "units_sold": sold,
                "units_unsold": max(ls.device_count - sold, 0),
                "age_days": (today() - ls.listing_date).days if ls.listing_date else None,
            }
        )
    return out


@router.post("/sales-transactions", status_code=201, dependencies=[Depends(require("commercial"))])
def sale(body: schemas.SaleIn, db: Session = Depends(get_db)):
    c = writable_cohort(db, body.cohort_id)
    tx = finance.post_sale(db, **body.model_dump())
    _advance(db, c, "partially_sold")
    alert_svc.evaluate(db, today(), pol(db))
    db.commit()
    return row(tx)


@router.get("/sales-transactions")
def list_sales(cohort_id: str | None = None, db: Session = Depends(get_db)):
    q = select(SalesTransaction).order_by(SalesTransaction.sale_date.desc()).limit(200)
    if cohort_id:
        q = q.where(SalesTransaction.cohort_id == cohort_id)
    return [row(s) for s in db.scalars(q)]


@router.post("/sales-transactions/{sale_id}/return", dependencies=[Depends(require("commercial"))])
def sale_return(sale_id: str, body: schemas.ReturnIn, db: Session = Depends(get_db)):
    tx = finance.record_return(db, sale_id, body.units, body.refund_amount, body.return_cost)
    alert_svc.evaluate(db, today(), pol(db))
    db.commit()
    return row(tx)


@router.post("/invoices", status_code=201, dependencies=[Depends(require("finance_write"))])
def invoice(body: schemas.InvoiceIn, db: Session = Depends(get_db)):
    if not db.get(Partner, body.partner_id):
        raise NotFound("Partner")
    if db.scalar(select(Invoice).where(Invoice.invoice_number == body.invoice_number)):
        raise DomainError("Invoice number already exists.", code="duplicate")
    inv = finance.post_invoice(db, **body.model_dump())
    db.commit()
    return row(inv)


@router.get("/invoices")
def list_invoices(db: Session = Depends(get_db)):
    out = []
    for i in db.scalars(select(Invoice).order_by(Invoice.due_date)):
        paid = sum(
            (r.amount for r in db.scalars(select(CashReceipt).where(CashReceipt.invoice_id == i.id))), 0
        )
        out.append({**row(i), "partner": i.partner.name, "paid": paid, "remaining": i.amount - paid})
    return out


@router.post("/cash-receipts", status_code=201, dependencies=[Depends(require("finance_write"))])
def receipt(body: schemas.ReceiptIn, db: Session = Depends(get_db)):
    r = finance.post_receipt(db, **body.model_dump())
    alert_svc.evaluate(db, today(), pol(db))
    db.commit()
    return row(r)


@router.get("/collections/aging")
def aging(db: Session = Depends(get_db)):
    return finance.collections_aging(db, today())


@router.get("/analytics/inventory-aging")
def inv_aging(db: Session = Depends(get_db)):
    return finance.inventory_aging(db, today(), pol(db).target_days_to_sale)


_ = timedelta
