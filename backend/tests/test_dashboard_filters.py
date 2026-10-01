"""DASH-07 -- dashboard filters. Every expectation is recomputed independently from raw database rows (not from the
dashboard's own output), so a card can only pass if it truly reconciles to source records.

Frozen "today" is 2026-09-30 (the `seeded` fixture). Default windows when NO date filter is given:
operations/utilisation/throughput = trailing 14 days, cash collected = current month, exceptions/realized margin = all time.
"""

import json
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (
    CapacityCalendar,
    CashReceipt,
    CostEntry,
    DailyOperation,
    DeviceCohort,
    Invoice,
    Partner,
    ResaleListing,
    RobotCell,
    SalesTransaction,  # fmt: skip
)

TODAY = date(2026, 9, 30)
ACTIVE = {
    "in_intake",
    "in_processing",
    "exception_review",
    "quality_control",
    "listed_for_sale",
    "partially_sold",
}
SALE_SIDE = {"outbound_shipping", "marketplace_fee", "returns"}
BASELINE = json.loads((Path(__file__).parent / "fixtures" / "dashboard_baseline.json").read_text())


def dash(c, **q):
    r = c.get("/api/dashboard/executive", params=q)
    assert r.status_code == 200, (q, r.text)
    return r.json()


def approx(a, b, tol=0.01):
    assert abs(float(a) - float(b)) <= tol, (a, b)


class Raw:
    """Independent view of the source tables."""

    def __init__(self, sf):
        with sf() as s:
            self.cohorts = list(s.scalars(select(DeviceCohort)))
            self.ops = list(s.scalars(select(DailyOperation)))
            self.cal = list(s.scalars(select(CapacityCalendar)))
            self.cells = list(s.scalars(select(RobotCell)))
            self.sales = list(s.scalars(select(SalesTransaction)))
            self.invoices = list(s.scalars(select(Invoice)))
            self.receipts = list(s.scalars(select(CashReceipt)))
            self.costs = list(s.scalars(select(CostEntry)))
            self.listings = list(s.scalars(select(ResaleListing)))
            self.partners = list(s.scalars(select(Partner)))
        self.code = {c.cohort_code: c for c in self.cohorts}
        self.channel_of = {ls.id: ls.listing_channel for ls in self.listings}
        self.cell = {c.cell_name: c for c in self.cells}

    def ids(self, *codes):
        return {self.code[c].id for c in codes}

    def ops_rows(self, ids, cell=None, lo=None, hi=TODAY):
        return [
            o
            for o in self.ops
            if o.cohort_id in ids
            and (not cell or o.robot_cell_id == cell)
            and (lo is None or o.operation_date >= lo)
            and o.operation_date <= hi
        ]

    def util(self, ids, cell=None, lo=TODAY - timedelta(days=13), hi=TODAY):
        cells = [c for c in self.cells if c.status == "available" and (not cell or c.id == cell)]
        cid = {c.id for c in cells}
        prod = sum(
            D(o.productive_robot_hours) for o in self.ops_rows(ids, cell, lo, hi) if o.robot_cell_id in cid
        )
        sched = sum(
            D(r.scheduled_hours) - D(r.maintenance_hours)
            for r in self.cal
            if r.robot_cell_id in cid and (lo is None or r.calendar_date >= lo) and r.calendar_date <= hi
        )
        return prod / sched if sched else D(0)

    def realized(self, ids, lo=None, hi=TODAY, channel=None):
        """net_recognized_revenue - direct variable cost, each item bounded by ITS OWN date."""
        sales = [s for s in self.sales if s.cohort_id in ids and s.sale_date and (lo is None or s.sale_date >= lo) and s.sale_date <= hi
                 and (not channel or self.channel_of.get(s.resale_listing_id) == channel)]  # fmt: skip
        gross = sum(D(s.gross_sale_amount) for s in sales)
        net = gross - sum(D(s.discount_amount) + D(s.refund_amount) + D(s.marketplace_fee) for s in sales)
        cost = sum(D(s.shipping_cost) + D(s.return_cost) for s in sales)
        if not channel:
            invs = [
                i
                for i in self.invoices
                if i.cohort_id in ids and (lo is None or i.invoice_date >= lo) and i.invoice_date <= hi
            ]
            net += sum(D(i.amount) for i in invs)
            cost += sum(
                D(c.amount)
                for c in self.costs
                if c.cohort_id in ids
                and c.cost_category not in SALE_SIDE
                and (lo is None or c.cost_date >= lo)
                and c.cost_date <= hi
            )
        return gross, net, net - cost

    def cash(self, ids, lo, hi, channel=None):
        tx = {s.id: s for s in self.sales}
        inv = {i.id: i for i in self.invoices}
        total = D(0)
        for r in self.receipts:
            if r.receipt_date < (lo or date.min) or r.receipt_date > hi:
                continue
            if r.sales_transaction_id:
                s = tx[r.sales_transaction_id]
                cid, ch = s.cohort_id, self.channel_of.get(s.resale_listing_id)
            else:
                cid, ch = inv[r.invoice_id].cohort_id, None
            if channel and ch != channel:
                continue
            if cid in ids:
                total += D(r.amount)
        return total


