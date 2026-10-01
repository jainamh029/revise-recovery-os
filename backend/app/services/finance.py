"""Actuals, cash exposure, collections and inventory aging.

Cash rules: resale revenue is cash only once a payout receipt exists; invoice revenue is cash only once a
receipt exists. Sales-side costs (fees, shipping, return cost) are deducted in the payout, so they hit
margin but are not separate cash outflows.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import today
from ..errors import DomainError, Invalid, NotFound
from ..models import CashReceipt, CostEntry, DailyOperation, Invoice, ResaleListing, SalesTransaction
from .economics import d, safe_div

D = Decimal
ZERO = D(0)
COST_CATEGORIES = {
    "device_acquisition",
    "inbound_logistics",
    "robot_operations",
    "technician_labor",
    "repair_parts",
    "quality_control",
    "outbound_shipping",
    "marketplace_fee",
    "returns",
    "other_direct_cost",
    "allocated_support_cost",
}
SALE_SIDE = {"outbound_shipping", "marketplace_fee", "returns"}  # only count if posted manually


def _sum(rows, attr):
    return sum((d(getattr(r, attr)) for r in rows), ZERO)


def cohort_actuals(
    db: Session, cohort_id: str, as_of: date, start: date | None = None, channel: str | None = None
) -> dict:
    """Actuals for one cohort, optionally windowed.

    `start` (inclusive) bounds every dated source by ITS OWN date: operations by operation_date, cost entries by cost_date,
    sales by sale_date, invoices by invoice_date, cash receipts by receipt_date. `channel` keeps only sales made through that
    resale channel and then excludes cohort-level processing costs, operations and invoices (they cannot be attributed to a
    channel), so the result is a channel RECOVERY margin. Inventory fields are NOT window-correct: use the unwindowed call.
    """
    ops = list(
        db.scalars(
            select(DailyOperation).where(
                DailyOperation.cohort_id == cohort_id, DailyOperation.operation_date <= as_of
            )
        )
    )
    costs = list(
        db.scalars(select(CostEntry).where(CostEntry.cohort_id == cohort_id, CostEntry.cost_date <= as_of))
    )
    sales = list(
        db.scalars(
            select(SalesTransaction).where(
                SalesTransaction.cohort_id == cohort_id, SalesTransaction.sale_date <= as_of
            )
        )
    )
    invoices = list(
        db.scalars(select(Invoice).where(Invoice.cohort_id == cohort_id, Invoice.invoice_date <= as_of))
    )
    if start:
        ops = [o for o in ops if o.operation_date >= start]
        costs = [c for c in costs if c.cost_date >= start]
        sales = [x for x in sales if x.sale_date and x.sale_date >= start]
        invoices = [i for i in invoices if i.invoice_date >= start]
    if channel:
        listing_channel = {
            ls.id: ls.listing_channel
            for ls in db.scalars(select(ResaleListing).where(ResaleListing.cohort_id == cohort_id))
        }
        sales = [x for x in sales if listing_channel.get(x.resale_listing_id) == channel]
        invoices, ops = [], []
        costs = [c for c in costs if c.cost_category in SALE_SIDE]
    sale_ids = [s.id for s in sales]
    inv_ids = [i.id for i in invoices]
    receipts = []
    if sale_ids:
        receipts += list(
            db.scalars(
                select(CashReceipt).where(
                    CashReceipt.sales_transaction_id.in_(sale_ids), CashReceipt.receipt_date <= as_of
                )
            )
        )
    if inv_ids:
        receipts += list(
            db.scalars(
                select(CashReceipt).where(
                    CashReceipt.invoice_id.in_(inv_ids), CashReceipt.receipt_date <= as_of
                )
            )
        )

    if start:
        receipts = [r for r in receipts if r.receipt_date >= start]

    by_cat: dict[str, Decimal] = {}
    for c in costs:
        by_cat[c.cost_category] = by_cat.get(c.cost_category, ZERO) + d(c.amount)

    units = {
        "received": sum(o.devices_received for o in ops),
        "started": sum(o.devices_started for o in ops),
        "completed": sum(o.devices_completed for o in ops),
        "first_pass": sum(o.first_pass_completions for o in ops),
        "exceptions": sum(o.manual_exception_count for o in ops),
        "qa_approved": sum(o.quality_approved_count for o in ops),
        "rework_events": sum(o.rework_events for o in ops),
    }
    hours = {
        "productive": _sum(ops, "productive_robot_hours"),
        "downtime": _sum(ops, "downtime_hours"),
        "technician": _sum(ops, "technician_hours"),
    }
    gross = _sum(sales, "gross_sale_amount")
    refunds = _sum(sales, "refund_amount")
    discounts = _sum(sales, "discount_amount")
    s_fee, s_ship, s_ret = (
        _sum(sales, "marketplace_fee"),
        _sum(sales, "shipping_cost"),
        _sum(sales, "return_cost"),
    )
    units_sold = sum(s.units_sold for s in sales)
    units_returned = sum(s.units_returned for s in sales)
    sales_out = {
        "units_sold": units_sold,
        "units_returned": units_returned,
        "gross": gross,
        "refunds": refunds,
        "discounts": discounts,
        "marketplace_fees": s_fee,
        "shipping": s_ship,
        "return_cost": s_ret,
        "net": _sum(sales, "net_sale_amount"),
    }

    billed = _sum(invoices, "amount")
    inv_collected = sum((d(r.amount) for r in receipts if r.invoice_id), ZERO)

    manual_sale_costs = sum((v for k, v in by_cat.items() if k in SALE_SIDE), ZERO)
    pre_sale_cost = sum((v for k, v in by_cat.items() if k not in SALE_SIDE), ZERO)
    # Manually posted sale-side entries are classified like their sale-record equivalents.
    fees_total = s_fee + by_cat.get("marketplace_fee", ZERO)
    shipping_total = s_ship + by_cat.get("outbound_shipping", ZERO)
    return_ship_total = s_ret + by_cat.get("returns", ZERO)
    # net_recognized_revenue = gross - discounts - refunds - marketplace fees (+ invoiced service fees); each exactly once.
    net_revenue = gross - discounts - refunds - fees_total + billed
    # Direct variable cost: pre-sale costs + merchant-paid shipping + return shipping. Fees are NOT here (they are in revenue).
    cost_total = pre_sale_cost + shipping_total + return_ship_total
    cm = net_revenue - cost_total
    cm_pct = cm / net_revenue if net_revenue > 0 else None
    cm_pct_gross = cm / gross if gross > 0 else None  # informational only

    cash_out = sum(by_cat.values(), ZERO) - manual_sale_costs  # sale-side costs are netted in payouts
    cash_in = sum((d(r.amount) for r in receipts), ZERO)

    sold_net = max(units_sold - units_returned, 0)
    relieve_base = max(units["qa_approved"], units_sold, 1)
    unit_cost = pre_sale_cost / relieve_base
    inventory_units = max(units["qa_approved"] - units_sold, 0)
    inventory_value = max(pre_sale_cost - unit_cost * units_sold, ZERO)

    robot_cost = by_cat.get("robot_operations", ZERO)
    return {
        # Two lenses on robot cost: committed (all cell hours paid for) vs productive (hours that produced output).
        "robot_cost_incurred": robot_cost,
        "productive_robot_cost_rate": safe_div(robot_cost, hours["productive"], None),
        "idle_capacity_leakage": robot_cost
        - (safe_div(robot_cost, hours["productive"] + hours["downtime"], ZERO) * hours["productive"]),
        "units": units,
        "hours": hours,
        "costs_by_category": by_cat,
        "sales": sales_out,
        "invoices": {"billed": billed, "collected": inv_collected, "outstanding": billed - inv_collected},
        # Reconciliation: CM == net_recognized_revenue - operating_direct_cost
        "gross_sale_proceeds": gross,
        "discounts": discounts,
        "marketplace_fees_total": fees_total,
        "net_recognized_revenue": net_revenue,
        "net_revenue": net_revenue,  # alias
        "operating_direct_cost": cost_total,
        "pre_sale_cost": pre_sale_cost,
        "cost_total": cost_total,
        "revenue": net_revenue,  # alias of net_recognized_revenue
        "contribution_margin": cm,
        "cm_pct": cm_pct,  # CM / net recognised revenue (authoritative); None if net revenue <= 0
        "cm_pct_net_revenue": cm_pct,
        "cm_pct_gross_sale_proceeds": cm_pct_gross,
        "cash_out": cash_out,
        "cash_in": cash_in,
        "cash_exposure": cash_out - cash_in,
        "inventory_units": inventory_units,
        "inventory_value": inventory_value,
        "unit_cost": unit_cost,
        "units_sold_net": sold_net,
        "realized_price": safe_div(gross - discounts - refunds, max(units_sold - units_returned, 0), None)
        if units_sold
        else None,
        "cm_per_robot_hour": safe_div(cm, hours["productive"], None),
        "margin_per_incoming_device": safe_div(cm, D(units["received"]), None) if units["received"] else None,
    }


def cash_conversion(db: Session, cohort_id: str, as_of: date) -> dict:
    """First cash outflow -> date cumulative receipts cover cumulative outflows (or forecast)."""
    costs = list(
        db.scalars(select(CostEntry).where(CostEntry.cohort_id == cohort_id, CostEntry.cost_date <= as_of))
    )
    outflows = sorted((c.cost_date, d(c.amount)) for c in costs if c.cost_category not in SALE_SIDE)
    if not outflows:
        return {"first_outflow": None, "recovery_date": None, "days": None, "forecast_recovery_date": None}
    sales = list(db.scalars(select(SalesTransaction).where(SalesTransaction.cohort_id == cohort_id)))
    invoices = list(db.scalars(select(Invoice).where(Invoice.cohort_id == cohort_id)))
    sale_ids = [s.id for s in sales]
    inv_ids = [i.id for i in invoices]
    rc = []
    if sale_ids:
        rc += list(db.scalars(select(CashReceipt).where(CashReceipt.sales_transaction_id.in_(sale_ids))))
    if inv_ids:
        rc += list(db.scalars(select(CashReceipt).where(CashReceipt.invoice_id.in_(inv_ids))))
    receipts = sorted((r.receipt_date, d(r.amount)) for r in rc if r.receipt_date <= as_of)
    total_out = sum((a for _, a in outflows), ZERO)
    first = outflows[0][0]
    cum, recovery = ZERO, None
    for dt, amt in receipts:
        cum += amt
        if cum >= total_out:
            recovery = dt
            break
    forecast = None
    if recovery is None:
        pending = [
            (s.payout_due_date, d(s.net_sale_amount))
            for s in sales
            if s.payout_date is None and s.payout_due_date
        ]
        pending += [(i.due_date, d(i.amount)) for i in invoices]
        # outstanding = unpaid remainder; order by due date and find when cumulative would cover outflows
        paid = cum
        ordered = sorted(p for p in pending if p[0])
        outstanding_total = _outstanding_total(db, sales, invoices)
        if paid + outstanding_total >= total_out:
            running = paid
            for dt, amt in ordered:
                running += amt
                if running >= total_out:
                    forecast = max(dt, as_of)
                    break
    end = recovery or forecast
    return {
        "first_outflow": first.isoformat(),
        "recovery_date": recovery.isoformat() if recovery else None,
        "forecast_recovery_date": forecast.isoformat() if forecast else None,
        "days": (end - first).days if end else None,
        "total_outflow": float(total_out),
    }


def _outstanding_total(db, sales, invoices) -> Decimal:
    tot = ZERO
    for s in sales:
        tot += max(d(s.net_sale_amount) - _receipts_for(db, sale_id=s.id), ZERO)
    for i in invoices:
        tot += max(d(i.amount) - _receipts_for(db, invoice_id=i.id), ZERO)
    return tot


def _receipts_for(db: Session, *, invoice_id: str | None = None, sale_id: str | None = None) -> Decimal:
    q = select(CashReceipt)
    q = (
        q.where(CashReceipt.invoice_id == invoice_id)
        if invoice_id
        else q.where(CashReceipt.sales_transaction_id == sale_id)
    )
    return sum((d(r.amount) for r in db.scalars(q)), ZERO)


# ---------------------------------------------------------------------------------------------------
# Posting (with validation)
# ---------------------------------------------------------------------------------------------------
def post_sale(db: Session, **f) -> SalesTransaction:
    gross = d(f["gross_sale_amount"])
    discount = d(f.get("discount_amount", 0))
    fee, ship, ret, refund = (
        d(f.get("marketplace_fee", 0)),
        d(f.get("shipping_cost", 0)),
        d(f.get("return_cost", 0)),
        d(f.get("refund_amount", 0)),
    )
    for name, v in (
        ("gross", gross),
        ("fee", fee),
        ("shipping", ship),
        ("return cost", ret),
        ("refund", refund),
        ("discount", discount),
    ):
        if v < 0:
            raise Invalid(f"Sale {name} cannot be negative.")
    if (
        f.get("units_sold", 1) < 0
        or f.get("units_returned", 0) < 0
        or f.get("units_returned", 0) > f.get("units_sold", 1)
    ):
        raise Invalid("Units sold/returned are inconsistent.")
    net = gross - discount - fee - ship - ret - refund  # cash the marketplace pays out
    if net > gross:
        raise Invalid("Net sale proceeds cannot exceed gross sale proceeds.")
    if net < 0:
        raise Invalid("Discounts, fees, shipping, returns and refunds exceed the gross sale amount.")
    sale_date = f.get("sale_date")
    due = f.get("payout_due_date") or (
        sale_date + timedelta(days=f.get("payout_days", 7)) if sale_date else None
    )
    tx = SalesTransaction(
        cohort_id=f["cohort_id"],
        resale_listing_id=f.get("resale_listing_id"),
        sale_date=sale_date,
        units_sold=f.get("units_sold", 1),
        units_returned=f.get("units_returned", 0),
        gross_sale_amount=gross,
        marketplace_fee=fee,
        shipping_cost=ship,
        return_cost=ret,
        refund_amount=refund,
        discount_amount=discount,
        net_sale_amount=net,
        payment_status="pending",
        payout_due_date=due,
    )
    db.add(tx)
    db.flush()
    return tx


def record_return(
    db: Session, sale_id: str, units: int, refund: Decimal, return_cost: Decimal
) -> SalesTransaction:
    """Returns reverse net recovery immediately: refund + return cost come off the sale's net proceeds."""
    tx = db.get(SalesTransaction, sale_id)
    if not tx:
        raise NotFound("Sale")
    if units <= 0 or tx.units_returned + units > tx.units_sold:
        raise Invalid("Returned units exceed units sold.")
    refund, return_cost = d(refund), d(return_cost)
    new_net = d(tx.net_sale_amount) - refund - return_cost
    paid = _receipts_for(db, sale_id=sale_id)
    tx.units_returned += units
    tx.refund_amount = d(tx.refund_amount) + refund
    tx.return_cost = d(tx.return_cost) + return_cost
    tx.net_sale_amount = max(new_net, ZERO)
    if paid > tx.net_sale_amount:
        db.add(
            CashReceipt(
                sales_transaction_id=sale_id,
                receipt_date=today(),
                amount=tx.net_sale_amount - paid,
                receipt_type="adjustment",
                reference_number="return-clawback",
            )
        )
    return tx


