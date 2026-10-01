"""Seed illustrative demo data. Everything goes through the real services so validation applies.

    python -m app.seed

Story (all synthetic):
  EDU-001   strong cohort, above-plan throughput, strong realized margin
  ITAD-002  exception rate climbs 12% -> 28%; forecast margin falls through the floor; Cell D has an uptime problem
  ENT-003   premium devices sell fast, but payouts/invoices are paid late -> cash exposure
  REC-004   declined at underwriting (negative base CM, poor quality, high exceptions)
  EDU-005   400 retired laptops offered today -> Accept (the user-acceptance scenario)
  ITAD-006  900-laptop follow-on lot -> Review (capacity + partner track record)
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import DEMO_DISCLAIMER, today
from .db import SessionLocal, engine
from .models import (
    Base,
    DeviceCohort,
    DeviceModelProfile,
    ExceptionEvent,
    Partner,
    PartnerContract,
    ResaleListing,
    RobotCell,
)
from .policy import get_policy
from .seed_data import COHORTS, DISCOUNT_RATE, MODELS, PARTNERS, payload
from .services import alerts as alert_svc
from .services import capacity, finance, operations
from .services import cohorts as cohort_svc
from .services.economics import compute
from .services.underwriting import UnderwritingContext

D = Decimal
CELL_HOURS = D(12)


def is_workday(d: date) -> bool:
    return d.weekday() < 5


def add_workdays(d: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    while n != 0:
        d += timedelta(days=step)
        if is_workday(d):
            n -= step
    return d if is_workday(d) else add_workdays(d, 1)


def workdays(start: date, end: date) -> list[date]:
    return [
        start + timedelta(days=i)
        for i in range((end - start).days + 1)
        if is_workday(start + timedelta(days=i))
    ]


def build(db: Session) -> None:
    rnd = random.Random(7)
    T = today()
    policy = get_policy(db)

    # ---- reference data ------------------------------------------------------------------------
    profiles = []
    for mfr, name, age, mins, fpy, exc, rep, price, st in MODELS:
        p = DeviceModelProfile(
            manufacturer=mfr,
            model_name=name,
            typical_age_years=D(str(age)),
            expected_process_minutes=D(mins),
            expected_first_pass_yield_pct=D(str(fpy)),
            expected_exception_rate_pct=D(str(exc)),
            expected_repair_cost=D(rep),
            expected_resale_price=D(price),
            expected_sell_through_pct=D(str(st)),
        )
        db.add(p)
        profiles.append(p)
    db.flush()

    partners = {k: Partner(**v) for k, v in PARTNERS.items()}
    db.add_all(partners.values())
    db.flush()

    def contract(key, name, ctype, own, acq=None, fee=None, terms=30):
        c = PartnerContract(
            partner_id=partners[key].id,
            contract_name=name,
            contract_type=ctype,
            inventory_ownership_model=own,
            start_date=T - timedelta(days=200),
            end_date=T + timedelta(days=330),
            acquisition_cost_per_unit=acq,
            service_fee_per_unit=fee,
            expected_monthly_volume=PARTNERS[key]["expected_monthly_volume"],
            payment_terms_days=terms,
            status="active",
            document_ref=f"SYN-{name[:3].upper()}-2026",
        )
        db.add(c)
        db.flush()
        return c

    contracts = {
        "edu": contract(
            "edu", "Cascadia device buy-out", "supply_purchase", "operator_owned", acq=70, terms=14
        ),
        "itad": contract("itad", "Meridian mixed-lot purchase", "supply_purchase", "operator_owned", acq=31),
        "ent": contract(
            "ent", "Northbridge refresh + secure wipe", "hybrid", "operator_owned", acq=210, fee=14, terms=60
        ),
        "rec": contract("rec", "GreenLoop bulk intake", "supply_purchase", "operator_owned", acq=28),
    }

    cells = {}
    for nm, loc, status in (
        ("Cell A", "Bay 1", "available"),
        ("Cell B", "Bay 1", "available"),
        ("Cell C", "Bay 2", "available"),
        ("Cell D", "Bay 2", "available"),
        ("Cell E", "Bay 3", "maintenance"),
    ):
        c = RobotCell(
            cell_name=nm,
            location=loc,
            status=status,
            standard_daily_capacity_hours=CELL_HOURS,
            standard_daily_capacity_units=22,
            supported_device_categories=["laptop"],
            supported_workflows=["test", "wipe", "photograph", "list"],
        )
        db.add(c)
        cells[nm[-1]] = c
    db.flush()
    for c in cells.values():
        capacity.ensure_calendar(db, c, T - timedelta(days=110), T + timedelta(days=60), CELL_HOURS)
    # planned maintenance
    for r in capacity.calendar_rows(db, T - timedelta(days=5), T + timedelta(days=30), [cells["E"].id]):
        r.maintenance_hours = CELL_HOURS
        capacity.refresh_available(r)
    for r in capacity.calendar_rows(db, T + timedelta(days=3), T + timedelta(days=3), [cells["D"].id]):
        r.maintenance_hours = D(4)
        capacity.refresh_available(r)
    db.flush()

    # ---- cohorts -------------------------------------------------------------------------------
    def make(code, intake, target, contract_key, exposure, decision_ctx=True):
        spec = COHORTS[code]
        partner = partners[spec["partner"]]
        ct = contracts.get(contract_key)
        c = DeviceCohort(
            partner_id=partner.id,
            contract_id=ct.id if ct else None,
            cohort_code=code,
            cohort_name=spec["name"],
            intake_date=intake,
            target_completion_date=target,
            target_sale_date=target + timedelta(days=45),
            expected_device_count=sum(ln["units"] for ln in spec["lines"]),
            ownership_model=spec["ownership"],
            status="draft",
        )
        db.add(c)
        db.flush()
        # link lines to device model profiles for model-level analytics
        payload_lines = []
        for ln in spec["lines"]:
            payload_lines.append(
                {
                    **{k: v for k, v in ln.items() if k != "_idx"},
                    "device_model_profile_id": profiles[ln["_idx"]].id,
                }
            )
        pl = payload(code)
        pl["lines"] = payload_lines
        a = cohort_svc.resolve_assumptions(pl, ct, partner, policy)
        cohort_svc.add_budget_version(db, c, a, created_by="demo.analyst")
        ctx = None
        if decision_ctx:  # point-in-time context for historical decisions
            ctx = UnderwritingContext(
                wipe_level=partner.data_security_requirement,
                capacity_hours_available=D(99999),
                review_capacity_available=D(99999),
                portfolio_cash_exposure=D(exposure),
            )
        cohort_svc.run_underwriting(db, c, policy, persist=True, ctx=ctx)
        return c, a, ctx

    def approve(c, ctx, who, why, override=False):
        cohort_svc.approve(db, c, approver=who, rationale=why, policy=policy, override=override, ctx=ctx)

    def schedule(c, a, cell_keys, start, end=None):
        econ = compute(a)
        per_cell = econ["scheduled_robot_hours"] / len(cell_keys)
        days_needed = math.ceil(float(per_cell / CELL_HOURS * D("1.08"))) + 1
        end = end or add_workdays(start, days_needed)
        units_each = int(econ["units_processed"]) // len(cell_keys)
        for k in cell_keys:
            capacity.schedule_assignment(
                db, c, cells[k], start, end, per_cell.quantize(D("0.01")), units_each
            )
        return end

    def run_ops(
        c,
        a,
        cell_keys,
        start,
        *,
        total,
        minutes,
        util,
        rate,
        rework,
        repair,
        downtime,
        batches,
        end_limit=None,
        model_weights=None,
        tech_minutes=38,
        dt_reason=None,
        wipe_open=False,
    ):
        """Simulate one operating day per workday per cell. Returns day list."""
        started_total, received_total, day = 0, 0, start
        rows = []
        lines = c and list(a.lines)
        end_limit = end_limit or T
        while started_total < total and day <= end_limit:
            if is_workday(day):
                for ck in cell_keys:
                    remaining = total - started_total
                    if remaining <= 0:
                        break
                    u = util(ck, day)
                    dt = downtime(ck, day)
                    prod = float(CELL_HOURS) * u
                    mins = minutes(day) * (1 + rnd.uniform(-0.04, 0.04))
                    s = min(int(prod * 60 / mins), remaining)
                    if s <= 0:
                        continue
                    frac = started_total / total
                    e = min(round(s * max(rate(frac, day) + rnd.uniform(-0.012, 0.012), 0)), s)
                    r = round(s * rework)
                    recv = batches.get(day, 0) if ck == cell_keys[0] else 0
                    scrap = round(e * 0.12)
                    fp = max(s - e - r, 0)
                    done = s - scrap
                    row = operations.post_daily(
                        db,
                        dict(
                            cohort_id=c.id,
                            robot_cell_id=cells[ck].id,
                            operation_date=day,
                            devices_received=recv,
                            devices_started=s,
                            devices_completed=done,
                            first_pass_completions=min(fp, done),
                            manual_exception_count=e,
                            devices_under_repair=round(e * 0.3),
                            quality_approved_count=done,
                            rework_events=r,
                            productive_robot_hours=D(str(round(s * mins / 60, 2))),
                            downtime_hours=D(str(round(dt, 2))),
                            downtime_reason=dt_reason(ck, day)
                            if dt_reason and dt > 1
                            else ("Routine tool change" if dt > 0.5 else None),
                            technician_hours=D(str(round(e * tech_minutes / 60 * rnd.uniform(0.9, 1.1), 2))),
                            repair_parts_cost=D(str(round(e * repair(frac) * rnd.uniform(0.92, 1.08), 2))),
                            notes=None,
                        ),
                        policy,
                    )
                    started_total += s
                    received_total += recv
                    rows.append((row, e, day, ck))
                    _exception_events(c, lines, e, day, cells[ck], frac, day >= T - timedelta(days=2))
            day += timedelta(days=1)
        return rows, started_total

    type_mix = {
        "device_identification": 0.10,
        "hardware_failure": 0.26,
        "software_failure": 0.14,
        "data_wipe_failure": 0.03,
        "robotic_handling": 0.14,
        "vision_classification": 0.10,
        "network_or_os": 0.10,
        "quality_control": 0.13,
    }

    def _exception_events(c, lines, n, day, cell, frac, recent):
        if n <= 0:
            return
        wts = [ln.units * float(ln.exception_rate) for ln in lines]
        for _ in range(n):
            ln = rnd.choices(lines, wts)[0]
            mix = dict(type_mix)
            if c.cohort_code == "ITAD-002" and frac > 0.35:
                mix["hardware_failure"] = 0.44  # swollen batteries / failing SSDs in the C-grade lots
            typ = rnd.choices(list(mix), list(mix.values()))[0]
            if typ == "data_wipe_failure":
                typ = "software_failure"  # the one open wipe failure is injected explicitly
            cost = float(ln.repair_cost) * rnd.uniform(0.6, 1.3)
            opened = rnd.random()
            db.add(
                ExceptionEvent(
                    cohort_id=c.id,
                    device_model_profile_id=ln.device_model_profile_id,
                    robot_cell_id=cell.id,
                    exception_type=typ,
                    severity="warning",
                    status="open" if recent and opened < 0.5 else "resolved",
                    requires_manual_review=True,
                    opened_at=datetime.combine(
                        day, time(10, 0)
                    ),  # the operating day it belongs to, not the wall clock
                    estimated_resolution_cost=D(str(round(cost, 2))),
                    actual_resolution_cost=None
                    if recent and opened < 0.5
                    else D(str(round(cost * rnd.uniform(0.9, 1.15), 2))),
                )
            )

    def list_and_sell(
        c,
        *,
        list_days,
        sell_start,
        sell_end,
        sold_frac,
        price_mult,
        avg_price,
        fee_rate,
        ship,
        ret_rate,
        pay_days,
        paid_share,
        late_after=None,
        channel="eBay Refurbished",
        every=2,
    ):
        from .services.finance import cohort_actuals

        act = cohort_actuals(db, c.id, T)
        qa_units = act["units"]["qa_approved"]
        cohort_svc.transition(db, c, "quality_control")
        cohort_svc.transition(db, c, "listed_for_sale")
        listings = []
        left = qa_units
        for i, (ld, share) in enumerate(list_days):
            n = left if i == len(list_days) - 1 else int(qa_units * share)
            left -= n
            ls = ResaleListing(
                cohort_id=c.id,
                listing_channel=channel,
                listing_date=ld,
                listing_price=D(str(round(avg_price, 2))),
                status="active",
                device_count=n,
            )
            db.add(ls)
            listings.append(ls)
        db.flush()
        target_units = int(qa_units * sold_frac)
        sold, sales = 0, []
        days = [d for d in workdays(sell_start, sell_end)][::every] or [sell_start]
        per_day = max(target_units // len(days), 1)
        for d in days:
            if sold >= target_units:
                break
            units = min(int(per_day * rnd.uniform(0.85, 1.15)), target_units - sold)
            if d == days[-1]:
                units = target_units - sold
            price = avg_price * price_mult * rnd.uniform(0.97, 1.03)
            gross = D(str(round(units * price, 2)))
            returned = int(round(units * ret_rate * rnd.uniform(0.6, 1.4)))
            ls = next((x for x in listings if x.listing_date <= d and x.device_count > 0), listings[0])
            tx = finance.post_sale(
                db,
                cohort_id=c.id,
                resale_listing_id=ls.id,
                sale_date=d,
                units_sold=units,
                units_returned=returned,
                gross_sale_amount=gross,
                discount_amount=(gross * D(str(DISCOUNT_RATE))).quantize(D("0.01")),
                marketplace_fee=(gross * D(str(fee_rate))).quantize(D("0.01")),
                shipping_cost=D(str(round(units * ship * (1 + ret_rate), 2))),
                refund_amount=D(str(round(returned * price, 2))),
                return_cost=D(str(round(returned * ship, 2))),
                payout_days=pay_days,
            )
            sold += units
            sales.append(tx)
        cohort_svc.transition(db, c, "partially_sold")
        db.flush()
        paid_n = int(round(len(sales) * paid_share))
        for i, tx in enumerate(sales):
            if i < paid_n:
                lag = rnd.randint(0, 2)
                rd = tx.payout_due_date + timedelta(days=lag)
                if rd <= T:
                    finance.post_receipt(
                        db,
                        sales_transaction_id=tx.id,
                        receipt_date=rd,
                        amount=tx.net_sale_amount,
                        receipt_type="marketplace_payout",
                        reference_number=f"PO-{tx.id[:6].upper()}",
                    )
        return sales

    # =========================== EDU-001 : the good cohort ===========================================
    s_edu = add_workdays(T, -24)
    c_edu, a_edu, ctx = make("EDU-001", s_edu, add_workdays(T, -8), "edu", 0)
    approve(
        c_edu,
        ctx,
        "Maya Chen (Finance)",
        "Base CM on net recognized revenue is well above the accept line and the downside case clears the risk floor; clean mix of B-grade business laptops. Prioritise for robot time.",
    )
    schedule(c_edu, a_edu, ["A", "B", "C"], s_edu, add_workdays(T, -9))
    run_ops(
        c_edu,
        a_edu,
        ["A", "B", "C"],
        s_edu,
        total=500,
        minutes=lambda d: 29.4,
        util=lambda ck, d: rnd.uniform(0.80, 0.88),
        rate=lambda f, d: 0.095,
        rework=0.018,
        repair=lambda f: 54,
        downtime=lambda ck, d: rnd.uniform(0.3, 0.9),
        batches={s_edu: 250, add_workdays(s_edu, 3): 250},
    )
    avg_edu = float(compute(a_edu)["avg_resale_price"])
    list_and_sell(
        c_edu,
        list_days=[(add_workdays(T, -12), 0.5), (add_workdays(T, -10), 0.5)],
        sell_start=add_workdays(T, -11),
        sell_end=T - timedelta(days=1),
        sold_frac=0.90,
        price_mult=1.035,
        avg_price=avg_edu,
        fee_rate=0.12,
        ship=13,
        ret_rate=0.035,
        pay_days=7,
        paid_share=1.0,
        channel="eBay Refurbished",
        every=1,
    )

    seed_devices(db, c_edu, a_edu.lines, s_edu)

    # =========================== ENT-003 : great devices, slow cash ==================================
    s_ent = add_workdays(T, -75)
    c_ent, a_ent, ctx_ent = make(
        "ENT-003", s_ent, add_workdays(T, -58), "ent", 150000
    )  # portfolio was heavily exposed at review time
    approve(
        c_ent,
        ctx_ent,
        "Maya Chen (Finance)",
        "Premium devices with a strong base and downside margin. Flagged Review for working-capital headroom; approved with a 30-day payout covenant on the bulk buyer.",
    )
    schedule(c_ent, a_ent, ["C", "D"], s_ent)
    run_ops(
        c_ent,
        a_ent,
        ["C", "D"],
        s_ent,
        total=300,
        minutes=lambda d: 31.5,
        util=lambda ck, d: rnd.uniform(0.78, 0.86),
        rate=lambda f, d: 0.075,
        rework=0.02,
        repair=lambda f: 47,
        downtime=lambda ck, d: rnd.uniform(0.3, 1.0),
        batches={s_ent: 300},
        end_limit=T,
    )
    avg_ent = float(compute(a_ent)["avg_resale_price"])
    sales = list_and_sell(
        c_ent,
        list_days=[(add_workdays(T, -65), 1.0)],
        sell_start=add_workdays(T, -64),
        sell_end=add_workdays(T, -38),
        sold_frac=0.94,
        price_mult=1.02,
        avg_price=avg_ent,
        fee_rate=0.10,
        ship=18,
        ret_rate=0.025,
        pay_days=30,
        paid_share=0.3,
        channel="Atlas Wholesale (B2B)",
        every=2,
    )
    inv = finance.post_invoice(
        db,
        cohort_id=c_ent.id,
        partner_id=partners["ent"].id,
        invoice_number="INV-NBF-0417",
        invoice_date=T - timedelta(days=72),
        due_date=T - timedelta(days=12),
        amount=D(300 * 14),
    )
    _ = (sales, inv)
    seed_devices(db, c_ent, a_ent.lines, s_ent)

    # =========================== REC-004 : declined ===================================================
    c_rec, a_rec, ctx_rec = make("REC-004", add_workdays(T, -6), add_workdays(T, 12), "rec", 40000)
    cohort_svc.decline(
        db,
        c_rec,
        approver="Maya Chen (Finance)",
        policy=policy,
        ctx=ctx_rec,
        rationale="Negative base CM, condition-weighted quality index 0.24 vs 0.45 minimum, 35% expected exceptions. "
        "Route to certified recycling; re-offer only at <=$0 acquisition with a per-unit processing fee.",
    )

    # =========================== ITAD-002 : in-flight, degrading ======================================
    s_itad = add_workdays(T, -8)
    c_itad, a_itad, ctx_itad = make("ITAD-002", s_itad, add_workdays(T, 10), "itad", 35000)
    approve(
        c_itad,
        ctx_itad,
        "Maya Chen (Finance)",
        "Approved from Review: base CM clears the accept line but the downside case is below the risk floor and first-pass yield is below standard. Run on supervised cells, cap exposure, and review the exception mix weekly.",
    )
    schedule(c_itad, a_itad, ["A", "C", "D"], s_itad, add_workdays(T, 9))

    def itad_rate(f, d):  # 12% at start -> 28% now, driven by C-grade lots
        return 0.12 + 0.17 * min(f / 0.6, 1.0) ** 1.4

    def itad_util(ck, d):
        if ck == "D" and d >= add_workdays(T, -4):  # Cell D fault: five consecutive low-utilization days
            return rnd.uniform(0.42, 0.55)
        return rnd.uniform(0.76, 0.86)

    def itad_dt(ck, d):
        if ck == "D" and d >= add_workdays(T, -4):
            return rnd.uniform(3.6, 5.0)
        return rnd.uniform(0.4, 1.0)

    run_ops(
        c_itad,
        a_itad,
        ["A", "C", "D"],
        s_itad,
        total=470,
        minutes=lambda d: 34.5 + 0.0,
        util=itad_util,
        rate=itad_rate,
        rework=0.08,
        repair=lambda f: 76 + 78 * min(f / 0.6, 1.0),
        downtime=itad_dt,
        batches={s_itad: 400, add_workdays(T, -3): 350},
        dt_reason=lambda ck, d: "Gripper calibration fault" if ck == "D" else "Vision camera recalibration",
    )
    # first listed batch + early sales at a lower realised price than plan
    from .services.finance import cohort_actuals as _act

    qa = _act(db, c_itad.id, T)["units"]["qa_approved"]
    # first finished devices are listed while the rest of the cohort is still in the robot cells
    ls = ResaleListing(
        cohort_id=c_itad.id,
        listing_channel="eBay Refurbished",
        listing_date=add_workdays(T, -4),
        listing_price=D(198),
        status="active",
        device_count=min(260, qa),
    )
    db.add(ls)
    db.flush()
    avg_itad = float(compute(a_itad)["avg_resale_price"])
    for d, u in ((add_workdays(T, -3), 34), (add_workdays(T, -2), 38), (add_workdays(T, -1), 29)):
        gross = D(str(round(u * avg_itad * 0.90, 2)))
        finance.post_sale(
            db,
            cohort_id=c_itad.id,
            resale_listing_id=ls.id,
            sale_date=d,
            units_sold=u,
            units_returned=2,
            gross_sale_amount=gross,
            discount_amount=(gross * D(str(DISCOUNT_RATE))).quantize(D("0.01")),
            marketplace_fee=(gross * D("0.12")).quantize(D("0.01")),
            shipping_cost=D(str(round(u * 14 * 1.05, 2))),
            refund_amount=D(str(round(2 * avg_itad * 0.90, 2))),
            return_cost=D(28),
            payout_days=7,
        )
    db.add(
        ExceptionEvent(
            cohort_id=c_itad.id,
            device_model_profile_id=profiles[0].id,
            robot_cell_id=cells["C"].id,
            exception_type="data_wipe_failure",
            severity="critical",
            status="open",
            requires_manual_review=True,
            opened_at=datetime.combine(T - timedelta(days=2), time(10, 0)),
            estimated_resolution_cost=D(60),
            resolution_notes="Secure-erase verification failed on 1 device -- quarantined pending re-wipe.",
        )
    )

    seed_devices(db, c_itad, a_itad.lines, s_itad, quarantine_one=True)

    # =========================== pending decisions (live) ============================================
    c_e5, a_e5, _ = make("EDU-005", add_workdays(T, 3), add_workdays(T, 17), "edu", 0, decision_ctx=False)
    c_i6, a_i6, _ = make("ITAD-006", add_workdays(T, 3), add_workdays(T, 12), "itad", 0, decision_ctx=False)
    db.flush()

    # evaluate alerts once the world is built
    alert_svc.evaluate(db, T, policy)
    db.commit()


def seed_devices(db, c, cohort_lines, intake, *, quarantine_one: bool = False) -> None:
    """Serial-level ledger whose counts reconcile exactly with operations + sales already recorded for the cohort."""
    from datetime import datetime, time

    from sqlalchemy import select as _select

    from .models import Device, DeviceCustodyEvent, SalesTransaction
    from .services.finance import cohort_actuals

    rnd = random.Random(hash(c.cohort_code) % 10_000)
    T = today()
    act = cohort_actuals(db, c.id, T)
    R, S, Q = act["units"]["received"], act["units"]["started"], act["units"]["qa_approved"]
    listings = list(
        db.scalars(
            _select(ResaleListing).where(ResaleListing.cohort_id == c.id).order_by(ResaleListing.listing_date)
        )
    )
    sales = list(
        db.scalars(
            _select(SalesTransaction)
            .where(SalesTransaction.cohort_id == c.id)
            .order_by(SalesTransaction.sale_date)
        )
    )
    L = sum(x.device_count for x in listings)
    lines = [ln for ln in cohort_lines for _ in range(1)]
    weights = [ln.units for ln in lines]
    base_ts = datetime.combine(intake, time(9, 0))

    plan: list[dict] = []  # (status, listing_id, sale_id) per device in intake order
    for tx in sales:
        for k in range(tx.units_sold):
            plan.append(
                {
                    "status": "RETURNED" if k < tx.units_returned else "SOLD",
                    "listing": tx.resale_listing_id,
                    "sale": tx.id,
                }
            )
    remaining_cap = {
        ls.id: ls.device_count - sum(1 for p_ in plan if p_["listing"] == ls.id) for ls in listings
    }
    for ls in listings:
        for _ in range(max(remaining_cap[ls.id], 0)):
            plan.append({"status": "LISTED", "listing": ls.id, "sale": None})
    plan += [{"status": "READY_FOR_LISTING", "listing": None, "sale": None} for _ in range(max(Q - L, 0))]
    plan += [{"status": "RECYCLED", "listing": None, "sale": None} for _ in range(max(S - Q, 0))]
    n_wait = max(R - len(plan), 0)
    plan += [{"status": "AWAITING_SANITIZATION", "listing": None, "sale": None} for _ in range(n_wait)]
    plan = plan[:R]
    if quarantine_one and plan and plan[-1]["status"] == "AWAITING_SANITIZATION":
        plan[-1]["status"] = "QUARANTINED"

    devices, events = [], []
    for i, pl in enumerate(plan, start=1):
        ln = rnd.choices(lines, weights)[0]
        st = pl["status"]
        serial = f"{c.cohort_code.replace('-', '')}{i:05d}"
        sanitized = st not in ("AWAITING_SANITIZATION", "QUARANTINED")
        ts = base_ts
        d = Device(
            cohort_id=c.id, serial_number=serial, asset_tag=f"AT-{serial[-6:]}", manufacturer=ln.model_name.split()[0],
            model=" ".join(ln.model_name.split()[1:]), storage_type="ssd", intake_condition_grade=ln.condition_grade, intake_timestamp=ts,
            current_location="Fulfilment" if st in ("SOLD", "RETURNED") else "Receiving dock" if st in ("AWAITING_SANITIZATION", "QUARANTINED") else "Inventory",
            current_custodian="intake.operator" if not sanitized else "ops.team", current_status=st, data_sanitization_required=True,
            sanitization_method="NIST 800-88 Clear (software overwrite)" if sanitized or st == "QUARANTINED" else None,
            sanitization_status="SANITIZED" if sanitized else "FAILED" if st == "QUARANTINED" else "PENDING",
            sanitization_completed_at=ts if sanitized or st == "QUARANTINED" else None,
            sanitization_certificate_id=f"CERT-{serial}" if sanitized else None,
            quality_control_status="FAILED" if st == "RECYCLED" else "PASSED" if st in ("READY_FOR_LISTING", "LISTED", "SOLD", "RETURNED") else "PENDING",
            final_disposition="RECYCLE" if st == "RECYCLED" else "RESALE" if st in ("READY_FOR_LISTING", "LISTED", "SOLD", "RETURNED") else "PENDING",
            disposition_reason_code="BEYOND_ECONOMIC_REPAIR" if st == "RECYCLED" else None,
            disposition_operator="tech.lead" if st == "RECYCLED" else None, disposition_at=ts if st == "RECYCLED" else None,
            resale_listing_id=pl["listing"], sales_transaction_id=pl["sale"],
        )  # fmt: skip
        devices.append(d)
    db.add_all(devices)
    db.flush()
    for d in devices:
        ev = [("received", None, "RECEIVED")]
        if d.sanitization_status == "SANITIZED":
            ev += [("sanitization", "AWAITING_SANITIZATION", "SANITIZED")]
        if d.current_status == "QUARANTINED":
            ev += [
                ("sanitization", "AWAITING_SANITIZATION", "SANITIZATION_FAILED"),
                ("status_change", "SANITIZATION_FAILED", "QUARANTINED"),
            ]
        if d.quality_control_status in ("PASSED", "FAILED"):
            ev += [
                (
                    "quality_control",
                    "QUALITY_CONTROL",
                    "READY_FOR_LISTING" if d.quality_control_status == "PASSED" else "MANUAL_REVIEW",
                )
            ]
        if d.current_status == "RECYCLED":
            ev += [("disposition", "MANUAL_REVIEW", "RECYCLED")]
        if d.resale_listing_id:
            ev += [("listing", "READY_FOR_LISTING", "LISTED")]
        if d.current_status in ("SOLD", "RETURNED"):
            ev += [("sale", "LISTED", "SOLD")]
        if d.current_status == "RETURNED":
            ev += [("return", "SOLD", "RETURNED")]
        for t, f, to in ev:
            events.append(DeviceCustodyEvent(device_id=d.id, event_type=t, from_status=f, to_status=to, location=d.current_location,
                                             custodian=d.current_custodian, occurred_at=d.intake_timestamp, detail=None))  # fmt: skip
    db.add_all(events)
    db.flush()


def reset_and_seed() -> None:
    """Drop and recreate every table, then load the synthetic dataset.

    On PostgreSQL, DROP TABLE must wait for any session still touching those tables (e.g. in-flight requests from a browser
    that has just finished a test), so the DDL runs with a bounded lock timeout and the caller may retry."""
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
    with SessionLocal() as db:
        build(db)


if __name__ == "__main__":
    reset_and_seed()
    print("Seeded illustrative demo data.\n" + DEMO_DISCLAIMER)