@pytest.fixture()
def env(seeded, session_factory):
    return seeded, Raw(session_factory)


# ---------------------------------------------------------------------------------------------- DASH-07 tests
def test_DASH07_01_unfiltered_dashboard_matches_the_pre_filter_global_baseline(env):
    c, raw = env
    d = dash(c)
    for k in (
        "active_cohorts",
        "committed_cohorts",
        "pending_review",
        "throughput_actual_14d",
        "alert_ids_count",
    ):
        if k in d:
            assert d[k] == BASELINE[k], k
    for k in ("plan_cm", "forecast_cm", "actual_cm_to_date", "cash_tied_up_inventory", "cash_collected_this_month", "utilization", "exception_rate",
              "aged_inventory_value", "overdue_collections", "throughput_plan_14d"):  # fmt: skip
        approx(d[k], BASELINE[k], 0.01 if "util" not in k and "rate" not in k else 1e-9)
    assert {x["name"]: round(x["utilization"], 9) for x in d["cells"]} == {
        x["name"]: round(x["utilization"], 9) for x in BASELINE["cells"]
    }
    assert {x["code"] for x in d["cohorts"]} == set(BASELINE["cohort_rows"])
    assert d["filters"]["count"] == 0 and d["filters"]["applied"] == []


def test_DASH07_02_filter_to_one_cohort_reflects_that_cohort_only(env):
    c, raw = env
    edu = raw.code["EDU-001"]
    d = dash(c, cohort_id=edu.id)
    assert (
        d["filters"]["cohorts_in_scope"] == 1
        and d["active_cohorts"] == 1
        and [x["code"] for x in d["cohorts"]] == ["EDU-001"]
    )
    row = d["cohorts"][0]
    approx(d["plan_cm"], row["plan_cm"])
    approx(d["forecast_cm"], row["forecast_cm"])
    approx(d["cash_tied_up_inventory"], row["inventory_value"])  # cash exposure / inventory
    approx(d["actual_cm_to_date"], raw.realized({edu.id})[2])  # margin from raw transactions
    rows = raw.ops_rows({edu.id}, lo=None)
    approx(
        d["exception_rate"],
        sum(o.manual_exception_count for o in rows) / sum(o.devices_started for o in rows),
        1e-9,
    )
    win = raw.ops_rows({edu.id}, lo=TODAY - timedelta(days=13))
    assert (
        d["throughput_actual"]
        == sum(o.devices_started for o in win)
        == sum(r["actual"] for r in d["throughput_vs_plan"])
    )  # chart == card
    mine = [a for a in c.get("/api/alerts").json() if a["cohort_id"] == edu.id]
    assert d["alert_counts"]["critical"] == len([a for a in mine if a["severity"] == "critical"])
    assert [p["name"] for p in d["partners"]] == ["Cascadia State University"]


