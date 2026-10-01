# Revise Recovery OS

**An illustrative operating-finance decision system for automated laptop refurbishment.**

> **Synthetic data only.** Everything in this repository — partners, cohorts, devices, prices, costs, thresholds, policies, alerts — is invented for a portfolio prototype. It is built from public context (that Revise Robotics describes automating electronics refurbishment with AI and robotics) and is **not affiliated with, endorsed by, or derived from Revise Robotics**. It does not use, represent or reproduce Revise's internal data, customers, contracts, pricing, operations, workflows or policies. This notice is shown on every screen. Not investment advice; not audited reporting; no robot control or live telemetry; **not production-ready**.

---

## 1. What it is

A cohort-level decision and tracking system: a FastAPI backend that owns every financial, capacity, decision and alert calculation, and a Next.js frontend that displays results and collects validated input. It underwrites an incoming batch of used laptops *before* robot time or cash is committed, then tracks actual throughput, exceptions, cost, resale recovery and collections against the approved plan, down to serial-numbered devices.

## 2. Why the product exists

A refurbishment operation's question is not *"can the robot process these laptops?"* but *"should we accept this cohort, give it scarce robot capacity and commit working capital, given its expected processing difficulty, resale recovery, margin and cash-conversion profile?"* Finance tools show results late, robotics tools show technical performance, spreadsheets connect neither. This prototype connects them at the unit of decision — the **cohort**.

```
Supply Partner → Laptop Cohort → Capacity Allocation → Robotic Processing → Repair / Exceptions → Resale → Cash Collection
```

## 3. What it models

Partners and contracts · device-model mix and condition · base / downside / upside economics · robot-cell capacity (with maintenance, downtime and multi-cell splits) · daily operations, exceptions and repair cost · resale listings, sales, returns, invoices and cash receipts · serial-level custody, sanitization and quality control · alerts · partner scoring · scenario sensitivity · underwriting memos.

## 4. Architecture

```
Next.js (TypeScript, Tailwind, Recharts) ──/api/* proxy──▶ FastAPI ──▶ SQLAlchemy 2 ──▶ SQLite (local) / PostgreSQL 16
   display + validated input only                           all finance, capacity, decision, alert and scenario logic
```

The browser never computes a financial figure. The backend is deterministic and testable (`Decimal` everywhere; no LLM is involved in any number). Layout: `backend/app/services/*` (economics, underwriting, capacity, operations, finance, variance, alerts, devices, drill-downs), `backend/app/routers/*` (thin HTTP layer), `backend/alembic` (migrations), `frontend/app/*` (pages), `scripts/*` (reproducible verification).

## 5. Financial model policy

Contribution-margin percentage is **always measured on net recognized revenue**, and every policy decision uses it.

$$\text{Net Recognized Revenue} = \text{Gross Sale Proceeds} - \text{Marketplace Fees} - \text{Refunds} - \text{Discounts}\;(+\;\text{invoiced service fees})$$

$$\text{Contribution Margin} = \text{Net Recognized Revenue} - \text{Direct Variable Costs}$$

$$\text{CM\%} = \frac{\text{Contribution Margin}}{\text{Net Recognized Revenue}}\qquad(\text{undefined / null when net revenue} \le 0)$$

Direct variable costs = acquisition (or partner share) + inbound logistics + **committed** robot cost + manual labour + repair parts + **merchant-paid outbound shipping and return shipping**.

- Merchant-paid outbound shipping and return shipping are **direct variable costs**, never revenue deductions.
- Marketplace fees, refunds and discounts are deducted **once**, from revenue.
- **Gross-basis CM% is informational only** (labelled "CM % of Gross Sale Proceeds") and never drives acceptance, prioritisation, partner scoring, margin-floor alerts or any policy.
- Robot cost is shown two ways: *committed* (scheduled hours × hourly cost — what the margin uses, because capacity is paid for even when idle) and *productive* (what the productive hours cost); the gap is idle-capacity leakage.
- A repaired device counts toward recovery only if it then passes repair and QC, and it never counts toward first-pass yield.
- All money is `Decimal`; rounding happens only at presentation.

| Outcome | Rule (defaults, editable on the Policy page) |
|---|---|
| Decline | base CM dollars < 0, or base CM% < 15% (also: unsupported wipe level, quality below minimum) |
| Review | base CM% 15% to < 30%, or downside CM% < 15%, or a material capacity / review-slot / cash gate fails |
| Accept | base ≥ 30%, downside ≥ 15%, all gates pass |
| Accept with conditions | as Accept, plus a manageable operational condition (exceptions, first-pass yield, sell-through, slow cash, partner track record) — never a margin shortfall |
| Critical margin intervention (live) | forecast CM% < 15% with non-negative CM dollars |
| Stop / loss-making (live) | forecast CM dollars < 0 |

**Policy thresholds and seed assumptions are illustrative synthetic assumptions** and use CM% based on net recognized revenue.

## 6. Core workflows

