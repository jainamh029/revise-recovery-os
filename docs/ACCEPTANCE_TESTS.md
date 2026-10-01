# Acceptance test program — traceability and results

All data is synthetic. "Automated" = a named test that runs in `make test` (backend, 107 tests) or `make e2e` (browser, 9 tests).
IDs follow the acceptance plan. **Nothing below is marked passed unless a test executed and passed.**

## Test-run record

| Field | Value |
|---|---|
| Build / commit | none — the project is not in a git repository (no SHA exists) |
| Test date | 2026-09-30 |
| Tester | Claude (automated), for Jainam |
| Backend, SQLite | **107 passed, 2 skipped** (skips = DASH-07, DASH-08, documented gaps) |
| Backend, PostgreSQL 16 | **107 passed, 2 skipped** (same suite via `TEST_DATABASE_URL`; includes the concurrency test on real row locks) |
| Frontend unit (vitest) | 12 / 12 |
| Type-check / lint | `tsc --noEmit` clean · `ruff check` clean |
| Production build | `next build` passes (built by the e2e harness) |
| Browser E2E, SQLite API | **9 / 9** (Chromium, production build) |
| Browser E2E, PostgreSQL API | **9 / 9** |
| Manual UAT | not performed as a separate pass; every case below is automated or marked open |

Postgres was a local, throw-away PostgreSQL 16 cluster; Alembic `0001`→`0002` applied cleanly (22 tables).

## Traceability

| ID | Covered by | Status |
|---|---|---|
| ENV-01 health + DB | `test_ENV_01_health_reports_database_status` | pass |
| ENV-02/04 routes load, no console errors | e2e `ENV-02/04/07` (12 routes incl. cohort detail) | pass |
| ENV-03 seed reset | e2e `ENV-03` (mutate → reseed → baseline) | pass |
| ENV-05 375px overflow | e2e `ENV-05` (13 routes) | pass |
| ENV-06 production build | e2e harness `next build` | pass |
| ENV-07 API contract | pytest `test_ENV_07…` (every view, 6 cohorts) + e2e failed-call watch | pass |
| FIN-01…12 controlled cohort | `test_FIN_01_to_12_controlled_cohort`, `test_FIN_11_break_even…` | pass |
| FIN-13 no float error | `test_FIN_13_no_float_error_on_cent_values` | pass |
| FIN-14…17 invalid inputs | `test_FIN_14_to_17…` (9 parametrised cases) | pass |
| FIN-18 return impact | `test_FIN_18_return_reverses_revenue_and_margin` | pass |
| Reconciliation equations | `test_reconciliation_identities…`, `test_reconciliation_identity_on_every_seeded_cohort` | pass |
| APP-01…10 | `test_fin_app.py` (APP_01, 02/03/04, 05-08, 09, 10) | pass |
| CAP-01…12 | `test_cap_ops.py` (incl. exact capacity, +0.01h, sub-cent, double-cancel, concurrency) | pass (SQLite + PostgreSQL) |
| OPS-01…12 | `test_cap_ops.py`; OPS-07 also e2e `ITAD-002 deterioration` | pass |
| CASH-01…12 | `test_cash_coc.py`; e2e `ENT-003` | pass |
| COC-01…12 | `test_cash_coc.py`; e2e `COC` and `COC-05` | pass |
| DASH-01…06, 09, 10 | `test_dash_rbac_env.py` (independent recomputation from source records) | pass |
| DASH-07 filters | `backend/tests/test_dashboard_filters.py` (45 tests) + `frontend/e2e/dashboard-filters.spec.ts` (6) — mapping below | pass (SQLite) |
| DASH-08 drill-down from every metric | `backend/tests/test_drilldowns.py` (128 tests) + `frontend/e2e/drilldowns.spec.ts` (8) — mapping below | pass (SQLite) |
| Roles (Test set 9) | `test_roles_*`, `test_override_requires_a_rationale…` | pass — **authorization only, not authentication** |
| Mobile smoke | e2e `Mobile smoke` | pass |
| Postgres gate | full suite + e2e on PostgreSQL 16 | pass (local) |