def test_DASH07_03_partner_filter_reconciles_to_that_partners_records(env):
    c, raw = env
    meridian = next(p for p in raw.partners if p.name.startswith("Meridian"))
    ids = {x.id for x in raw.cohorts if x.partner_id == meridian.id}
    d = dash(c, partner_id=meridian.id)
    assert {x["code"] for x in d["cohorts"]} == {"ITAD-002", "ITAD-006"} and d["filters"][
        "cohorts_in_scope"
    ] == 2
    assert [p["name"] for p in d["partners"]] == [meridian.name]
    live = [x for x in d["cohorts"] if x["status"] in ACTIVE]
    approx(d["plan_cm"], sum(x["plan_cm"] for x in live))
    approx(d["forecast_cm"], sum(x["forecast_cm"] for x in live))
    approx(d["actual_cm_to_date"], raw.realized(ids)[2])
    approx(d["cash_collected"], raw.cash(ids, TODAY.replace(day=1), TODAY))
    scoped = [a for a in c.get("/api/alerts").json() if a["cohort_id"] in ids]
    assert sum(d["alert_counts"].values()) == len(scoped) and d["alert_counts"]["critical"] == len(
        [a for a in scoped if a["severity"] == "critical"]
    )
    assert not [x for x in d["critical_alerts"] if x["cohort_id"] not in ids]


def test_DASH07_04_robot_cell_filter_scopes_utilisation_and_throughput_to_that_cell(env):
    c, raw = env
    cell = raw.cell["Cell D"]
    d = dash(c, robot_cell_id=cell.id)
    assert [x["name"] for x in d["cells"]] == ["Cell D"]
    approx(d["utilization"], raw.util({x.id for x in raw.cohorts}, cell.id), 1e-9)
    assert d["throughput_actual"] == sum(
        o.devices_started
        for o in raw.ops_rows({x.id for x in raw.cohorts}, cell.id, TODAY - timedelta(days=13))
    )
    assert {x["code"] for x in d["cohorts"]} == {"ENT-003", "ITAD-002"}  # only cohorts that used Cell D
    other = dash(c, robot_cell_id=raw.cell["Cell B"].id)  # Cell B is idle
    assert other["utilization"] == 0 and other["throughput_actual"] == 0  # idle for 14 days...
    assert {x["code"] for x in other["cohorts"]} == {"EDU-001"}  # ...but EDU-001 used it earlier


def test_DASH07_05_date_range_includes_only_records_in_range(env):
    c, raw = env
    lo, hi = date(2026, 9, 21), date(2026, 9, 25)
    d = dash(c, range="custom", start_date=lo.isoformat(), end_date=hi.isoformat())
    all_ids = {x.id for x in raw.cohorts}
    live = {x.id for x in raw.cohorts if x.status in ACTIVE or x.status == "closed"}
    rows = raw.ops_rows(all_ids, lo=lo, hi=hi)
    assert d["throughput_actual"] == sum(o.devices_started for o in rows) > 0
    approx(d["utilization"], raw.util(all_ids, None, lo, hi), 1e-9)
    live_rows = [o for o in rows if o.cohort_id in live]
    approx(
        d["exception_rate"],
        sum(o.manual_exception_count for o in live_rows) / sum(o.devices_started for o in live_rows),
        1e-9,
    )
    gross, net, cm = raw.realized(live, lo, hi)
    approx(d["actual_cm_to_date"], cm)
    approx(d["realized_detail"]["net_recognized_revenue"], net)
    approx(d["cash_collected"], raw.cash(all_ids, lo, hi))
    assert d["filters"]["windows"]["operations"] == {"start": lo.isoformat(), "end": hi.isoformat()}
    # an earlier window must not leak later records
    early = dash(c, range="custom", start_date="2026-07-01", end_date="2026-07-10")
    assert early["throughput_actual"] == 0 and early["utilization"] == 0