1. **Underwrite** a cohort (live preview; base / downside / upside; break-evens; policy checks) → recommendation → approve or decline with a rationale. Approval freezes the budget; later changes create a *reforecast* version.
2. **Allocate capacity**: the backend rejects overbooking, blocked cells and SLA breaches, with the exact shortfall; a cohort can be split across cells and hours and units reconcile exactly. Concurrent requests cannot double-book (atomic claims; row locks on PostgreSQL).
3. **Track operations**: daily records, throughput, utilisation, first-pass yield, exceptions, labour and repair cost, plan-vs-actual variance, reforecast and alerts.
4. **Resale and cash**: listings, sales (gross, fees, discounts, shipping, refunds kept separate), returns, invoices, receipts, aging.
5. **Close out** a cohort only when nothing is unsold, uncollected or critically alerting.

## 7. Device and ITAD-style control gates

Each laptop has a serial-level record and an append-only chain-of-custody log. The backend enforces: listing needs sanitized + QC passed + designated for resale; a failed wipe quarantines the device, raises one critical alert and blocks processing, resale and shipment; a sale needs a listed device, a real sale record and unsold quantity; recycle or destroy needs a reason code and operator reference; counts reconcile (sold + recycled + destroyed + active = registered = intake). Closeout is blocked by unsold devices, uncollected cash or open critical alerts, and closed cohorts are frozen.

## 8. Dashboard filters and metric semantics

Filters (date range, partner, cohort, robot cell, cohort status, contract type, ownership, resale channel) live in the URL, are validated and applied **server-side**, and combine by intersection. Each metric states its basis:

- **Date-filtered, by each source's own date:** realized CM (sale / invoice / cost date), cash collected (receipt date), utilisation, throughput and exception rate (operation date).
- **Current (never backdated — there are no historical snapshots):** active cohorts, plan and forecast CM, cash tied up, aged inventory, partner ranking, alerts. Overdue collections use a *current* balance for items issued in the range.
- No date filter ≠ "all time": each metric then uses its documented default window.

Full table: [`docs/DASHBOARD_FILTERS.md`](docs/DASHBOARD_FILTERS.md).

## 9. Dashboard drill-down reconciliation

Every actionable dashboard card links to a backend-driven detail page carrying the same filters, with a reconciliation sentence, the limitations, a CSV export and *Back to filtered dashboard*.

| Card | Route | Reconciles because |
|---|---|---|
| Active cohorts | `/cohorts?status=active` | row count = card |
| Forecast CM | `/analytics/cohort-profitability?metric=forecast_cm` | Σ forecast CM = card |
| Realized CM | `…?metric=realized_cm` | Σ CM = card; CM% = Σ CM ÷ Σ net revenue (never a mean of row %) |
| Cash tied up | `/analytics/cash-exposure` | inventory section = card; + payouts + service invoices = total; each record once |
| Overdue collections | `/collections/aging?status=overdue` | Σ outstanding balance = card |
| Robot utilization | `/operations/robot-performance` | Σ productive h ÷ Σ available h = card |
| Exception rate | `/operations/exceptions` | Σ exceptions ÷ Σ devices entering processing = card |
| Critical alerts | `/alerts?severity=critical&status=open` | row count = card |
| Aged inventory | `/inventory/aged` | Σ cost basis = card |

Ratios are always recomputed from summed numerators and denominators. A resale-channel filter shows *channel recovery* margin (acquisition and processing costs are not stored per channel); a robot-cell filter shows the cohorts that used the cell, not cell-attributable margin.

## 10. Three-minute demo walkthrough

Start from a clean state: `make reset-demo`, then `make api` and `make web` (two terminals) and open http://localhost:3000.

1. **Review EDU-005** — *Underwriting → EDU-005*: base / downside / upside, break-evens, policy checks. Recommendation: **Accept** (base ≈ 44% CM, downside ≈ 31%, on net recognized revenue).
2. **Approve it** with a rationale. The plan is now **immutable**; any change would create a reforecast version.
3. **Over-capacity attempt** — *Capacity*: choose EDU-005, **Cell A**, Assign. The backend rejects it: *"Overbooked: Cell A has 119.5h free … but 237.2h were requested (117.7h short)"*.
4. **Split across cells** — assign part of the hours to **Cell B**, then the remainder to **Cell C** (leave hours blank to place what is left). Hours and units reconcile exactly; a further assignment is refused (nothing left to place).
5. **ITAD-002 → Review** — *Underwriting*: base CM ≈ 33% clears the accept line, but the **downside case ≈ 12.5% is below the 15% floor**, so the system says Review, not Accept.
6. **Sanitization / QC gate** — *Device ledger*: take a received device and try to list it: refused (not sanitized, QC pending). Triage → sanitize with a certificate → robot processing → QC pass → designate for resale → list: now allowed. (A *failed* wipe quarantines the device and raises a critical alert.)
7. **Deterioration** — *ITAD-002 → Plan vs actual*: exception rate climbing 12% → 28%, repair cost per exception nearly doubling; forecast CM falls to ≈ 5% of net revenue → a **critical margin-intervention alert** on the dashboard.
8. **Filters** — on the Executive dashboard pick a partner, a cell or *Last 30 days*; the URL updates, every card re-queries the backend, *Clear filters* restores the global view.
9. **Drill down** — click *Realized contribution margin* (or *Overdue collections*, *Robot utilization*…): the detail table sums to the card, with the reconciliation sentence and the Current / date-window label. *Back to filtered dashboard* restores your filters.
10. **Export** — *Export CSV* on any detail page: the file's TOTAL row equals the visible total.

