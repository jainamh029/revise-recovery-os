"""Test set 8 (dashboard reconciliation), Test set 9 (roles), Test set 1 (smoke / contract)."""

from datetime import date, timedelta

from tests.conftest import approved_cohort, new_cohort

TODAY = date(2026, 9, 30)  # the `seeded` fixture freezes "today" to this date
ACTIVE = {
    "in_intake",
    "in_processing",
    "exception_review",
    "quality_control",
    "listed_for_sale",
    "partially_sold",
}


def cohorts(c):
    return c.get("/api/cohorts").json()


# ---------------------------------------------------------------------------------------- ENV
def test_ENV_01_health_reports_database_status(client):
    h = client.get("/health")
    assert (
        h.status_code == 200
        and h.json()["status"] == "ok"
        and h.json()["database"] == "ok"
        and h.json()["dialect"] in ("sqlite", "postgresql")
    )
    assert "Illustrative demo data only" in h.json()["disclaimer"]


def test_ENV_07_api_contract_no_failures_on_primary_views(seeded):
    ids = {c["code"]: c["id"] for c in cohorts(seeded)}
    urls = ["/health", "/api/meta", "/api/dashboard/executive", "/api/cohorts", "/api/capacity/calendar", "/api/capacity/queue", "/api/alerts",
            "/api/analytics/robot-performance", "/api/analytics/partner-scorecards", "/api/analytics/model-burden", "/api/analytics/inventory-aging",
            "/api/collections/aging", "/api/policy", "/api/devices?limit=5", "/api/export/cohorts.csv", "/api/export/collections.csv"]  # fmt: skip
    for cid in ids.values():
        urls += [f"/api/cohorts/{cid}", f"/api/cohorts/{cid}/underwriting", f"/api/cohorts/{cid}/performance", f"/api/cohorts/{cid}/memo",
                 f"/api/cohorts/{cid}/device-reconciliation", f"/api/cohorts/{cid}/closeout-check", f"/api/cohorts/{cid}/audit"]  # fmt: skip
    bad = [(u, seeded.get(u).status_code) for u in urls if seeded.get(u).status_code != 200]
    assert not bad, bad
    assert (
        seeded.post(f"/api/cohorts/{ids['EDU-005']}/scenario", json=dict(price_mult=0.9)).status_code == 200
    )


# ---------------------------------------------------------------------------------------- Test set 8
def test_reconciliation_identity_on_every_seeded_cohort(seeded):
    for c in cohorts(seeded):
        if c["status"] in ACTIVE:
            a = seeded.get(f"/api/cohorts/{c['id']}/actuals").json()
            assert abs(a["contribution_margin"] - (a["net_revenue"] - a["operating_direct_cost"])) < 0.005, c[
                "code"
            ]
            assert abs(a["contribution_margin"] - (a["revenue"] - a["cost_total"])) < 0.005, c["code"]


def test_DASH_01_executive_margin_equals_sum_of_cohort_margins(seeded):
    d = seeded.get("/api/dashboard/executive").json()
    rows = [c for c in cohorts(seeded) if c["status"] in ACTIVE]
    assert abs(d["actual_cm_to_date"] - sum(c["actual_cm"] for c in rows)) < 0.01
    assert abs(d["forecast_cm"] - sum(c["forecast_cm"] for c in rows)) < 0.01
    assert abs(d["plan_cm"] - sum(c["plan_cm"] for c in rows)) < 0.01
    assert abs(d["cash_tied_up_inventory"] - sum(c["inventory_value"] for c in rows)) < 0.01


def test_DASH_02_aged_inventory_equals_unsold_listings_beyond_target(seeded):
    d = seeded.get("/api/dashboard/executive").json()
    inv = seeded.get("/api/analytics/inventory-aging").json()
    target = seeded.get("/api/policy").json()["target_days_to_sale"]
    assert (
        abs(d["aged_inventory_value"] - sum(r["value"] for r in inv["rows"] if r["age_days"] > target)) < 0.01
    )
    assert abs(sum(inv["buckets"].values()) - sum(r["value"] for r in inv["rows"])) < 0.01


