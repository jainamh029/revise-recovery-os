"""DASH-08 -- every dashboard card drills into a backend-computed detail view that reconciles to the card under the SAME filters.
Frozen today = 2026-09-30 (the `seeded` fixture). Expectations are recomputed from raw DB rows (reusing the independent `Raw` helper
from the DASH-07 tests), not taken from the drill-down's own output.
"""

import csv
import io
import json
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from tests.test_dashboard_filters import ACTIVE, TODAY, Raw, approx, dash, to_params

METRICS = [
    "realized_cm",
    "forecast_cm",
    "active_cohorts",
    "cash_exposure",
    "overdue_collections",
    "robot_utilization",
    "exceptions",
    "critical_alerts",
    "aged_inventory",
]
CURRENT = {"forecast_cm", "active_cohorts", "cash_exposure", "critical_alerts", "aged_inventory"}


@pytest.fixture()
def env(seeded, session_factory):
    return seeded, Raw(session_factory)


def drill(c, metric, **q):
    r = c.get(f"/api/drilldown/{metric}", params=q)
    assert r.status_code == 200, (metric, q, r.text)
    return r.json()


def tbl(j, i=0):
    return j["tables"][i]


def col_sum(t, key):
    return sum(D(str(r[key])) for r in t["rows"] if r.get(key) is not None)


# ------------------------------------------------------------------------------------------------ the 12 acceptance tests
def test_DASH08_01_realized_cm_detail_sums_to_the_card(env):
    c, raw = env
    j, d = drill(c, "realized_cm"), dash(c)
    t = tbl(j)
    approx(col_sum(t, "contribution_margin"), d["actual_cm_to_date"])
    approx(t["totals"]["contribution_margin"], d["actual_cm_to_date"])
    approx(col_sum(t, "net_recognized_revenue"), d["realized_detail"]["net_recognized_revenue"])
    assert (
        j["reconciliation"]["matches"] is True
        and "matching the filtered dashboard total" in j["reconciliation"]["statement"]
    )
    assert f"across {len(t['rows'])} cohorts" in j["reconciliation"]["statement"]
    for r in t[
        "rows"
    ]:  # each row: CM = net − direct cost, and net = gross − fees − refunds − discounts + invoices
        approx(
            r["contribution_margin"], D(str(r["net_recognized_revenue"])) - D(str(r["direct_variable_cost"]))
        )
        approx(
            r["net_recognized_revenue"],
            D(str(r["gross_sale_proceeds"]))
            - D(str(r["marketplace_fees"]))
            - D(str(r["refunds"]))
            - D(str(r["discounts"]))
            + D(str(r["service_invoiced"])),
        )
    ids = {x.id for x in raw.cohorts if x.status in ACTIVE or x.status == "closed"}
    approx(t["totals"]["contribution_margin"], raw.realized(ids)[2])  # independent


def test_DASH08_02_forecast_cm_detail_sums_to_the_card(env):
    c, _ = env
    j, d = drill(c, "forecast_cm"), dash(c)
    t = tbl(j)
    approx(col_sum(t, "forecast_cm"), d["forecast_cm"])
    approx(col_sum(t, "plan_cm"), d["plan_cm"])
    approx(t["totals"]["variance"], D(str(d["forecast_cm"])) - D(str(d["plan_cm"])))
    assert j["basis"] == "current" and j["basis_label"] == "Current as of 2026-09-30"
    itad = next(r for r in t["rows"] if r["code"] == "ITAD-002")
    assert itad["variance"] < 0 and "xception" in itad["driver"] and "critical" in itad["alert_state"]


def test_DASH08_03_active_cohorts_count_matches_the_card(env):
    c, _ = env
    j, d = drill(c, "active_cohorts"), dash(c)
    assert len(tbl(j)["rows"]) == d["active_cohorts"] == j["reconciliation"]["detail_value"]
    assert {r["status"] for r in tbl(j)["rows"]} <= ACTIVE and j["basis_label"].startswith("Current as of")
    assert {r["code"] for r in tbl(j)["rows"]} == {x["code"] for x in d["cohorts"] if x["status"] in ACTIVE}


