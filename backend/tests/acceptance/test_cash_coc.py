"""Test set 6 (resale / cash), Test set 7 (chain of custody and sanitization)."""

import pytest

from tests.acceptance.helpers import advance_cohort, register, schedule_and_receive, to_ready
from tests.conftest import TODAY


# ------------------------------------------------------------------------------------------ helpers
def listed_cohort(client, world, code, n=2):
    c = schedule_and_receive(client, world, code, units=n)
    ids = register(client, c["id"], n, prefix=code)
    ls = client.post(
        "/api/resale-listings",
        json=dict(
            cohort_id=c["id"],
            listing_channel="eBay",
            listing_date="2026-06-12",
            listing_price=300,
            device_count=n,
        ),
    ).json()
    for i in ids:
        to_ready(client, i, cert=f"C-{i[:4]}")
        assert client.post(f"/api/devices/{i}/list", json=dict(resale_listing_id=ls["id"])).status_code == 200
    return c, ids, ls


def sell(client, c, ls, units=1, gross=300):
    r = client.post("/api/sales-transactions", json=dict(cohort_id=c["id"], resale_listing_id=ls["id"], sale_date="2026-06-13", units_sold=units,
                                                         gross_sale_amount=gross, marketplace_fee=gross * 0.1, shipping_cost=10 * units))  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------------------------------ Test set 6
def test_CASH_01_to_04_listing_sale_is_revenue_not_cash_until_payout(client, world):
    c, ids, ls = listed_cohort(client, world, "CS1")
    assert client.get(f"/api/cohorts/{c['id']}").json()["status"] == "listed_for_sale"  # CASH-01
    s = sell(client, c, ls, units=1, gross=300)
    assert s["net_sale_amount"] == 300 - 30 - 10  # CASH-02
    a = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    assert (
        a["net_recognized_revenue"] == 270 and a["gross_sale_proceeds"] == 300 and a["cash_in"] == 0
    )  # CASH-03 (gross 300 - fee 30)
    exposure = a["cash_exposure"]
    client.post(
        "/api/cash-receipts", json=dict(sales_transaction_id=s["id"], receipt_date="2026-06-15", amount=260)
    )
    a2 = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    assert a2["cash_in"] == 260 and abs(a2["cash_exposure"] - (exposure - 260)) < 1e-6  # CASH-04 / CASH-10
    assert client.get("/api/collections/aging").json()["total_outstanding"] == 0


def test_CASH_05_06_partial_receipt_then_overpayment_rejected(client, world):
    c, *_ = listed_cohort(client, world, "CS5")
    inv = client.post(
        "/api/invoices",
        json=dict(
            cohort_id=c["id"],
            partner_id=world["partner"]["id"],
            invoice_number="INV-5",
            invoice_date="2026-06-01",
            due_date="2026-07-01",
            amount=1000,
        ),
    ).json()
    assert (
        client.post(
            "/api/cash-receipts", json=dict(invoice_id=inv["id"], receipt_date="2026-06-10", amount=400)
        ).status_code
        == 201
    )
    row = next(i for i in client.get("/api/invoices").json() if i["id"] == inv["id"])
    assert row["status"] == "partial" and row["remaining"] == 600  # CASH-05
    r = client.post(
        "/api/cash-receipts", json=dict(invoice_id=inv["id"], receipt_date="2026-06-11", amount=600.01)
    )
    assert r.status_code == 409 and r.json()["error"] == "overpayment"  # CASH-06


def test_CASH_08_return_after_sale_reverses_revenue_margin_and_inventory_state(client, world):
    c, ids, ls = listed_cohort(client, world, "CS8")
    s = sell(client, c, ls, units=2, gross=600)
    for i in ids:
        assert (
            client.post(f"/api/devices/{i}/sell", json=dict(sales_transaction_id=s["id"])).status_code == 200
        )
    a0 = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    # a device cannot be marked returned before the return is booked financially
    assert client.post(f"/api/devices/{ids[0]}/return").json()["error"] == "return_not_booked"
    client.post(
        f"/api/sales-transactions/{s['id']}/return", json=dict(units=1, refund_amount=300, return_cost=15)
    )
    assert client.post(f"/api/devices/{ids[0]}/return").status_code == 200
    a1 = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    assert (
        a1["revenue"] == a0["revenue"] - 300
        and abs(a1["contribution_margin"] - (a0["contribution_margin"] - 315)) < 1e-6
    )
    assert client.get(f"/api/cohorts/{c['id']}/device-reconciliation").json()["by_status"]["RETURNED"] == 1