## 11. Local setup

Prerequisites: Python 3.12, Node 20+ (22 recommended), `make`. For the PostgreSQL verification only: PostgreSQL 16 server binaries (`brew install postgresql@16` on macOS; `initdb` and `pg_ctl` must be found). No Docker, remote service or credentials are needed.

```bash
make setup            # backend venv + pip install; frontend npm install   (network)
make setup-browsers   # one-time Chromium download for Playwright          (network)
make reset-demo       # create schema + load the synthetic dataset (safe to repeat)
make api              # FastAPI on http://localhost:8010   (docs at /docs)
make web              # Next.js on http://localhost:3000
```

The API uses port **8010**; the web app proxies `/api/*` to it (`API_URL` in `frontend/.env.local`). Set `DEMO_TODAY=YYYY-MM-DD` in `backend/.env` to freeze "today"; see `backend/.env.example`. `make help` lists every target.

## 12. Test and verification commands

```bash
make disk-check       # free space + generated-artifact sizes (deletes nothing)
make test-backend     # backend tests on SQLite
make test-frontend    # frontend unit tests
make lint             # backend lint (ruff)
make typecheck        # frontend type-check (tsc)
make build            # production frontend build
make e2e              # Chromium E2E on SQLite (starts and stops its own API + temporary database)
make verify           # all of the above on SQLite; fails on the first failing step
make verify-postgres  # isolated temporary PostgreSQL 16: alembic upgrade + alembic check + backend tests + Chromium E2E
make e2e-postgres     # Chromium E2E only, against the temporary PostgreSQL
make clean-generated  # remove ONLY project-local reproducible artifacts (review disk-check first)
```

Every build/test target first checks free disk space and refuses to run below 3 GB (override with `MIN_FREE_GB`). `verify-postgres` keeps its cluster in `./.pgdata` and removes it on success **and** failure.

## 13. What has been verified

Verified locally on 2026-09-30 (full detail in [`RELEASE_EVIDENCE.md`](RELEASE_EVIDENCE.md)):

- Backend tests: **299 passed** on SQLite; **299 passed** on PostgreSQL 16.
- Frontend unit tests: **12 of 12 passed**.
- Chromium E2E: **23 of 23 passed** against SQLite; **23 of 23 passed** against PostgreSQL 16.
- Backend lint, frontend type-check and the production build: clean.
- Migrations apply on PostgreSQL 16 and match the models (`alembic check`).
- Hand-calculated financials, exact capacity and multi-cell reconciliation, a concurrency test, serial-level gating, role checks, dashboard filters (DASH-07), dashboard drill-downs (DASH-08), CSV reconciliation, 375px layouts, and a seed-story test that fails if the demo narrative regresses. ID-by-ID traceability: [`docs/ACCEPTANCE_TESTS.md`](docs/ACCEPTANCE_TESTS.md).

## 14. Current limitations

- **No authentication.** The `X-Role` header is a spoofable, demo-only authorization scaffold, not production authorization.
- No backup / restore validation; no production logging, tracing or monitoring; no rate limiting.
- PostgreSQL was verified only against a **local ephemeral PostgreSQL 16**, not a hosted deployment (`render.yaml` and the Dockerfile are untested).
- The GitHub Actions workflow is **prepared but has never run remotely** (this folder is not yet a Git repository).
- No historical snapshot tables: past-date inventory, cash exposure, cohort status, partner ranking and alerts cannot be reconstructed, so they are labelled *Current*.
- Resale-channel margin is *recovery* margin, not a fully allocated channel contribution margin.
- The robot-cell filter identifies the cohorts that used the cell; it does not allocate their economics to it.
- Alert rules evaluate on read and after writes (no scheduler). Laptops only; no marketplace integrations; desktop-first data entry.
- Seed data and policies are illustrative and are not Revise Robotics' assumptions.

## 15. Production hardening roadmap

Authentication and secure sessions → server-side RBAC tied to authenticated identities → audit-log retention → backup and restore test → hosted PostgreSQL deployment test → secrets-management review → structured logging → error monitoring and alerting → rate limiting → security review → first remote CI run → production smoke test → data retention and privacy policy → real operational data integration and validation. The unchecked checklist lives in [`RELEASE_EVIDENCE.md`](RELEASE_EVIDENCE.md).

## 16. Synthetic-data and non-affiliation disclaimer

> Illustrative demo data only. Built from public operating context and synthetic assumptions; not representative of Revise Robotics' internal data, customers, economics, or processes. This project is independent of and not affiliated with Revise Robotics.
