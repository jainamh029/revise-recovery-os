"""Documented filter semantics for every executive-dashboard metric. Returned with the API so the UI can label them and
tests can assert them. basis: dated = filtered by the stated source date; current = point-in-time, NOT backdated; cohort =
cohort-level (follows cohort filters only)."""

SEMANTICS: dict[str, dict] = {
    "active_cohorts": {
        "basis": "current",
        "date_field": None,
        "note": "Current cohort status. Status history is not snapshotted, so it is not backdated to the selected range.",
    },
    "pending_review": {"basis": "current", "date_field": None, "note": "Current cohort status."},
    "plan_cm": {
        "basis": "current",
        "date_field": None,
        "note": "Approved plan of live cohorts in scope; a plan is not dated activity.",
    },
    "forecast_cm": {
        "basis": "current",
        "date_field": None,
        "note": "Reforecast as of today for live cohorts in scope.",
    },
    "actual_cm_to_date": {
        "basis": "dated",
        "date_field": "sale_date / invoice_date / cost_date",
        "note": "Net recognized revenue (sales by sale_date, invoices by invoice_date) minus direct variable costs (cost entries by cost_date; shipping and return labels by sale_date), within the range. With a resale-channel filter: channel recovery margin (matching sales only; cohort processing costs and invoices cannot be attributed to a channel).",
    },
    "cash_tied_up_inventory": {
        "basis": "current",
        "date_field": None,
        "note": "Current unsold inventory at cost. No reliable historical snapshots exist, so it is never backdated. With a channel filter: unsold units on that channel's listings at cohort unit cost.",
    },
    "cash_collected": {
        "basis": "dated",
        "date_field": "receipt_date",
        "note": "Cash receipts in the range (default when no date filter: current month).",
    },
    "utilization": {
        "basis": "dated",
        "date_field": "operation_date / calendar_date",
        "note": "Productive hours of in-scope cohorts on in-scope available cells ÷ scheduled minus maintenance hours, within the range (default: trailing 14 days). With cohort filters it is those cohorts' share of cell hours.",
    },
    "throughput": {
        "basis": "dated",
        "date_field": "operation_date",
        "note": "Devices started per operating day in the range vs the assignment schedule (default: trailing 14 days). Non-operating weekend days are omitted.",
    },
    "exception_rate": {
        "basis": "dated",
        "date_field": "operation_date",
        "note": "Manual exceptions ÷ devices started on live cohorts in the range (default: all time).",
    },
    "aged_inventory_value": {
        "basis": "current",
        "date_field": None,
        "note": "Current listed-unsold inventory older than the target days, at cost. Not backdated.",
    },
    "overdue_collections": {
        "basis": "current",
        "date_field": "sale_date / invoice_date (selection only)",
        "note": "Current unpaid balance past due, for invoices/payouts ISSUED in the range (invoice_date / sale_date). Balances are as of today.",
    },
    "partners": {
        "basis": "current",
        "date_field": None,
        "note": "Score of each partner's in-scope cohorts, as of today.",
    },
    "alerts": {
        "basis": "current",
        "date_field": None,
        "note": "Open alerts attached to in-scope cohorts (or the selected cell). Portfolio-level alerts appear only when no entity filter is set.",
    },
    "cohort_table": {
        "basis": "current",
        "date_field": None,
        "note": "In-scope cohorts with current status and current plan/forecast.",
    },
}