def post_invoice(db: Session, **f) -> Invoice:
    if d(f["amount"]) <= 0:
        raise Invalid("Invoice amount must be positive.")
    if f["due_date"] < f["invoice_date"]:
        raise Invalid("Invoice due date cannot precede invoice date.")
    inv = Invoice(
        cohort_id=f.get("cohort_id"),
        partner_id=f["partner_id"],
        invoice_number=f["invoice_number"],
        invoice_date=f["invoice_date"],
        due_date=f["due_date"],
        amount=d(f["amount"]),
        status="sent",
    )
    db.add(inv)
    db.flush()
    return inv


def post_receipt(db: Session, **f) -> CashReceipt:
    amount = d(f["amount"])
    inv_id, sale_id = f.get("invoice_id"), f.get("sales_transaction_id")
    if bool(inv_id) == bool(sale_id):
        raise Invalid("A receipt must reference exactly one of invoice_id or sales_transaction_id.")
    if f.get("receipt_type") != "adjustment" and amount <= 0:
        raise Invalid("Receipt amount must be positive (use an adjustment record to reverse).")
    if inv_id:
        inv = db.get(Invoice, inv_id)
        if not inv:
            raise NotFound("Invoice")
        remaining = d(inv.amount) - _receipts_for(db, invoice_id=inv_id)
        if f.get("receipt_type") != "adjustment" and amount > remaining:
            raise DomainError(
                f"Receipt {float(amount):,.2f} exceeds the invoice's remaining balance {float(remaining):,.2f}.",
                code="overpayment",
            )
    else:
        tx = db.get(SalesTransaction, sale_id)
        if not tx:
            raise NotFound("Sale")
        remaining = d(tx.net_sale_amount) - _receipts_for(db, sale_id=sale_id)
        if f.get("receipt_type") != "adjustment" and amount > remaining:
            raise DomainError(
                f"Payout {float(amount):,.2f} exceeds the sale's remaining net proceeds {float(remaining):,.2f}.",
                code="overpayment",
            )
    r = CashReceipt(
        invoice_id=inv_id,
        sales_transaction_id=sale_id,
        receipt_date=f["receipt_date"],
        amount=amount,
        receipt_type=f.get("receipt_type") or ("invoice_payment" if inv_id else "marketplace_payout"),
        reference_number=f.get("reference_number"),
    )
    db.add(r)
    db.flush()
    if inv_id:
        rem = d(inv.amount) - _receipts_for(db, invoice_id=inv_id)
        inv.status = "paid" if rem <= 0 else "partial"
    else:
        rem = d(tx.net_sale_amount) - _receipts_for(db, sale_id=sale_id)
        if rem <= 0:
            tx.payment_status, tx.payout_date = "paid", f["receipt_date"]
        else:
            tx.payment_status = "partial"
    return r