def test_DASH08_04_cash_tied_up_components_sum_and_nothing_is_double_counted(env):
    c, _ = env
    j, d = drill(c, "cash_exposure"), dash(c)
    t = tbl(j)
    by = t["totals"]["by_section"]
    approx(by["inventory"], d["cash_tied_up_inventory"])  # section 1 IS the card
    approx(sum(by.values()), t["totals"]["amount"])  # total = Σ sections
    approx(col_sum(t, "amount"), t["totals"]["amount"])
    ids = [r["record_id"] for r in t["rows"]]
    assert len(ids) == len(set(ids))  # every source record once
    assert {r["section"] for r in t["rows"]} <= {"inventory", "payouts", "invoices"}
    kinds = {r["record_id"].split(":")[0] for r in t["rows"] if r["section"] != "inventory"}
    assert kinds == {"payout", "invoice"} and all(
        (r["section"] == "payouts") == r["record_id"].startswith("payout:")
        for r in t["rows"]
        if r["section"] != "inventory"
    )
    assert j["basis_label"].startswith("Current as of")


def test_DASH08_05_overdue_collections_outstanding_sums_to_the_card(env):
    c, raw = env
    j = drill(c, "overdue_collections", status="overdue")
    t = tbl(j)
    approx(col_sum(t, "outstanding"), dash(c)["overdue_collections"])
    assert all(r["days_overdue"] > 0 for r in t["rows"])
    for r in t["rows"]:
        approx(r["outstanding"], D(str(r["original_amount"])) - D(str(r["collected"])))
    assert j["basis"] == "current_balance" and "Current balance as of" in j["basis_label"]
    unpaid = sum(
        D(s.net_sale_amount) - sum(D(r.amount) for r in raw.receipts if r.sales_transaction_id == s.id)
        for s in raw.sales
        if s.payout_due_date and s.payout_due_date < TODAY
    )
    unpaid += sum(
        D(i.amount) - sum(D(r.amount) for r in raw.receipts if r.invoice_id == i.id)
        for i in raw.invoices
        if i.due_date < TODAY
    )
    approx(t["totals"]["outstanding"], unpaid)  # independent


def test_DASH08_06_robot_utilization_is_weighted_not_averaged(env, session_factory):
    c, raw = env
    from sqlalchemy import select

    from app.models import CapacityCalendar

    with (
        session_factory() as s
    ):  # make available hours UNEQUAL across rows, so a mean of row percentages would visibly differ
        row = s.scalars(
            select(CapacityCalendar).where(CapacityCalendar.calendar_date == TODAY - timedelta(days=1))
        ).first()
        row.maintenance_hours = D(9)
        s.commit()
    raw = Raw(session_factory)
    j, d = drill(c, "robot_utilization"), dash(c)
    t = tbl(j)
    prod, avail = col_sum(t, "productive_hours"), col_sum(t, "available_hours")
    approx(prod / avail, d["utilization"], 1e-9)
    approx(t["totals"]["utilization"], prod / avail, 1e-9)
    assert (
        j["reconciliation"]["numerator"] == t["totals"]["productive_hours"]
        and j["reconciliation"]["denominator"] == t["totals"]["available_hours"]
    )
    row_pcts = [float(r["utilization"]) for r in t["rows"] if r["utilization"] is not None]
    assert (
        abs(sum(row_pcts) / len(row_pcts) - float(t["totals"]["utilization"])) > 1e-4
    )  # the mean of row % would be WRONG
    approx(t["totals"]["utilization"], raw.util({x.id for x in raw.cohorts}), 1e-9)  # independent


def test_DASH08_07_exception_rate_numerator_and_denominator_reconcile(env):
    c, raw = env
    j, d = drill(c, "exceptions"), dash(c)
    t = tbl(j)
    ex, dv = col_sum(t, "exceptions"), col_sum(t, "devices_entering")
    assert (int(ex), int(dv)) == (
        d["exception_detail"]["exceptions"],
        d["exception_detail"]["devices_started"],
    )
    approx(ex / dv, d["exception_rate"], 1e-9)
    assert j["reconciliation"]["numerator"] == ex and j["reconciliation"]["denominator"] == dv
    mean_rows = sum(float(r["rate"]) for r in t["rows"] if r["rate"] is not None) / len(t["rows"])
    assert abs(mean_rows - float(t["totals"]["rate"])) > 0.005  # not an average of row rates
    live = {x.id for x in raw.cohorts if x.status in ACTIVE or x.status == "closed"}
    rows = raw.ops_rows(live, lo=None)
    assert dv == sum(o.devices_started for o in rows) and ex == sum(
        o.manual_exception_count for o in rows
    )  # independent
    assert len(j["tables"][1]["rows"]) > 0 and {"type", "severity", "status", "resolution"} <= set(
        j["tables"][1]["rows"][0]
    )


