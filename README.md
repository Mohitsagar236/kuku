# Promo Finder

Find ranked 20–60 second promos in timestamped microdrama screenplays. Each result includes ordered episode cuts, source-backed hooks and cliffhangers, ranking reasons, confidence and quality checks. Promos can be continuous or stitched.

The application is a Python modular monolith with a FastAPI editor API and a locally bundled React viewer. See [architecture and operating limits](docs/ARCHITECTURE.md) for boundaries, migration decisions and deployment scope.

## Install

Requires Python 3.12+, Node 22.12+ and npm to build the viewer (CI uses Node 24).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .
make frontend-install frontend-build
cp .env.example .env
```

A built wheel includes the viewer, prompts and configuration; end users installing that wheel only need Python. `make install` creates the same locked editable development installation.

## Run

```bash
SHOW="path/to/show-folder"
make run-rules SHOW_DIR="$SHOW"           # free, no API calls
make doctor SHOW_DIR="$SHOW"              # provider preflight; one live test call
make smoke SHOW_DIR="$SHOW" EPISODES=1-3  # small live validation
make run SHOW_DIR="$SHOW"                 # full analysis
make ui                                   # http://127.0.0.1:8765
```

The viewer browses runs and robustness variants, downloads CSV/cut lists, displays quality reports, saves editor verdicts and suggests valid cut adjustments. It needs no browser internet connection after installation. Arrow keys or j/k move through promos. Review data lives in `outputs/<show>/editor_feedback.yaml`; refresh report metrics with `make feedback RUN=outputs/<show>/<run>`.

`python -m promo_finder --help` or `.venv/bin/promo-finder --help` lists commands. `--episodes`, `--no-ablation`, `--stability`, `--allow-partial`, `--no-cache` and `--out` control individual runs. Run `compare` for two smoke results, and `package` for a private output archive. The old internal Python module paths have been replaced; existing Make targets and output formats are preserved.

## Models and configuration

Select a provider in `.env`: `gemini`, `groq`, `anthropic`, `openai`, `openai_compatible`, `ollama`, `mock` or `none`. Set `LLM_MODEL` and the relevant provider key. For a compatible gateway, set `LLM_BASE_URL`, `LLM_MODEL`, optional `LLM_API_KEY` and optional JSON `LLM_EXTRA_HEADERS`. CLI `--provider` and `--model` override one run. `.env.example` lists settings.

Use `LLM_MAX_CONCURRENCY`, `LLM_RPM` and `LLM_RPD` for the provider key's actual limits. `LLM_BUDGET_CALLS` caps logical calls atomically; retries remain part of each call. `LLM_BUDGET_USD` is based on observed token usage, so concurrent in-flight calls can exceed it. Run only one analysis process per provider key. Responses are cached in `.cache/llm` for resume; changed inputs/prompts/models require new judgments.

Versioned defaults are in `src/promo_finder/resources/config/`; prompts are in `src/promo_finder/resources/prompts/`. `PF_CONFIG_DIR` and `PF_PROMPTS_DIR` override them explicitly. Installed packages work outside the checkout. Relative `.env`, cache and output paths resolve from the working directory.

Previous live validation covered Gemini only. Other provider adapters have automated HTTP tests; run doctor and smoke before trusting a real endpoint. This architecture migration does not claim new live model validation.

## Outputs and confidentiality

Each completed run writes `outputs/<show>/<timestamp>/` with `promos.json`, `promos.csv`, `ranking.md`, `quality_report.md`, `run_metadata.json`, optional ablations and raw LLM replies. Full runs can refresh `rules_baseline/`. Files are replaced atomically; new runs become visible after all core artifacts are written.

The screenplay pack and derived output contain confidential content. `.env`, `.cache/`, `outputs/`, labels and private submission PDFs remain ignored. Do not publish them. `make check-confidential SHOW_DIR="$SHOW"` checks committable files, including PDFs, for screenplay passages. Inputs are sent only to the explicitly selected LLM provider during analysis; `none` makes no model calls.

## Develop and package

```bash
make check              # Python + frontend + architecture checks
# First browser-test setup: (cd frontend && npx playwright install chromium)
make browser-test       # live browser regressions against isolated synthetic runs
# If the default test port is occupied: PF_TEST_PORT=18877 make browser-test
make wheel              # built viewer + sdist/wheel under dist/
make docs               # existing two-page solution-document workflow
```

`requirements.lock` pins runtime dependencies; `requirements-dev.lock` pins the development environment; `frontend/package-lock.json` pins browser build dependencies. Review and regenerate them together when upgrading. CI runs the checks and verifies a clean wheel installation outside the repository.

The supported deployment is local: `.venv/bin/promo-viewer --port 8765 --outputs outputs`. The API has `/healthz`, `/readyz` and `/api/openapi.json`. A non-root multi-stage Docker recipe is included:

```bash
docker build -t promo-finder .
docker run --rm -p 127.0.0.1:8765:8765 \
  -v "$PWD/outputs:/app/outputs" promo-finder
```

Reviewing existing results works without the source pack. Cut adjustments require the source at the path recorded in run metadata; for containers, mount that directory at the same absolute path or generate the run inside the container. Private remote viewers require both `PF_ALLOWED_HOSTS` and `PF_VIEWER_PASSWORD` (HTTP Basic authentication). Multi-tenant hosting and public access to confidential outputs are not supported.

CLI exit codes: `0` success, `2` invalid input/configuration, `3` quota/budget stop, `4` smoke problems, `130` interrupted.

## Assignment handoff

See [requirement-by-requirement completion status](docs/ASSIGNMENT_COMPLETION.md).
The public two-page explanation is [SOLUTION.pdf](docs/SOLUTION.pdf). Keep the private
solution and output archive off the public repository.

```bash
# Validate an existing run against its source without making model calls
.venv/bin/python scripts/audit_deliverables.py "$SHOW" "$RUN" --json outputs/assignment-audit.json
# Package the supplied assignment run, original replies and private solution
.venv/bin/python scripts/prepare_submission.py
```

The submission archive is written under ignored `outputs/packages/`; it includes
byte-for-byte copies and SHA-256 checksums. It contains confidential derived text.

## Free Render demo

The root `render.yaml` deploys a **free synthetic demo**, with no paid disk and no API keys.
Create a Render Blueprint from this repository. The Blueprint sets `PF_DEMO_MODE=true`
and derives the allowed hostname from Render automatically. Startup seeds a fictional
six-episode story with deterministic mock scores. The editor, review notes, cut nudges,
variants and downloads work against isolated demo data. Shared reviews reset when the
free instance restarts or sleeps; the UI labels this explicitly.

Demo mode always uses its own `/tmp/promo-finder-demo/outputs`, regardless of the
private outputs path supplied to the app. Never upload the assignment's screenplay pack
or private output archive to this public demo. The bundled sample scripts are fictional
rehearsal fixtures, not Kuku content.

For a private remote viewer, leave `PF_DEMO_MODE` unset, configure a password and
allowed hostname, and provision persistent storage separately.
