# Interview runbook: new show, maybe their model

Python 3.12 and `make` needed. The viewer needs internet (it loads React from unpkg).
Measured = I ran it. Estimate = arithmetic, not timed.

## 1. Fresh clone to results (about 5 minutes + the run)

```bash
git clone <repo-url> promo-finder && cd promo-finder
make install                         # venv + dependencies
cp .env.example .env                 # then edit .env (section 2)
make doctor SHOW_DIR="/path/to/new-show"          # PASS/FAIL per check, prints no secrets
make smoke  SHOW_DIR="/path/to/new-show" EPISODES=1-3   # 3 episodes on the real model
make run    SHOW_DIR="/path/to/new-show"          # full run -> outputs/<show>/<time>/
make ui                                            # http://127.0.0.1:8765
```

No key or no time? `make run-rules SHOW_DIR=...` needs no key (about 13 s for 50 episodes, measured).
Read first: `outputs/<show>/<time>/ranking.md` (top 10) and `quality_report.md` section 9 (where it picks badly).

## 2. Switch to their model in 1 minute (edit `.env`, no code)

```bash
# Anthropic
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
LLM_MODEL=<model id they give you>          # default if blank: claude-haiku-5-5

# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=...
LLM_MODEL=<model id>                         # default if blank: gpt-4.1-mini

# Any OpenAI-style gateway / hosted model  (provider: openai_compatible)
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://their-host/v1           # the URL that ends before /chat/completions
LLM_API_KEY=...                              # optional; sent as "Authorization: Bearer"
LLM_MODEL=<model id>
LLM_EXTRA_HEADERS={"X-Org": "kuku"}          # optional JSON object of extra headers
```

Set the limits to their key's real ones: `LLM_RPM` (requests/min) and `LLM_RPD` (requests/day),
and raise `LLM_MAX_CONCURRENCY` (default 2) if the key allows it. Then `make doctor`
(checks reachability, that the model exists, one JSON test call), then `make smoke`.
One-off without editing `.env`: `--provider X --model Y` on any command.

**Honest status:** only Gemini has been run against a real service. Anthropic, OpenAI, Groq and
Ollama are tested against mocked HTTP only. `openai_compatible` is tested against a local stub
server. That is why `doctor` and `smoke` come before `run`.

## 3. How long it takes and how many calls

- Calls (measured on the 50-episode show; `make doctor` prints this for any show): about 70 for the
  main ranking (at most 78). Rule of thumb: candidates / 8 per episode, plus 1 call per 6 of the top
  60 for blind clarity, plus 2 rerank passes.
- Ablations (on by default) re-score what they change: up to about the same again. Time-boxed? `--no-ablation`.
- Measured: 3-episode smoke = 10-12 calls. 6-episode unseen show = 17 calls, 84 s wall time
  at `LLM_RPM=10`, 2 workers. Per-call latency 1.5-3 s on the free Gemini model.
- Estimate, free tier (`LLM_RPM=10`): 70 calls is at least 7 minutes, and the rate limit, not the
  model, is the bottleneck. With ablations, plan 15 minutes. `LLM_RPD=200` covers one show per day.
- Estimate, paid tier (RPM 60+, concurrency 4): latency-bound, about 70 calls x 2-3 s / 4 = 1 to 2
  minutes for the main ranking.

## 4. Knobs if time is short

| Knob | Where | Effect |
|---|---|---|
| `--episodes 1-10` | run / smoke | only those episodes |
| `--no-ablation` | run | skips the two robustness re-runs (biggest saving) |
| `scoring.cascade_top: 60 -> 20` | `config/pipeline.yaml` | blind-clarity calls drop from about 10 to about 4 |
| `candidate.top_n_per_episode: 8 -> 4` | `config/pipeline.yaml` | about half the scoring calls, fewer candidates |
| `LLM_MAX_CONCURRENCY`, `LLM_RPM` | `.env` | faster if their key allows it |
| `--allow-partial` | run | on quota/budget stop, finish the rest on rule scores |
| `LLM_BUDGET_CALLS=50` or `LLM_BUDGET_USD=...` | `.env` | hard cap per run |

Stability re-scoring is already off by default (`--stability` turns it on).
Everything is cached: **re-running the same command resumes and re-bills nothing.**

## 5. If something breaks

| You see | Cause and fix |
|---|---|
| `doctor`: FAIL provider settings | missing key / `LLM_BASE_URL` / `LLM_MODEL`; fix `.env` |
| `doctor`: model not in its list | wrong model id; the line lists real ones |
| `doctor`: rejected the API key (401/403) | wrong key or wrong base URL |
| Exit code 3 "STOPPED: daily quota" | quota used up; re-run later (resumes from cache) or `--allow-partial` |
| Many `http_5xx` / 429 retries in the report | provider overloaded; lower `LLM_MAX_CONCURRENCY`, or wait |
| Smoke: rejected or invalid JSON answers | weak model; try a stronger one, or check `llm_raw/` (raw replies) |
| 400 about `temperature`, `max_tokens` or `response_format` | `openai_compatible` retries without them automatically |
| `doctor`: warnings, low dialogue %, 0 candidates | script format drifted; see `parse_warning`s; `tests/fixtures/rehearsal_show/` has examples of drift that already works |
| Output has fewer than 30 promos | small show or variety caps (2 per episode, 1 per scene); the report lists drops |
| Everything slow | rate limit, not code: see sections 3 and 4 |

Never commit `outputs/`, `.cache/` or `.env`. `make check-confidential SHOW_DIR=...` verifies.