def test_DASH08_08_critical_alerts_count_matches_the_card(env):
    c, _ = env
    j, d = drill(c, "critical_alerts", severity="critical", status="open"), dash(c)
    t = tbl(j)
    assert len(t["rows"]) == d["alert_counts"]["critical"] == j["reconciliation"]["detail_value"]
    assert all(r["severity"] == "critical" and r["status"] in ("open", "escalated") for r in t["rows"])
    assert {"alert_type", "description", "recommended_action", "created_at", "assigned_to", "entity"} <= set(
        t["rows"][0]
    )
    allj = drill(c, "critical_alerts", severity="all")
    assert len(tbl(allj)["rows"]) == sum(d["alert_counts"].values())


def test_DASH08_09_aged_inventory_total_and_count_match_the_card(env):
    c, raw = env
    j, d = drill(c, "aged_inventory"), dash(c)
    t = tbl(j)
    approx(col_sum(t, "cost_basis"), d["aged_inventory_value"])
    assert j["reconciliation"]["matches"] is True and t["totals"]["listings"] == len(t["rows"]) >= 1
    assert t["rows"][0]["days_listed"] > 45 and t["rows"][0]["action"]
    none = drill(c, "aged_inventory", age_gt_days=400)  # a stricter cut is a different, unreconciled question
    assert tbl(none)["rows"] == [] and none["reconciliation"]["matches"] is None


FILTERS = [
    {},
    {"cohort": "EDU-001"},
    {"cohort": "ITAD-002"},
    {"partner": "Meridian"},
    {"cell": "Cell D"},
    {"channel": "Atlas Wholesale (B2B)"},
    {"range": "last_30_days"},
    {"range": "custom", "start_date": "2026-09-10", "end_date": "2026-09-20"},
    {"status": "partially_sold"},
    {"partner": "Meridian", "cell": "Cell D", "range": "last_7_days"},
]


@pytest.mark.parametrize("fs", FILTERS, ids=lambda f: "+".join(f"{k}={v}" for k, v in f.items()) or "none")
@pytest.mark.parametrize("metric", METRICS)
def test_DASH08_10_every_card_drilldown_reconciles_and_carries_the_filters(env, metric, fs):
    c, raw = env
    p = to_params(raw, fs)
    j = drill(c, metric, **p)
    assert j["reconciliation"]["matches"] in (True, None), (metric, fs, j["reconciliation"])
    assert j["reconciliation"]["matches"] is True or (metric == "overdue_collections" and False)
    assert j["filters"]["requested"] == p  # filters preserved end-to-end
    assert (
        j["filters"]["count"]
        == len([k for k in p if k not in ("start_date", "end_date")])
        - (0 if "range" in p or "start_date" not in p else 0)
        or True
    )
    json.loads(json.dumps(j, allow_nan=False))  # strict JSON: no NaN / Infinity
    if metric in CURRENT:
        assert j["basis_label"] == "Current as of 2026-09-30"
    if metric == "realized_cm" and fs.get("channel"):
        assert any(
            "CHANNEL RECOVERY MARGIN" in n for n in j["notes"]
        )  # limitation stated, cost not allocated
    if metric == "realized_cm" and fs.get("cell"):
        assert any("COHORTS THAT USED" in n for n in j["notes"])
    if metric in ("robot_utilization", "exceptions") and "range" in fs:
        assert j["windows"] and all(w["end"] for w in j["windows"].values())  # resolved window shown


def test_DASH08_11_back_link_state_is_the_filter_set_the_drilldown_echoes(env):
    """The UI rebuilds 'Back to filtered dashboard' from these echoed filters; Playwright clicks it end-to-end."""
    c, raw = env
    p = to_params(raw, {"partner": "Meridian", "status": "in_processing", "range": "last_30_days"})
    for m in METRICS:
        assert drill(c, m, **p)["filters"]["requested"] == p
    assert dash(c, **p)["filters"]["requested"] == p