# ---------------------------------------------------------------------------------------------------
# Collections + inventory aging
# ---------------------------------------------------------------------------------------------------
def _bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    return "60+"


def collections_aging(db: Session, as_of: date) -> dict:
    items = []
    channel_of = {ls.id: ls.listing_channel for ls in db.scalars(select(ResaleListing))}
    for inv in db.scalars(select(Invoice)):
        rem = d(inv.amount) - _receipts_for(db, invoice_id=inv.id)
        if rem > 0:
            od = (as_of - inv.due_date).days
            items.append(
                {
                    "kind": "invoice",
                    "id": inv.id,
                    "ref": inv.invoice_number,
                    "cohort_id": inv.cohort_id,
                    "partner": inv.partner.name,
                    "due_date": inv.due_date.isoformat(),
                    "issued": inv.invoice_date.isoformat(),
                    "channel": None,
                    "days_overdue": od,
                    "bucket": _bucket(od),
                    "remaining": rem,
                    "amount": d(inv.amount),
                }
            )
    for s in db.scalars(select(SalesTransaction)):
        rem = d(s.net_sale_amount) - _receipts_for(db, sale_id=s.id)
        if rem > 0 and s.payout_due_date:
            od = (as_of - s.payout_due_date).days
            items.append(
                {
                    "kind": "payout",
                    "id": s.id,
                    "ref": f"Sale {s.sale_date}",
                    "cohort_id": s.cohort_id,
                    "partner": None,
                    "due_date": s.payout_due_date.isoformat(),
                    "issued": s.sale_date.isoformat() if s.sale_date else None,
                    "channel": channel_of.get(s.resale_listing_id),
                    "days_overdue": od,
                    "bucket": _bucket(od),
                    "remaining": rem,
                    "amount": d(s.net_sale_amount),
                }
            )
    buckets = {b: ZERO for b in ("current", "1-30", "31-60", "60+")}
    for it in items:
        buckets[it["bucket"]] += it["remaining"]
    overdue = sum((it["remaining"] for it in items if it["days_overdue"] > 0), ZERO)
    return {
        "items": sorted(items, key=lambda x: -x["days_overdue"]),
        "buckets": buckets,
        "total_outstanding": sum(buckets.values(), ZERO),
        "total_overdue": overdue,
    }