## Defects the program found and fixed

1. Assigned hours drifted from planned hours by up to ~0.08h on long assignments (per-day rounding) → residue now reconciled to the cent.
2. **Two concurrent capacity requests both succeeded and double-booked a cell** → claims are now atomic conditional updates (+ row locks on PostgreSQL).
3. Sub-cent remainder after a split crashed the scheduler → treated as fully assigned.
4. Cancelling an assignment twice would have restored capacity twice → second cancel rejected.
5. Alert rules ran before pending changes were flushed, so a quarantine's critical alert appeared one request late → evaluation flushes first.
6. Only the first cohort's open wipe failure could raise an alert (a stray `break`) → one alert per cohort, and none when a device-level alert already covers it.
7. Payout lag was being taken from partner payment terms (inflating days-to-cash) → only used when the partner is the payer.

## Known limits / not claimed

- No authentication; `X-Role` is spoofable authorization scaffolding. Finance "conditional" approval and a controlled-adjustment workflow for closed cohorts are not built (closed cohorts are simply frozen).
- CI workflow is written but has never run on GitHub.
- No backup/restore test, structured logging, rate limiting or secrets management. Not production-ready.


## DASH-07 — exact test mappings (Phase 2)

| # | Requirement | Test |
|---|---|---|
| 1 | Unfiltered dashboard == pre-filter global baseline | `test_DASH07_01_unfiltered_dashboard_matches_the_pre_filter_global_baseline` (against `tests/fixtures/dashboard_baseline.json`, captured from the pre-Phase-2 code) |
| 2 | Filter to EDU-001 | `test_DASH07_02_filter_to_one_cohort_reflects_that_cohort_only` |
| 3 | Filter by partner | `test_DASH07_03_partner_filter_reconciles_to_that_partners_records` |
| 4 | Filter by robot cell | `test_DASH07_04_robot_cell_filter_scopes_utilisation_and_throughput_to_that_cell` |
| 5 | Filter by date range | `test_DASH07_05_date_range_includes_only_records_in_range` |
| 6 | Filter by resale channel | `test_DASH07_06_resale_channel_filter_reconciles_to_matching_transactions` |
| 7 | Multiple filters = intersection | `test_DASH07_07_multiple_filters_are_an_intersection_never_a_union`, `test_filters_never_increase_scope`; e2e "multi-filter combination intersects…" |
| 8 | Clear filters restores global totals exactly | `test_DASH07_08_clearing_filters_restores_the_exact_global_values`; e2e "Clear filters restores the exact global dashboard" |
| 9 | Refresh keeps filters and results | `test_DASH07_09_…` (stateless idempotent URL); e2e "selecting a filter writes it to the URL… survives a refresh" and the shareable-URL test (fresh tab) |
| 10 | Invalid date range blocked | `test_DASH07_10_invalid_date_ranges_are_blocked_with_a_clear_message` (4 cases); e2e "invalid date ranges are blocked…" (UI message + hand-edited URL + unknown id) |
| 11 | No-match → correct zero state | `test_DASH07_11_no_match_produces_a_clean_zero_state` (strict-JSON, no NaN); e2e "a no-match combination shows an explained zero state" |
| 12 | Card totals reconcile to source records under every filter | `test_DASH07_12_…` parametrised over **18 filter sets**, each recomputed from raw DB rows |
| — | Typed validation | `test_typed_validation_rejects_bad_input_with_422` (11 cases) |
| — | Semantics published / labelled | `test_semantics_are_published_with_the_response`; e2e "dated and current-state metrics are labelled as such" |