def test_CASH_07_09_seeded_overdue_and_aged_inventory(seeded):
    rows = {r["code"]: r for r in seeded.get("/api/cohorts").json()}
    aging = seeded.get("/api/collections/aging").json()
    ent = [i for i in aging["items"] if i["cohort_id"] == rows["ENT-003"]["id"] and i["days_overdue"] > 0]
    assert sum(i["remaining"] for i in ent) > 100_000  # CASH-07
    types = {a["alert_type"] for a in seeded.get("/api/alerts").json()}
    assert {"OVERDUE_COLLECTION", "AGED_INVENTORY"} <= types  # CASH-07 / CASH-09
    ent_actuals = seeded.get(f"/api/cohorts/{rows['ENT-003']['id']}/actuals").json()
    assert (
        ent_actuals["contribution_margin"] > 0 and ent_actuals["cash_exposure"] > 0
    )  # profitable yet cash-consuming


def test_CASH_11_closeout_succeeds_when_everything_is_sold_paid_and_clean(client, world):
    c, ids, ls = listed_cohort(client, world, "CS11")
    s = sell(client, c, ls, units=2, gross=600)
    for i in ids:
        client.post(f"/api/devices/{i}/sell", json=dict(sales_transaction_id=s["id"]))
    client.post(
        "/api/cash-receipts",
        json=dict(sales_transaction_id=s["id"], receipt_date="2026-06-15", amount=s["net_sale_amount"]),
    )
    chk = client.get(f"/api/cohorts/{c['id']}/closeout-check").json()
    assert chk["ready"] and chk["blockers"] == []
    assert client.post(f"/api/cohorts/{c['id']}/status", json=dict(status="closed")).status_code == 200
    # a closed cohort is frozen
    r = client.post(
        "/api/cost-entries",
        json=dict(cohort_id=c["id"], cost_date="2026-06-15", cost_category="other_direct_cost", amount=5),
    )
    assert r.status_code == 409 and r.json()["error"] == "closed_cohort"


def test_CASH_12_closeout_is_blocked_with_clear_reasons(client, world):
    c, ids, ls = listed_cohort(client, world, "CS12")
    s = sell(client, c, ls, units=1, gross=300)
    client.post(f"/api/devices/{ids[0]}/sell", json=dict(sales_transaction_id=s["id"]))
    r = client.post(f"/api/cohorts/{c['id']}/status", json=dict(status="closed"))
    assert r.status_code == 409 and r.json()["error"] == "closeout_blocked"
    codes = {b["code"] for b in r.json()["details"]["blockers"]}
    assert codes == {"cash_outstanding", "devices_not_disposed"}  # missing payout + unsold device
    # now an open critical alert joins the list
    extra = register(client, c["id"], 1, prefix="QQ")[0]
    for to in ("TRIAGED", "AWAITING_SANITIZATION"):
        client.post(f"/api/devices/{extra}/advance", json=dict(to=to))
    client.post(f"/api/devices/{extra}/sanitization", json=dict(passed=False))
    r = client.post(f"/api/cohorts/{c['id']}/status", json=dict(status="closed"))
    assert r.json()["error"] == "critical_alerts_open" and "critical_alerts_open" in {
        b["code"] for b in r.json()["details"]["blockers"]
    }