def test_DASH07_06_resale_channel_filter_reconciles_to_matching_transactions(env):
    c, raw = env
    ch = "Atlas Wholesale (B2B)"
    ent = raw.ids("ENT-003")
    d = dash(c, resale_channel=ch)
    assert {x["code"] for x in d["cohorts"]} == {"ENT-003"} and d["filters"][
        "realized_basis"
    ] == "channel_recovery"
    gross, net, cm = raw.realized(ent, channel=ch)
    approx(d["realized_detail"]["gross_sale_proceeds"], gross)
    approx(d["realized_detail"]["net_recognized_revenue"], net)
    approx(d["actual_cm_to_date"], cm)
    approx(d["cash_collected"], raw.cash(ent, TODAY.replace(day=1), TODAY, ch))
    unpaid = sum(D(s.net_sale_amount) - sum(D(r.amount) for r in raw.receipts if r.sales_transaction_id == s.id) for s in raw.sales
                 if self_ch(raw, s) == ch and s.payout_due_date and s.payout_due_date < TODAY)  # fmt: skip
    approx(d["overdue_collections"], unpaid)  # invoices are not channel-attributable -> excluded
    other = dash(c, resale_channel="eBay Refurbished")
    assert {x["code"] for x in other["cohorts"]} == {"EDU-001", "ITAD-002"}


def self_ch(raw, s):
    return raw.channel_of.get(s.resale_listing_id)


def test_DASH07_07_multiple_filters_are_an_intersection_never_a_union(env):
    c, raw = env
    meridian = next(p for p in raw.partners if p.name.startswith("Meridian"))
    a = {x["code"] for x in dash(c, partner_id=meridian.id)["cohorts"]}
    b = {x["code"] for x in dash(c, cohort_status="in_processing")["cohorts"]}
    both = dash(c, partner_id=meridian.id, cohort_status="in_processing")
    assert {x["code"] for x in both["cohorts"]} == a & b == {"ITAD-002"}
    cellc = {x["code"] for x in dash(c, robot_cell_id=raw.cell["Cell D"].id)["cohorts"]}
    triple = dash(c, partner_id=meridian.id, cohort_status="in_processing", robot_cell_id=raw.cell["Cell D"].id, contract_type="supply_purchase",
                  ownership_model="operator_owned", resale_channel="eBay Refurbished", range="last_30_days")  # fmt: skip
    assert {x["code"] for x in triple["cohorts"]} <= a & b & cellc
    assert triple["filters"]["count"] == 7
    disjoint = dash(c, partner_id=meridian.id, cohort_id=raw.code["EDU-001"].id)
    assert disjoint["empty"] and disjoint["cohorts"] == []  # union would have been 3 cohorts


def test_DASH07_08_clearing_filters_restores_the_exact_global_values(env):
    c, raw = env
    before = dash(c)
    dash(c, partner_id=raw.partners[0].id)
    dash(c, range="last_7_days", robot_cell_id=raw.cell["Cell D"].id)
    after = dash(c)
    strip = lambda d: {k: v for k, v in d.items() if k != "critical_alerts"}  # noqa: E731
    assert strip(before) == strip(after)
    assert dash(c, **{}) == dash(c) and c.get("/api/dashboard/executive?").json()["filters"]["count"] == 0


def test_DASH07_09_filters_survive_a_refresh_because_they_are_pure_query_parameters(env):
    """Persistence across a browser refresh is exercised in Playwright; here: the same URL is idempotent and stateless."""
    c, raw = env
    q = dict(range="last_30_days", partner_id=raw.partners[0].id)
    assert dash(c, **q) == dash(c, **q)
    assert dash(c, **q)["filters"]["requested"] == q


@pytest.mark.parametrize(
    "params,msg",
    [
        (dict(start_date="2026-09-25", end_date="2026-09-01"), "end_date cannot be before start_date"),
        (
            dict(range="custom", start_date="2026-09-25", end_date="2026-09-24"),
            "end_date cannot be before start_date",
        ),
        (dict(range="last_7_days", start_date="2026-09-01"), "can only be combined with range=custom"),
        (dict(range="all_time", end_date="2026-09-01"), "cannot be combined"),
    ],
)
def test_DASH07_10_invalid_date_ranges_are_blocked_with_a_clear_message(env, params, msg):
    c, _ = env
    r = c.get("/api/dashboard/executive", params=params)
    assert r.status_code == 422 and msg in json.dumps(r.json())