def test_DASH_03_overdue_receivables_equal_outstanding_past_due(seeded):
    d = seeded.get("/api/dashboard/executive").json()
    a = seeded.get("/api/collections/aging").json()
    invoices = seeded.get("/api/invoices").json()
    over_inv = sum(i["remaining"] for i in invoices if date.fromisoformat(i["due_date"]) < TODAY)
    over_items = sum(i["remaining"] for i in a["items"] if i["days_overdue"] > 0)
    assert abs(d["overdue_collections"] - over_items) < 0.01 and abs(a["total_overdue"] - over_items) < 0.01
    assert over_items >= over_inv > 0
    assert abs(sum(a["buckets"].values()) - a["total_outstanding"]) < 0.01


def test_DASH_04_utilization_is_aggregate_productive_over_aggregate_available(seeded):
    d = seeded.get("/api/dashboard/executive").json()
    start, end = TODAY - timedelta(days=13), TODAY
    cal = seeded.get(f"/api/capacity/calendar?start={start}&end={end}").json()
    ops = seeded.get("/api/operations/daily?limit=5000").json()
    avail_cells = {c["id"] for c in cal["cells"] if c["status"] == "available"}
    avail = sum(
        float(x["scheduled"]) - float(x["maintenance"])
        for c in cal["cells"]
        if c["id"] in avail_cells
        for x in c["days"]
    )
    prod = sum(
        float(o["productive_robot_hours"])
        for o in ops
        if o["robot_cell_id"] in avail_cells and start <= date.fromisoformat(o["operation_date"]) <= end
    )
    assert abs(d["utilization"] - prod / avail) < 1e-4


def test_DASH_05_partner_score_is_the_documented_formula_not_a_hardcoded_order(seeded):
    cards = [p for p in seeded.get("/api/analytics/partner-scorecards").json() if p["score"] is not None]
    assert len(cards) >= 3
    for p in cards:
        assert abs(p["score"] - sum(p["components"][k] * p["weights"][k] for k in p["weights"])) < 0.15
    by_margin = sorted(cards, key=lambda p: -p["metrics"]["forecast_cm_pct"])
    top = seeded.get("/api/dashboard/executive").json()["top_partners"]
    assert [t["partner_id"] for t in top] == [p["partner_id"] for p in by_margin[: len(top)]]


def test_DASH_06_alert_cards_equal_unresolved_alert_counts(seeded):
    d = seeded.get("/api/dashboard/executive").json()
    open_alerts = [a for a in seeded.get("/api/alerts").json() if a["status"] in ("open", "escalated")]
    assert d["alert_counts"]["critical"] == len([a for a in open_alerts if a["severity"] == "critical"])
    assert d["alert_counts"]["warning"] == len([a for a in open_alerts if a["severity"] == "warning"])


# DASH-07 (filters) is implemented and tested in tests/test_dashboard_filters.py.


# DASH-08 (drill-downs) is implemented and tested in tests/test_drilldowns.py and frontend/e2e/drilldowns.spec.ts.


def test_DASH_09_scenarios_never_overwrite_the_approved_budget(seeded):
    cid = next(c["id"] for c in cohorts(seeded) if c["code"] == "ENT-003")
    before = seeded.get(f"/api/cohorts/{cid}/budget-versions").json()
    for body in (dict(price_mult=0.5), dict(exception_mult=3, uptime=0.4), dict(volume_mult=1.5)):
        assert seeded.post(f"/api/cohorts/{cid}/scenario", json=body).status_code == 200
    assert seeded.get(f"/api/cohorts/{cid}/budget-versions").json() == before


def test_DASH_10_csv_exports_match_ui_totals_to_the_cent(seeded):
    rows = {c["code"]: c for c in cohorts(seeded)}
    text = seeded.get("/api/export/cohorts.csv").text.splitlines()
    head, body = text[0].split(","), [ln.split(",") for ln in text[1:]]
    i_code, i_act = head.index("cohort"), head.index("actual_cm_to_date")
    for r in body:
        if r[i_act]:
            assert r[i_act] == f"{rows[r[i_code]]['actual_cm']:.2f}"
    agg = seeded.get("/api/collections/aging").json()
    csv_rows = seeded.get("/api/export/collections.csv").text.splitlines()[1:]
    assert abs(sum(float(r.split(",")[-1]) for r in csv_rows) - agg["total_outstanding"]) < 0.01 * max(
        len(csv_rows), 1
    )
    assert all(
        "." in r.split(",")[-1] and len(r.split(",")[-1].split(".")[1]) == 2 for r in csv_rows
    )  # cents preserved


