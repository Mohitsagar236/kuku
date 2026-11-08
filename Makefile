PY ?= .venv/bin/python
SHOW_DIR ?=

.PHONY: doctor docs ui install test lint fmt typecheck check ingest-check candidates run run-rules smoke package-outputs check-confidential

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -q -e ".[dev]"

test:
	$(PY) -m pytest --cov --cov-report=term-missing

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

fmt:
	.venv/bin/ruff check --fix . && .venv/bin/ruff format .

typecheck:
	.venv/bin/mypy libs/common

check: lint typecheck test

# parse a show folder and print stats only (no script text)
ingest-check:
	$(PY) -m ingestion_service "$(SHOW_DIR)"

# candidate counts + projected LLM cost for a show (statistics only, no script text)
candidates:
	$(PY) -m candidate_service "$(SHOW_DIR)" -v

# THE one command: parse -> candidates -> LLM scoring -> rank -> evaluate -> write outputs/.
# Needs LLM_PROVIDER / LLM_MODEL / <PROVIDER>_API_KEY in .env (see .env.example).
run:
	$(PY) -m promo_finder_cli run "$(SHOW_DIR)"

# No LLM, no key: rules-only ranking (free baseline)
run-rules:
	$(PY) -m promo_finder_cli run "$(SHOW_DIR)" --provider none

# A few episodes through the REAL LLM first: prints calls, output tokens/candidate,
# JSON rejection/retry counts and truncation, before you spend a day of quota.
# usage: make smoke SHOW_DIR=... EPISODES=1-3
EPISODES ?= 1-3
smoke:
	$(PY) -m promo_finder_cli smoke "$(SHOW_DIR)" --episodes "$(EPISODES)"

# Zip the latest run + rules baseline for PRIVATE upload (outputs quote the script: never commit)
package-outputs:
	$(PY) -m promo_finder_cli package

# Fails if any file git would commit contains a long passage from the input folder
check-confidential:
	$(PY) -m promo_finder_cli check-confidential "$(SHOW_DIR)"

# React viewer for outputs/ (local only, no build step): http://127.0.0.1:8765
PORT ?= 8765
ui:
	$(PY) frontend/server.py --port $(PORT)

# Preflight before a real run: .env, provider reachable, model exists, 1 JSON test call,
# daily quota left vs calls needed, input folder parses. No secrets printed. NO_CALL=1 skips the call.
doctor:
	$(PY) -m promo_finder_cli doctor $(if $(SHOW_DIR),"$(SHOW_DIR)") $(if $(NO_CALL),--no-call)

# Solution doc: public docs/SOLUTION.pdf (no quotes) + private docs/SOLUTION_submission.pdf (git-ignored); fails if either exceeds 2 pages
docs:
	$(PY) docs/build_solution.py