# ------------------------------------------------------------------------------------------ Test set 7
def test_COC_01_duplicate_serial_rejected_case_insensitively(client, world):
    c = schedule_and_receive(client, world, "CC1", units=3)
    assert (
        client.post("/api/devices", json=dict(cohort_id=c["id"], serial_number="abc123")).status_code == 201
    )
    r = client.post("/api/devices", json=dict(cohort_id=c["id"], serial_number=" ABC123 "))
    assert r.status_code == 409 and r.json()["error"] == "duplicate_serial"
    bulk = client.post(
        "/api/devices/bulk",
        json=dict(
            cohort_id=c["id"],
            devices=[
                dict(cohort_id=c["id"], serial_number="NEW1"),
                dict(cohort_id=c["id"], serial_number="ABC123"),
            ],
        ),
    )
    assert bulk.status_code == 409
    assert client.get(f"/api/devices?cohort_id={c['id']}&q=NEW1").json() == []  # the batch is all-or-nothing


def test_COC_02_03_intake_record_and_immutable_custody_events(client, world, session_factory):
    c = schedule_and_receive(client, world, "CC2", units=2)
    d = client.post(
        "/api/devices",
        json=dict(
            cohort_id=c["id"],
            serial_number="SER-2",
            asset_tag="AT-2",
            manufacturer="Lenovo",
            model="T480",
            current_location="Dock 1",
            current_custodian="sam",
        ),
    ).json()
    assert d["current_status"] == "RECEIVED" and d["intake_timestamp"]
    client.post(
        f"/api/devices/{d['id']}/transfer", json=dict(location="Bay 2", custodian="priya", note="to cell")
    )
    det = client.get(f"/api/devices/{d['id']}").json()
    assert [e["event_type"] for e in det["custody_events"]] == ["received", "transfer"]
    assert (
        det["current_location"] == "Bay 2" and det["custody_events"][1]["detail"]["from_custodian"] == "sam"
    )
    from sqlalchemy import select

    from app.models import DeviceCustodyEvent

    with session_factory() as s:
        ev = s.scalars(select(DeviceCustodyEvent).where(DeviceCustodyEvent.device_id == d["id"])).first()
        ev.location = "tampered"
        with pytest.raises(ValueError):
            s.commit()
        s.rollback()
        with pytest.raises(ValueError):
            s.delete(
                s.scalars(select(DeviceCustodyEvent).where(DeviceCustodyEvent.device_id == d["id"])).first()
            )
            s.commit()


def test_COC_04_08_nothing_reaches_resale_before_sanitization_and_qc(client, world):
    c, ids, ls = listed_cohort(client, world, "CC4", n=1)
    fresh = client.post("/api/devices", json=dict(cohort_id=c["id"], serial_number="FRESH-1")).json()["id"]
    r = client.post(f"/api/devices/{fresh}/list", json=dict(resale_listing_id=ls["id"]))
    assert r.status_code == 409 and r.json()["error"] == "listing_gate"  # COC-08
    assert any("SANITIZED" in p for p in r.json()["details"]["problems"])
    for bad in ("LISTED", "SOLD", "SANITIZED", "READY_FOR_LISTING"):  # cannot jump the gates via /advance
        assert client.post(f"/api/devices/{fresh}/advance", json=dict(to=bad)).status_code == 409
    client.post(f"/api/devices/{fresh}/advance", json=dict(to="TRIAGED"))
    r = client.post(
        f"/api/devices/{fresh}/advance", json=dict(to="IN_ROBOT_PROCESSING")
    )  # COC-04 robot processing needs sanitization
    assert r.status_code == 409