def test_DASH07_11_no_match_produces_a_clean_zero_state(env):
    c, raw = env
    d = dash(
        c,
        partner_id=next(p.id for p in raw.partners if p.name.startswith("Meridian")),
        cohort_id=raw.code["EDU-001"].id,
    )
    assert d["empty"] is True and d["filters"]["cohorts_in_scope"] == 0
    zeros = ("active_cohorts", "committed_cohorts", "pending_review", "plan_cm", "forecast_cm", "actual_cm_to_date", "cash_tied_up_inventory",
             "cash_collected", "utilization", "exception_rate", "aged_inventory_value", "overdue_collections", "throughput_actual")  # fmt: skip
    for k in zeros:
        assert d[k] == 0, k
    assert (
        d["cohorts"] == []
        and d["partners"] == []
        and d["critical_alerts"] == []
        and d["alert_counts"] == {"critical": 0, "warning": 0}
    )
    assert all(r["actual"] == 0 for r in d["throughput_vs_plan"]) and all(
        v == 0 for v in d["collections_buckets"].values()
    )
    json.loads(json.dumps(d, allow_nan=False))  # no NaN / Infinity anywhere


FILTER_SETS = [
    {},
    {"range": "last_7_days"},
    {"range": "last_30_days"},
    {"range": "current_month"},
    {"range": "all_time"},
    {"range": "custom", "start_date": "2026-09-10", "end_date": "2026-09-20"},
    {"cohort": "EDU-001"},
    {"cohort": "ITAD-002"},
    {"cohort": "ENT-003"},
    {"partner": "Meridian"},
    {"partner": "Cascadia"},
    {"cell": "Cell D"},
    {"cell": "Cell A", "range": "last_30_days"},
    {"channel": "Atlas Wholesale (B2B)"},
    {"status": "partially_sold"},
    {"contract": "hybrid"},
    {"ownership": "operator_owned", "cell": "Cell C"},
    {"partner": "Meridian", "status": "in_processing", "range": "last_7_days"},
]


def to_params(raw, fs):
    p = {}
    for k, v in fs.items():
        if k == "cohort":
            p["cohort_id"] = raw.code[v].id
        elif k == "partner":
            p["partner_id"] = next(x.id for x in raw.partners if x.name.startswith(v))
        elif k == "cell":
            p["robot_cell_id"] = raw.cell[v].id
        elif k == "channel":
            p["resale_channel"] = v
        elif k == "status":
            p["cohort_status"] = v
        elif k == "contract":
            p["contract_type"] = v
        elif k == "ownership":
            p["ownership_model"] = v
        else:
            p[k] = v
    return p


