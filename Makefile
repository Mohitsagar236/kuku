PY ?= .venv/bin/python
SHOW_DIR ?=

.PHONY: architecture frontend-install frontend-build frontend-check browser-test wheel feedback doctor docs ui install test lint fmt typecheck check ingest-check candidates run run-rules smoke package-outputs check-confidential

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.lock
	.venv/bin/pip install -q --no-deps -e .

test:
	$(PY) -m pytest --cov --cov-report=term-missing

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

fmt:
	.venv/bin/ruff check --fix . && .venv/bin/ruff format .

typecheck:
	.venv/bin/mypy src

architecture:
	.venv/bin/lint-imports

frontend-install:
	cd frontend && npm ci

frontend-build:
	cd frontend && npm run build

frontend-check:
	cd frontend && npm run lint && npm test && npm run build

browser-test: frontend-build
	cd frontend && npm run test:e2e

wheel: frontend-build
	$(PY) -m build

check: lint typecheck architecture test frontend-check

# parse a show folder and print stats only (no script text)
ingest-check:
	$(PY) -m promo_finder.ingestion "$(SHOW_DIR)"

# candidate counts + projected LLM cost for a show (statistics only, no script text)
candidates:
	$(PY) -m promo_finder.candidates "$(SHOW_DIR)" -v

# THE one command: parse -> candidates -> LLM scoring -> rank -> evaluate -> write outputs/.
# Needs LLM_PROVIDER / LLM_MODEL / <PROVIDER>_API_KEY in .env (see .env.example).
run:
	$(PY) -m promo_finder run "$(SHOW_DIR)"

# No LLM, no key: rules-only ranking (free baseline)
run-rules:
	$(PY) -m promo_finder run "$(SHOW_DIR)" --provider none

# A few episodes through the REAL LLM first: prints calls, output tokens/candidate,
# JSON rejection/retry counts and truncation, before you spend a day of quota.
# usage: make smoke SHOW_DIR=... EPISODES=1-3
EPISODES ?= 1-3
smoke:
	$(PY) -m promo_finder smoke "$(SHOW_DIR)" --episodes "$(EPISODES)"

# Zip the latest run + rules baseline for PRIVATE upload (outputs quote the script: never commit)
package-outputs:
	$(PY) -m promo_finder package

# Fails if any file git would commit contains a long passage from the input folder
check-confidential:
	$(PY) -m promo_finder check-confidential "$(SHOW_DIR)"

# Bundled React viewer, served by FastAPI/Uvicorn on loopback.
PORT ?= 8765
ui:
	$(PY) -m promo_finder.interfaces.web --port $(PORT)

# Refresh a run's quality_report.md with your viewer verdicts (precision@10, rejections and why).
# usage: make feedback RUN=outputs/<show>/<timestamp>
RUN ?=
feedback:
	$(PY) -m promo_finder feedback "$(RUN)"

# Preflight before a real run: .env, provider reachable, model exists, 1 JSON test call,
# daily quota left vs calls needed, input folder parses. No secrets printed. NO_CALL=1 skips the call.
doctor:
	$(PY) -m promo_finder doctor $(if $(SHOW_DIR),"$(SHOW_DIR)") $(if $(NO_CALL),--no-call)

# Solution doc: public docs/SOLUTION.pdf (no quotes) + private docs/SOLUTION_submission.pdf (git-ignored); fails if either exceeds 2 pages
docs:
	$(PY) docs/build_solution.py
