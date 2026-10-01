# Dashboard filters (DASH-07)

Illustrative, synthetic data only. Not Revise Robotics' actual operating metrics.

**How it works.** `GET /api/dashboard/executive` takes one typed, validated `DashboardFilters` query model
(`backend/app/filters.py`). Filters are validated server-side (unknown IDs, unsupported enum values, malformed dates, end before
start, conflicting `range` + dates, unknown parameters → HTTP 422 with a readable message) and **combined by intersection only**.
The browser keeps filter state only in the URL query string, so a filtered view is shareable and refresh-safe.

`range` ∈ `all_time | last_7_days | last_30_days | current_month | custom` (custom uses `start_date` / `end_date`, either may be open).
Other filters: `partner_id`, `cohort_id`, `robot_cell_id`, `cohort_status`, `contract_type`, `ownership_model`, `resale_channel`.

**No date filter ≠ "all time".** With no date filter each metric uses its own documented default window (shown on the dashboard);
`range=all_time` is an explicit, different choice. `end` is never later than today, so future calendar days cannot dilute utilisation.

## Per-metric semantics

| Metric | Date basis | Notes |
|---|---|---|
| Active cohorts, decisions waiting | **Current** status | Status history is not snapshotted, so it cannot be backdated. Cohort-level filters (partner, cohort, status, contract type, ownership, cell, channel) apply. |
| Plan CM, forecast CM | **Current** | Approved plan / reforecast as of today for live cohorts in scope. |
| Realized CM | **Dated** by each source's own date | `sale_date` (gross, discounts, refunds, fees, shipping, return labels), `invoice_date` (service invoices), `cost_date` (cost entries). Default: all time. With a **resale-channel** filter it is a *channel recovery margin* (matching sales only): cohort processing costs and invoices cannot be attributed to a channel, and the UI says so. |
| Cash collected | **Dated** by `receipt_date` | Default window: current month. Channel filter keeps receipts of that channel's sales. |
| Robot utilisation | **Dated** by `operation_date` (productive hours) and `calendar_date` (scheduled − maintenance) | Default: trailing 14 days. Cell filter narrows to that cell. With cohort filters it is those cohorts' share of the cells' hours. |
| Throughput vs plan | **Dated** by `operation_date` | Plan from the assignment schedule for in-scope cohorts/cells. Weekend (non-operating) days are omitted. |
| Exception rate | **Dated** by `operation_date` | Default: all time, live cohorts. The numerator and denominator are returned (`exception_detail`). |
| Cash tied up in inventory, aged inventory | **Current** | No reliable historical snapshots exist, so these are **never backdated** and are labelled *Current*. A channel filter uses that channel's listed-unsold units at cohort unit cost. |
| Overdue collections, receivable buckets | **Current balance** of items *issued* in range | Selection by `invoice_date` / `sale_date`; the outstanding balance is as of today. Channel filter keeps that channel's payouts (invoices are not channel-attributable, so excluded). |
| Partner ranking | **Current** | Each partner scored on its in-scope cohorts only. |
| Alerts | **Current** | Open alerts attached to in-scope cohorts (or the selected cell). Portfolio-level alerts (e.g. working capital) show only when no entity filter is set. |
| Cohort table | **Current** | In-scope cohorts. |

The same table is returned by the API as `semantics`, and the resolved windows as `filters.windows`.

## Things the data model cannot do (not faked)
- Cohort status *as of* a past date, inventory value and cash exposure *as of* a past date: there are no snapshot tables, and
  reconstructing them from current rows would be fabrication. They are labelled **Current**.
- Cell filter + margin: revenue and acquisition costs are not recorded per cell, so cohort-level margin cards show the *cohorts that
  used the cell*, not a cell-apportioned margin. Only operations metrics are truly cell-scoped.
