# Promo Finder

Reads a folder of microdrama screenplay episodes (`episode-NNN-*.txt`, timestamped scenes,
dialogue and action) and proposes ranked 20–60 s promos: for each one the episode, start and end
of every segment, the hook, the cliffhanger, one line on why it works, and a quality report on
where the tool picks badly. The LLM is swappable through `.env` only.

> **Confidential input.** Outputs quote the script. `outputs/`, `.cache/` and `.env` are
> git-ignored; never commit or publish them. `make check-confidential SHOW_DIR=...` fails if any
> committable file contains an 8-word passage from the input.

## Requirements

- Python 3.12 (`python3.12` on PATH)
- No API key needed for the rules-only mode. For LLM scoring, one free-tier key
  (Gemini or Groq), or a local Ollama model.
- The viewer loads React from unpkg.com, so the browser needs internet access.

## Setup

```bash
make install            # creates .venv and installs the package + dev tools
cp .env.example .env    # then set LLM_PROVIDER / GEMINI_API_KEY etc.
```

## Run

```bash
SHOW="path/to/show-folder"                   # folder of episode-NNN-*.txt files

make run-rules SHOW_DIR="$SHOW"             # free baseline, no LLM, no key
make smoke     SHOW_DIR="$SHOW" EPISODES=1-3 # a few episodes through the real LLM, health check
make run       SHOW_DIR="$SHOW"             # full run: parse -> candidates -> LLM scoring -> rank -> report
make ui                                       # viewer at http://127.0.0.1:8765 (PORT=... to change)
```

Each run writes `outputs/<show>/<timestamp>/`: `promos.json`, `promos.csv`, `ranking.md`,
`quality_report.md`, `run_metadata.json`, ablation rankings, and raw LLM replies in `llm_raw/`.
A full run also refreshes `outputs/<show>/rules_baseline/`. Re-running resumes from the response
cache in `.cache/llm`, so an interrupted run does not re-bill finished calls.

Other commands: `.venv/bin/python -m promo_finder_cli --help` (`compare` two smoke runs side by side,
`package` the latest run as a zip for private upload).

## Swapping the model

Set these in `.env`; no code changes:

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `gemini`, `groq`, `anthropic`, `openai`, `ollama`, `mock`, or `none` (rules only) |
| `LLM_MODEL` | blank = provider default |
| `<PROVIDER>_API_KEY` | key for the chosen provider |
| `LLM_RPM`, `LLM_RPD` | your key's real per-minute / per-day limits (the client throttles to them) |
| `LLM_BUDGET_USD`, `LLM_BUDGET_CALLS` | optional per-run caps; the run stops (or finishes on rules with `--allow-partial`) |

`--provider` / `--model` on the command line override `.env` for one run.

## The viewer

`make ui` serves a read-only page over `outputs/`: pick a run and ranking, step through promos
(↑/↓ or j/k), copy a promo's timestamps, download the CSV, and read the quality report. It binds
to 127.0.0.1 only and refuses requests for any other host name, because the outputs are
confidential. It is not meant to be deployed publicly.

## Tests and checks

```bash
make check    # ruff lint + format check, mypy, pytest with coverage
```

Tests use a mock LLM and synthetic fixtures; they never call a real provider, even if `.env`
holds a key.

## Exit codes

`0` ok · `2` bad input or configuration (missing folder, no parseable episodes, bad episode
range, unknown provider, missing key) · `3` stopped by quota or budget (re-run to resume) ·
`4` smoke run found problems (see its summary).