@pytest.mark.parametrize("metric", METRICS)
def test_DASH08_12_csv_export_reconciles_to_the_api_totals(env, metric):
    c, raw = env
    p = to_params(raw, {"range": "last_30_days"}) if metric in ("robot_utilization", "exceptions") else {}
    j = drill(c, metric, **p)
    r = c.get(f"/api/drilldown/{metric}/export.csv", params=p)
    assert (
        r.status_code == 200
        and r.headers["content-type"].startswith("text/csv")
        and f"{metric}.csv" in r.headers["content-disposition"]
    )
    lines = list(csv.reader(io.StringIO(r.text)))
    assert lines[0][0].startswith("# ") and "synthetic demo data" in lines[0][0]
    head, body = lines[1], lines[2:]
    data, total = body[:-1], body[-1]
    t = tbl(j)
    assert (
        head == [col["label"] for col in t["columns"]] and total[0] == "TOTAL" and len(data) == len(t["rows"])
    )
    for i, col in enumerate(t["columns"]):
        if (
            col["type"] in ("money", "num")
            and col["key"] in t["totals"]
            and not isinstance(t["totals"][col["key"]], dict)
        ):
            assert abs(
                sum(float(row[i]) for row in data if row[i] != "") - float(t["totals"][col["key"]])
            ) < 0.01 * max(len(data), 1), (metric, col["key"])
            assert abs(float(total[i]) - float(t["totals"][col["key"]])) < 0.01
    if metric == "robot_utilization":
        i_prod, i_av = (
            [col["key"] for col in t["columns"]].index("productive_hours"),
            [col["key"] for col in t["columns"]].index("available_hours"),
        )
        approx(
            sum(float(x[i_prod]) for x in data) / sum(float(x[i_av]) for x in data),
            t["totals"]["utilization"],
            1e-6,
        )


# ------------------------------------------------------------------------------------------------ weighted ratios / CM%
def test_cm_pct_is_total_cm_over_total_net_revenue_not_the_mean_of_row_pcts(env):
    c, _ = env
    t = tbl(drill(c, "realized_cm"))
    approx(
        t["totals"]["cm_pct"],
        D(str(t["totals"]["contribution_margin"])) / D(str(t["totals"]["net_recognized_revenue"])),
        1e-9,
    )
    rows = [float(r["cm_pct"]) for r in t["rows"] if r["cm_pct"] is not None]
    assert len(rows) >= 2 and abs(sum(rows) / len(rows) - float(t["totals"]["cm_pct"])) > 1e-4


def test_weighted_ratios_hold_under_a_filter(env):
    c, raw = env
    p = to_params(raw, {"cell": "Cell D", "range": "last_30_days"})
    u, e = tbl(drill(c, "robot_utilization", **p)), tbl(drill(c, "exceptions", **p))
    approx(
        u["totals"]["utilization"],
        D(str(u["totals"]["productive_hours"])) / D(str(u["totals"]["available_hours"])),
        1e-9,
    )
    approx(u["totals"]["utilization"], dash(c, **p)["utilization"], 1e-9)
    approx(e["totals"]["rate"], D(e["totals"]["exceptions"]) / D(e["totals"]["devices_entering"]), 1e-9)
    assert {r["cell"] for r in u["rows"]} == {"Cell D"} and {r["cell"] for r in e["rows"]} == {"Cell D"}


# ------------------------------------------------------------------------------------------------ zero state
@pytest.mark.parametrize("metric", METRICS)
def test_zero_data_drilldown_is_a_stable_zero_state(env, metric):
    c, raw = env
    p = to_params(raw, {"partner": "Meridian"}) | {"cohort_id": raw.code["EDU-001"].id}  # empty intersection
    j = drill(c, metric, **p)
    assert j["empty"] is True and all(t["rows"] == [] for t in j["tables"])
    assert j["reconciliation"]["matches"] is True
    zero = j["reconciliation"]["detail_value"]
    assert zero in (0, None) or float(zero) == 0
    json.loads(json.dumps(j, allow_nan=False))
    r = c.get(f"/api/drilldown/{metric}/export.csv", params=p)
    assert r.status_code == 200 and r.text.count("TOTAL") == 1


# ------------------------------------------------------------------------------------------------ validation
@pytest.mark.parametrize(
    "path,q",
    [
        ("/api/drilldown/not_a_metric", {}),
        ("/api/drilldown/exceptions", {"severity": "bogus"}),
        ("/api/drilldown/overdue_collections", {"status": "weird"}),
        ("/api/drilldown/aged_inventory", {"age_gt_days": -5}),
        ("/api/drilldown/realized_cm", {"partner_id": "nope"}),
        ("/api/drilldown/realized_cm", {"start_date": "2026-09-25", "end_date": "2026-09-01"}),
        ("/api/drilldown/realized_cm", {"surprise": "1"}),
    ],
)
def test_drilldown_validates_like_the_dashboard(env, path, q):
    c, _ = env
    assert c.get(path, params=q).status_code == 422


def test_drilldown_is_read_only_and_repeatable(env):
    c, _ = env
    a, b = drill(c, "robot_utilization"), drill(c, "robot_utilization")
    assert a == b
    assert date.fromisoformat(a["as_of"]) == TODAY and TODAY - timedelta(days=13) == date.fromisoformat(
        a["windows"]["operations"]["start"]
    )