# ---------------------------------------------------------------------------------------- Test set 9
def H(role):
    return {"X-Role": role}


def test_roles_viewer_is_read_only(client, world):
    assert client.get("/api/cohorts", headers=H("viewer")).status_code == 200
    attempts = [
        (
            "post",
            "/api/invoices",
            dict(
                partner_id=world["partner"]["id"],
                invoice_number="V1",
                invoice_date="2026-06-01",
                due_date="2026-07-01",
                amount=10,
            ),
        ),
        (
            "post",
            "/api/cost-entries",
            dict(cost_date="2026-06-01", cost_category="other_direct_cost", amount=5, allocation_rule="x"),
        ),
        ("post", "/api/partners", dict(name="X", partner_type="other")),
        ("patch", "/api/policy", dict(max_exception_rate=0.3)),
    ]
    for verb, url, body in attempts:
        r = getattr(client, verb)(url, json=body, headers=H("viewer"))
        assert r.status_code == 403 and r.json()["error"] == "forbidden", url


def test_roles_only_approver_can_approve_override_or_close(client, world):
    c = new_cohort(client, world, code="R1", acquisition_cost_per_unit=500)
    client.post(f"/api/cohorts/{c['id']}/underwrite")
    body = dict(approver="x", rationale="because", override=True)
    for role in ("operations", "business_development", "finance", "viewer"):
        assert (
            client.post(f"/api/cohorts/{c['id']}/approve", json=body, headers=H(role)).status_code == 403
        ), role
        assert (
            client.post(f"/api/cohorts/{c['id']}/decline", json=body, headers=H(role)).status_code == 403
        ), role
    ok = client.post(
        f"/api/cohorts/{c['id']}/approve", json=body, headers=H("approver")
    )  # policy said decline: override allowed only here...
    assert ok.status_code == 200
    bad = client.post(f"/api/cohorts/{c['id']}/status", json=dict(status="closed"), headers=H("finance"))
    assert bad.status_code == 403


def test_override_requires_a_rationale_and_is_audit_logged(client, world):
    c = new_cohort(client, world, code="R2", acquisition_cost_per_unit=500)
    client.post(f"/api/cohorts/{c['id']}/underwrite")
    assert (
        client.post(
            f"/api/cohorts/{c['id']}/approve", json=dict(approver="ceo", rationale="  ", override=True)
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/cohorts/{c['id']}/approve", json=dict(approver="ceo", rationale="strategic logo")
        ).status_code
        == 409
    )  # no override flag
    assert (
        client.post(
            f"/api/cohorts/{c['id']}/approve",
            json=dict(approver="ceo", rationale="strategic logo", override=True),
        ).status_code
        == 200
    )
    ev = next(e for e in client.get(f"/api/cohorts/{c['id']}/audit").json() if e["action"] == "approved")
    assert (
        ev["new_value"]["override"] is True
        and ev["new_value"]["recommendation"] == "decline"
        and ev["new_value"]["rationale"] == "strategic logo"
    )


def test_roles_operations_cannot_approve_but_can_operate_finance_can_book_cash(client, world):
    c = approved_cohort(client, world, code="R3", units=50)
    r = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][0], start_date="2026-06-16", end_date="2026-07-10"),
        headers=H("operations"),
    )
    assert r.status_code == 201
    assert client.post("/api/invoices", json=dict(partner_id=world["partner"]["id"], invoice_number="R3", invoice_date="2026-06-01", due_date="2026-07-01", amount=10),
                       headers=H("operations")).status_code == 403  # fmt: skip
    assert client.post("/api/invoices", json=dict(partner_id=world["partner"]["id"], invoice_number="R3", invoice_date="2026-06-01", due_date="2026-07-01", amount=10),
                       headers=H("finance")).status_code == 201  # fmt: skip
    assert (
        client.patch("/api/policy", json=dict(max_exception_rate=0.3), headers=H("finance")).status_code
        == 403
    )
    assert (
        client.patch("/api/policy", json=dict(max_exception_rate=0.3), headers=H("admin")).status_code == 200
    )
    assert client.get("/api/cohorts", headers=H("astronaut")).status_code == 403