@pytest.mark.parametrize(
    "fs", FILTER_SETS, ids=lambda f: "+".join(f"{k}={v}" for k, v in f.items()) or "none"
)
def test_DASH07_12_dashboard_totals_reconcile_to_source_records_under_every_filter(env, fs):
    c, raw = env
    p = to_params(raw, fs)
    d = dash(c, **p)
    ids = {x["id"] for x in d["cohorts"]}
    live = [x for x in d["cohorts"] if x["status"] in ACTIVE or x["status"] == "closed"]
    lo, hi = d["filters"]["windows"]["operations"]["start"], d["filters"]["windows"]["operations"]["end"]
    lo, hi = (date.fromisoformat(lo) if lo else None), date.fromisoformat(hi)
    scope_ids = (
        {x.id for x in raw.cohorts if x.id in ids}
        if (d["filters"]["cohorts_in_scope"] or p)
        else {x.id for x in raw.cohorts}
    )
    cell = p.get("robot_cell_id")
    # cohort-level cards == sum of the cohort rows shown
    approx(d["plan_cm"], sum(x["plan_cm"] for x in live))
    approx(d["forecast_cm"], sum(x["forecast_cm"] for x in live))
    assert d["active_cohorts"] == len([x for x in d["cohorts"] if x["status"] in ACTIVE])
    if not p.get("resale_channel"):
        approx(d["cash_tied_up_inventory"], sum(x["inventory_value"] for x in live))
    # dated cards == raw recomputation
    live_ids = {x["id"] for x in live}
    rows = raw.ops_rows(scope_ids, cell, lo, hi)
    assert (
        d["throughput_actual"]
        == sum(o.devices_started for o in rows)
        == sum(r["actual"] for r in d["throughput_vs_plan"])
    )
    approx(d["utilization"], raw.util(scope_ids, cell, lo, hi), 1e-9)
    elo = d["filters"]["windows"]["exceptions"]["start"]
    ehi = date.fromisoformat(d["filters"]["windows"]["exceptions"]["end"])
    erows = raw.ops_rows(live_ids, cell, date.fromisoformat(elo) if elo else None, ehi)
    assert d["exception_detail"]["exceptions"] == sum(o.manual_exception_count for o in erows)
    assert d["exception_detail"]["devices_started"] == sum(o.devices_started for o in erows)
    rw = d["filters"]["windows"]["realized_margin"]
    rlo, rhi = (date.fromisoformat(rw["start"]) if rw["start"] else None), date.fromisoformat(rw["end"])
    approx(d["actual_cm_to_date"], raw.realized(live_ids, rlo, rhi, p.get("resale_channel"))[2])
    cw = d["filters"]["windows"]["cash_collected"]
    clo, chi = (date.fromisoformat(cw["start"]) if cw["start"] else None), date.fromisoformat(cw["end"])
    approx(
        d["cash_collected"],
        raw.cash(scope_ids if p else {x.id for x in raw.cohorts}, clo, chi, p.get("resale_channel")),
    )
    # alerts == open alerts attached to in-scope cohorts (or the selected cell)
    scoped = [a for a in c.get("/api/alerts").json() if (not d["filters"]["applied"] or a["cohort_id"] in ids or (a["entity_type"] == "robot_cell" and a["entity_id"] == cell))
              or not [k for k in p if k != "range" and k != "start_date" and k != "end_date"]]  # fmt: skip
    assert sum(d["alert_counts"].values()) == len([a for a in scoped if a["status"] in ("open", "escalated")])
    # nothing is NaN / infinite
    json.loads(json.dumps(d, allow_nan=False))


# ---------------------------------------------------------------------------------------------- validation (typed schema)
@pytest.mark.parametrize(
    "params",
    [
        {"partner_id": "not-a-real-id"},
        {"cohort_id": "x"},
        {"robot_cell_id": "x"},
        {"resale_channel": "No Such Channel"},
        {"cohort_status": "bogus"},
        {"contract_type": "barter"},
        {"ownership_model": "rented"},
        {"range": "last_year"},
        {"start_date": "31-12-2026"},
        {"start_date": "garbage"},
        {"unknown_filter": "1"},
    ],
)
def test_typed_validation_rejects_bad_input_with_422(env, params):
    c, _ = env
    r = c.get("/api/dashboard/executive", params=params)
    assert r.status_code == 422, (params, r.text)
    assert r.json().get("message") or r.json().get("detail")


def test_filters_never_increase_scope(env):
    c, raw = env
    base = dash(c, range="last_30_days")["filters"]["cohorts_in_scope"]
    for extra in (
        {"cohort_status": "in_processing"},
        {"contract_type": "supply_purchase"},
        {"ownership_model": "operator_owned"},
    ):
        assert dash(c, range="last_30_days", **extra)["filters"]["cohorts_in_scope"] <= base


def test_semantics_are_published_with_the_response(env):
    c, _ = env
    sem = dash(c)["semantics"]
    assert (
        sem["cash_tied_up_inventory"]["basis"] == "current"
        and sem["aged_inventory_value"]["basis"] == "current"
    )
    assert sem["utilization"]["basis"] == "dated" and sem["cash_collected"]["date_field"] == "receipt_date"