def test_COC_05_failed_sanitization_quarantines_alerts_and_blocks_everything(client, world):
    c, ids, ls = listed_cohort(client, world, "CC5", n=1)
    bad = client.post("/api/devices", json=dict(cohort_id=c["id"], serial_number="WIPE-FAIL")).json()["id"]
    for to in ("TRIAGED", "AWAITING_SANITIZATION"):
        client.post(f"/api/devices/{bad}/advance", json=dict(to=to))
    out = client.post(
        f"/api/devices/{bad}/sanitization", json=dict(passed=False, method="NIST 800-88 Clear")
    ).json()
    assert out["current_status"] == "QUARANTINED" and out["sanitization_status"] == "FAILED"
    trail = [e["to_status"] for e in out["custody_events"]]
    assert trail[-2:] == ["SANITIZATION_FAILED", "QUARANTINED"]
    crit = [a for a in client.get("/api/alerts").json() if a["alert_type"] == "SANITIZATION_FAILURE"]
    assert len(crit) == 1 and crit[0]["severity"] == "critical"
    client.get("/api/alerts")
    assert (
        len([a for a in client.get("/api/alerts").json() if a["alert_type"] == "SANITIZATION_FAILURE"]) == 1
    )  # no duplicates
    for action, body in (("advance", dict(to="IN_ROBOT_PROCESSING")), ("advance", dict(to="QUALITY_CONTROL")), ("list", dict(resale_listing_id=ls["id"])),
                         ("disposition", dict(disposition="RESALE")), ("sell", dict(sales_transaction_id="x")), ("qc", dict(passed=True))):  # fmt: skip
        assert client.post(f"/api/devices/{bad}/{action}", json=body).status_code in (409, 422), action


def test_COC_06_07_pass_then_qc_fail_blocks_listing(client, world):
    c, ids, ls = listed_cohort(client, world, "CC6", n=1)
    d = client.post("/api/devices", json=dict(cohort_id=c["id"], serial_number="QC-FAIL")).json()["id"]
    for to in ("TRIAGED", "AWAITING_SANITIZATION"):
        client.post(f"/api/devices/{d}/advance", json=dict(to=to))
    assert (
        client.post(
            f"/api/devices/{d}/sanitization", json=dict(passed=True, method="NIST 800-88 Clear")
        ).status_code
        == 422
    )  # certificate required
    assert (
        client.post(
            f"/api/devices/{d}/sanitization", json=dict(passed=True, certificate_id="CERT-9")
        ).status_code
        == 200
    )
    client.post(f"/api/devices/{d}/advance", json=dict(to="QUALITY_CONTROL"))  # COC-06
    assert (
        client.post(f"/api/devices/{d}/qc", json=dict(passed=False, notes="cracked hinge")).json()[
            "current_status"
        ]
        == "MANUAL_REVIEW"
    )
    r = client.post(f"/api/devices/{d}/list", json=dict(resale_listing_id=ls["id"]))
    assert r.status_code == 409 and any(
        "quality_control_status" in p for p in r.json()["details"]["problems"]
    )  # COC-07


def test_COC_09_recycle_and_destroy_need_reason_operator_and_timestamp(client, world):
    c = schedule_and_receive(client, world, "CC9", units=3)
    a, b, _ = register(client, c["id"], 3, prefix="CC9")
    assert client.post(f"/api/devices/{a}/advance", json=dict(to="TRIAGED")).status_code == 200
    assert client.post(f"/api/devices/{a}/disposition", json=dict(disposition="RECYCLE")).status_code == 422
    assert (
        client.post(
            f"/api/devices/{a}/disposition",
            json=dict(disposition="RECYCLE", reason_code="BEYOND_ECONOMIC_REPAIR"),
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/devices/{a}/disposition",
            json=dict(disposition="RECYCLE", reason_code="NOT_A_CODE", operator_ref="sam"),
        ).status_code
        == 422
    )
    out = client.post(
        f"/api/devices/{a}/disposition",
        json=dict(disposition="DESTROY", reason_code="SECURITY_POLICY", operator_ref="sam.k"),
    ).json()
    assert (
        out["current_status"] == "DESTROYED"
        and out["disposition_at"]
        and out["disposition_operator"] == "sam.k"
        and out["disposition_reason_code"] == "SECURITY_POLICY"
    )
    assert (
        client.post(f"/api/devices/{a}/transfer", json=dict(location="x", custodian="y")).status_code == 409
    )  # terminal
    assert client.post(f"/api/devices/{a}/advance", json=dict(to="TRIAGED")).status_code == 409
    assert b


