# Revise Recovery OS — Release Evidence

| | |
|---|---|
| **Version** | `0.1.0` |
| **Release date** | 2026-09-30 (local verification date) |
| **Commit SHA** | `PENDING_GIT_INITIALIZATION` (this folder is not yet a Git repository) |

**Data classification:** Synthetic, illustrative portfolio data only. This application does not use, represent, or reproduce Revise Robotics' internal data, customers, contracts, pricing, operations, workflows, or policies.

## Verified locally (2026-09-30)

All results below were produced by the commands shown, on the final code, on one Mac (Python 3.12, Node 22, Playwright Chromium).

| Check | Command | Result |
|---|---|---|
| Backend tests — SQLite | `make verify` (→ `make test-backend`) | **299 passed**, 0 failed, 0 skipped |
| Backend tests — PostgreSQL 16.15 | `make verify-postgres` | **299 passed**, 0 failed, 0 skipped |
| Alembic migrations on PostgreSQL 16 | `alembic upgrade head` + `alembic check` | applied cleanly; "No new upgrade operations detected" (models == migrations) |
| Frontend unit tests (vitest) | `make test-frontend` | **12 / 12 passed** |
| Chromium E2E — SQLite | `make e2e` (inside `make verify`) | **23 / 23 passed** |
| Chromium E2E — PostgreSQL 16.15 | `make verify-postgres` | **23 / 23 passed** |
| Backend lint (ruff) | `make lint` | clean |
| Frontend type-check (tsc) | `make typecheck` | clean |
| Production frontend build | `make build` | clean (plus a fresh build inside every E2E run) |
| Dashboard filter acceptance (DASH-07) | included above | 45 backend tests + 6 Chromium tests, all passed |
| Dashboard drill-down acceptance (DASH-08) | included above | 128 backend tests + 8 Chromium tests, all passed |

Chromium composition (23): 9 acceptance, 6 dashboard-filter, 8 drill-down. Environment: macOS, PostgreSQL 16.15 (Homebrew) in a private, temporary, project-local cluster on 127.0.0.1 (removed after the run), SQLite via SQLAlchemy 2. ID-by-ID mapping: `docs/ACCEPTANCE_TESTS.md`.

Defects found and fixed during final validation (real code defects, tests not weakened):
- The test-support reseed endpoint could fail on PostgreSQL when the previous browser test still had requests in flight (`DROP TABLE` waited on locks). It now uses a bounded lock timeout and retries; the full PostgreSQL run was repeated and passed.
- Seeded exception events used the wall-clock date instead of their operating day (found in Phase 3).
- `scripts/pg_verify.sh` mis-handled a project path containing a space; fixed and the script now prints the server log on failure.

## Functional scope verified

- Cohort underwriting and base / downside / upside scenario economics
- Authoritative CM% based on net recognized revenue (Decimal; null when net revenue ≤ 0)
- Capacity planning with concurrent-allocation protection (atomic claims; row locks on PostgreSQL)
- Multi-cell cohort allocation with exact hour and unit reconciliation
- Plan-versus-actual operational tracking, reforecast and alerts
- Device-level custody log (append-only)
- Sanitization, QC, listing, sale, return, recycle, destroy and closeout gates
- Cohort, partner, operational, resale, cash and collections views
- Backend-driven dashboard filters with URL persistence
- Dashboard-to-detail reconciliation for all nine actionable cards
- CSV exports that reconcile to visible totals
- SQLite and local PostgreSQL 16 compatibility

## Current limitations

- No authentication.
- The demo-only `X-Role` authorization header is spoofable and is not production authorization.
- No backup and restore validation.
- No production logging, tracing, or monitoring.
- No rate limiting.
- PostgreSQL verified only against a local ephemeral PostgreSQL 16, not a hosted production deployment.
- The GitHub Actions workflow is prepared but has not been run remotely.
- No historical snapshot tables for past point-in-time inventory, cash exposure, cohort status, partner ranking, or alerts (these are labelled "Current").
- Resale-channel margin is recovery margin, not fully allocated channel contribution margin.
- The robot-cell filter identifies cohorts using the selected cell; it does not allocate all cohort economics to the cell.
- Seed data and policies are illustrative and are not Revise Robotics internal assumptions.

## Production release gate

- [ ] Authentication and secure session handling
- [ ] Server-side role-based access control tied to authenticated identities
- [ ] Audit-log retention policy
- [ ] Database backup and restore test
- [ ] Hosted PostgreSQL deployment test
- [ ] Secrets-management review
- [ ] Structured application logging
- [ ] Error monitoring and alerting
- [ ] Rate limiting
- [ ] Security review
- [ ] CI run on remote GitHub Actions
- [ ] Production environment smoke test
- [ ] Data retention and privacy policy
- [ ] Real operational data integration and validation
