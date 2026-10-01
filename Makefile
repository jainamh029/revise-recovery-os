# Revise Recovery OS -- synthetic, illustrative demo. Every target fails loudly (non-zero) if any step fails.
SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help setup setup-browsers seed reset-demo api web lint typecheck build test-backend test-frontend e2e verify \
        verify-postgres e2e-postgres disk-check clean-generated

VENV := backend/.venv/bin
need-venv:
	@test -x $(VENV)/python || { echo "Missing backend virtualenv (backend/.venv). Run: make setup" >&2; exit 1; }
need-node:
	@test -d frontend/node_modules || { echo "Missing frontend dependencies (frontend/node_modules). Run: make setup" >&2; exit 1; }
disk-gate:
	@scripts/require_disk.sh

help:  ## list targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | column -t -s $$'\t'

setup:  ## create the Python 3.12 venv and install backend + frontend dependencies (needs network)
	@command -v python3.12 >/dev/null || { echo "python3.12 not found on PATH" >&2; exit 1; }
	@command -v npm >/dev/null || { echo "npm not found on PATH (Node 20+ required)" >&2; exit 1; }
	cd backend && python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	cd frontend && npm install

setup-browsers:  ## one-time Chromium download for Playwright (needs network)
	cd frontend && npx playwright install chromium

seed: need-venv  ## create the schema and load the synthetic demo dataset (drops existing tables)
	cd backend && . .venv/bin/activate && python -m app.seed

reset-demo: seed  ## restore the pristine synthetic demo state; safe to run repeatedly

api: need-venv  ## FastAPI on http://localhost:8010 (docs at /docs)
	cd backend && . .venv/bin/activate && uvicorn app.main:app --port 8010 --reload

web: need-node  ## Next.js on http://localhost:3000 (proxies /api to :8010)
	cd frontend && npm run dev

lint: need-venv  ## backend lint (ruff). The frontend's static check is `make typecheck`.
	cd backend && . .venv/bin/activate && ruff check app tests

typecheck: need-node  ## frontend TypeScript type-check
	cd frontend && rm -rf .next/types && npx tsc --noEmit

build: need-node disk-gate  ## production frontend build
	cd frontend && npx next build

test-backend: need-venv disk-gate  ## backend tests on SQLite
	cd backend && . .venv/bin/activate && python -m pytest -q

test-frontend: need-node  ## frontend unit tests
	cd frontend && npm test

e2e: need-venv need-node disk-gate  ## Chromium E2E on SQLite (starts and stops its own API; needs `make setup-browsers` once)
	scripts/e2e.sh sqlite

verify: disk-gate test-backend test-frontend lint typecheck build e2e  ## everything on SQLite; fails on the first failing step
	@echo "VERIFY (SQLite) PASSED"

verify-postgres: need-venv need-node  ## isolated local PostgreSQL 16: migrations + backend tests + Chromium E2E; cleans up after itself
	scripts/pg_verify.sh all

e2e-postgres: need-venv need-node  ## Chromium E2E only, against an isolated temporary PostgreSQL 16
	scripts/pg_verify.sh e2e

disk-check:  ## report free disk space and generated-artifact sizes (deletes nothing)
	@scripts/disk_check.sh

clean-generated:  ## remove ONLY project-local reproducible artifacts (explicit; review `make disk-check` first)
	rm -rf frontend/.next frontend/.next-e2e frontend/test-results frontend/playwright-report frontend/coverage \
	       backend/.pytest_tmp backend/.pytest_cache backend/.ruff_cache .pgdata .e2e