def test_sale_cannot_exceed_unsold_quantity_and_device_must_be_listed(client, world):
    c, ids, ls = listed_cohort(client, world, "CC99", n=2)
    s = sell(client, c, ls, units=1, gross=300)
    assert (
        client.post(f"/api/devices/{ids[0]}/sell", json=dict(sales_transaction_id=s["id"])).status_code == 200
    )
    r = client.post(f"/api/devices/{ids[1]}/sell", json=dict(sales_transaction_id=s["id"]))
    assert r.status_code == 409 and r.json()["error"] == "sale_quantity_exceeded"
    extra = client.post("/api/devices", json=dict(cohort_id=c["id"], serial_number="NOT-LISTED")).json()["id"]
    assert (
        client.post(f"/api/devices/{extra}/sell", json=dict(sales_transaction_id=s["id"])).json()["error"]
        == "not_listed"
    )


def test_listing_capacity_is_enforced(client, world):
    c = schedule_and_receive(client, world, "CC98", units=2)
    ids = register(client, c["id"], 2, prefix="L")
    ls = client.post(
        "/api/resale-listings",
        json=dict(
            cohort_id=c["id"],
            listing_channel="eBay",
            listing_date="2026-06-12",
            listing_price=300,
            device_count=1,
        ),
    ).json()
    for i in ids:
        to_ready(client, i)
    assert (
        client.post(f"/api/devices/{ids[0]}/list", json=dict(resale_listing_id=ls["id"])).status_code == 200
    )
    assert (
        client.post(f"/api/devices/{ids[1]}/list", json=dict(resale_listing_id=ls["id"])).json()["error"]
        == "listing_full"
    )


def test_COC_10_11_reconciliation(client, world):
    c, ids, ls = listed_cohort(client, world, "CC10", n=4)
    s = sell(client, c, ls, units=1)
    client.post(f"/api/devices/{ids[0]}/sell", json=dict(sales_transaction_id=s["id"]))
    client.post(f"/api/devices/{ids[1]}/delist")
    client.post(
        f"/api/devices/{ids[1]}/disposition",
        json=dict(disposition="RECYCLE", reason_code="CONDITION_TOO_POOR", operator_ref="sam"),
    )
    rec = client.get(f"/api/cohorts/{c['id']}/device-reconciliation").json()
    assert (
        rec["registered"] == 4 == rec["received_per_operations"]
        and rec["reconciles"]
        and rec["matches_intake"]
    )
    assert rec["sold"] + rec["recycled"] + rec["destroyed"] + rec["active_inventory"] == rec["registered"]
    assert sum(rec["by_status"].values()) == rec["registered"]  # COC-11
    client.post(
        "/api/devices", json=dict(cohort_id=c["id"], serial_number="EXTRA")
    )  # registering more than were received is flagged
    assert client.get(f"/api/cohorts/{c['id']}/device-reconciliation").json()["matches_intake"] is False


def test_COC_12_audit_export_json_and_csv(client, world):
    c, ids, ls = listed_cohort(client, world, "CC12", n=1)
    s = sell(client, c, ls, units=1)
    client.post(f"/api/devices/{ids[0]}/sell", json=dict(sales_transaction_id=s["id"]))
    j = client.get(f"/api/devices/{ids[0]}/export").json()
    assert (
        j["sanitization"]["status"] == "SANITIZED"
        and j["sanitization"]["certificate_id"]
        and j["quality_control_status"] == "PASSED"
    )
    assert j["sales_transaction_id"] == s["id"] and j["current_status"] == "SOLD"
    types = [e["type"] for e in j["custody_events"]]
    assert (
        types[0] == "received"
        and "sanitization" in types
        and "quality_control" in types
        and types[-1] == "sale"
    )
    assert all(e["at"] for e in j["custody_events"])
    csv_text = client.get(f"/api/devices/{ids[0]}/export?format=csv").text
    assert (
        csv_text.splitlines()[0].startswith("serial,cohort") and len(csv_text.splitlines()) == len(types) + 1
    )


def test_devices_cannot_be_received_into_undecided_cohorts(client, world):
    from tests.conftest import new_cohort

    c = new_cohort(client, world, code="CCX")
    r = client.post("/api/devices", json=dict(cohort_id=c["id"], serial_number="EARLY"))
    assert r.status_code == 409 and r.json()["error"] == "bad_status"
    assert TODAY and advance_cohort