def inventory_aging(
    db: Session, as_of: date, target_days: int, cohort_ids: set[str] | None = None, channel: str | None = None
) -> dict:
    """Unsold listed devices by age, valued at cohort unit cost."""
    rows, buckets = [], {"0-15": ZERO, "16-30": ZERO, "31-45": ZERO, "45+": ZERO}
    aged_value = ZERO
    for ls in db.scalars(select(ResaleListing).where(ResaleListing.listing_date.is_not(None))):
        if ls.listing_date > as_of:
            continue
        if (cohort_ids is not None and ls.cohort_id not in cohort_ids) or (
            channel and ls.listing_channel != channel
        ):
            continue
        sold = sum(
            (
                s.units_sold - s.units_returned
                for s in db.scalars(
                    select(SalesTransaction).where(SalesTransaction.resale_listing_id == ls.id)
                )
            ),
            0,
        )
        unsold = max(ls.device_count - sold, 0)
        if unsold == 0:
            continue
        act = cohort_actuals(db, ls.cohort_id, as_of)
        value = act["unit_cost"] * unsold
        age = (as_of - ls.listing_date).days
        key = "0-15" if age <= 15 else "16-30" if age <= 30 else "31-45" if age <= 45 else "45+"
        buckets[key] += value
        if age > target_days:
            aged_value += value
        rows.append(
            {
                "listing_id": ls.id,
                "cohort_id": ls.cohort_id,
                "channel": ls.listing_channel,
                "age_days": age,
                "unsold_units": unsold,
                "value": value,
            }
        )
    return {
        "buckets": buckets,
        "aged_value": aged_value,
        "rows": sorted(rows, key=lambda r: -r["age_days"]),
        "total_value": sum(buckets.values(), ZERO),
    }