Phase 2 run (SQLite): backend 171 passed / 1 skipped (DASH-08, Phase 3) · frontend unit 12/12 · `tsc` + `ruff` clean · Chromium E2E 15/15 on a fresh production build.
PostgreSQL was **not** re-run in this phase.


## DASH-08 — exact test mappings (Phase 3)

| # | Requirement | Test |
|---|---|---|
| 1 | Realized CM detail sum == card | `test_DASH08_01_realized_cm_detail_sums_to_the_card` (also recomputes from raw rows; per-row CM = net − cost; header sentence) |
| 2 | Forecast CM detail sum == card | `test_DASH08_02_forecast_cm_detail_sums_to_the_card` |
| 3 | Active-cohort count == card | `test_DASH08_03_active_cohorts_count_matches_the_card` |
| 4 | Cash components sum; no double count | `test_DASH08_04_cash_tied_up_components_sum_and_nothing_is_double_counted` |
| 5 | Overdue outstanding sum == card | `test_DASH08_05_overdue_collections_outstanding_sums_to_the_card` (+ independent recomputation from sales/invoices/receipts) |
| 6 | Utilization recomputed == dashboard formula | `test_DASH08_06_robot_utilization_is_weighted_not_averaged` (maintenance block makes denominators unequal, proving the mean of row % would be wrong); e2e "weighted ratio: utilization…" |
| 7 | Exception numerator / denominator reconcile | `test_DASH08_07_exception_rate_numerator_and_denominator_reconcile`; e2e "exception-rate detail…" |
| 8 | Critical-alert count == card | `test_DASH08_08_critical_alerts_count_matches_the_card` |
| 9 | Aged-inventory total / count == card | `test_DASH08_09_aged_inventory_total_and_count_match_the_card` |
| 10 | Filters persist into every destination | `test_DASH08_10_…` — **9 metrics × 10 filter sets (90 cases)**: reconciles to the dashboard, echoes the requested filters, shows the Current / date-window label, carries the channel and cell limitation notes; e2e "every dashboard card opens its destination…" clicks all 9 cards under `range=last_30_days` and asserts path, params and filter in the URL |
| 11 | Back to filtered dashboard restores state | `test_DASH08_11_…` (echoed filters) + e2e "Back to filtered dashboard restores the original filter state" (3 filters, card values identical after return) |
| 12 | CSV totals == UI totals | `test_DASH08_12_csv_export_reconciles_to_the_api_totals` (9 metrics); e2e "CSV export from the UI reconciles to the visible total" (real download) |
| — | Weighted ratios | `test_cm_pct_is_total_cm_over_total_net_revenue_not_the_mean_of_row_pcts`, `test_weighted_ratios_hold_under_a_filter` |
| — | Zero state | `test_zero_data_drilldown_is_a_stable_zero_state` (9 metrics + CSV); e2e "a zero-data drill-down is a stable zero state" |
| — | Validation / read-only | `test_drilldown_validates_like_the_dashboard` (7), `test_drilldown_is_read_only_and_repeatable` |
| — | Keyboard / mobile | e2e "cards are keyboard accessible" (focus + Enter), e2e "drill-down pages never overflow… at 375px" |

Phase 3 run (SQLite, 2026-10-01): backend **299 passed, 0 skipped** · frontend unit 12/12 · `tsc` + `ruff` clean · fresh production build · Chromium E2E **23/23** (9 acceptance + 6 filters + 8 drill-down).
PostgreSQL was **not** re-run in this phase.


## Final release run (Phase 4, 2026-09-30)

SQLite — `make verify`: backend **299 passed** · frontend unit **12/12** · `ruff` clean · `tsc` clean · production build clean · Chromium **23/23**.
PostgreSQL 16.15 — `make verify-postgres`: `alembic upgrade head` + `alembic check` clean · backend **299 passed** · Chromium **23/23**.
Full evidence and limitations: `RELEASE_EVIDENCE.md`. Earlier per-phase lines above are historical and superseded by these counts.
