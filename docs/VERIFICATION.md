# Functional audit — 2026-11-08

The application is running at http://127.0.0.1:8765 using the current FastAPI server and bundled React viewer. The previous `frontend/server.py` process was stopped and replaced. The supplied path contains screenplay episodes, not a demo video, so this audit uses the README, interview runbook and assignment requirements as the feature checklist.

## Automated checks

| Check | Result |
|---|---|
| Final full Python suite | **327 passed**, one upstream deprecation warning |
| Coverage run before four new CLI edge-case tests | **323 passed, 95% coverage** |
| Browser regressions | **11 passed** against isolated synthetic runs |
| Frontend utility tests | **2 passed** |
| Ruff lint and formatting | Passed |
| Mypy | Passed for the full package; package plus browser-server script also passed |
| Import boundaries | Both contracts passed |
| Dependency consistency and Git whitespace | Passed |
| Production frontend build | Passed |
| Wheel and source distribution | Built successfully |
| Updated installed wheel outside checkout | Config, prompts, readiness, HTML, static assets and empty-state API passed |
| Confidentiality scan | No screenplay passages in committable files, including PDFs |

The browser suite now runs through `make browser-test` and is included in CI. It starts an ephemeral mock-backed server on port 8877; it never writes editor verdicts into the real show outputs. Install its browser once with `(cd frontend && npx playwright install chromium)`. An existing Chromium can be selected with `PF_BROWSER_EXECUTABLE`.

## Features exercised

| Feature | Evidence |
|---|---|
| Ingestion and candidate diagnostics | `make ingest-check` parsed all 50 episodes with no warnings; `make candidates` generated 428 candidates and cost projections |
| Configured Gemini connection | Live doctor call returned the requested JSON; model lookup passed |
| Fresh Gemini scoring | One real episode, 12 candidates, 3 uncached API calls, 1 delivered promo; zero invalid JSON, rejected answers, truncation, transport retries or rule fallback |
| Full real-show pipeline | 50 episodes, 30 promos, both ablations; completed using cached Gemini judgments with zero new scoring calls |
| Three-episode smoke and resume | Passed from the existing cache without new API calls |
| Source-backed cuts | All 30 promos in each of the main and two ablation rankings passed deterministic cut validation and hook-text checks against the actual source |
| Rules-only baseline | Produced during the full run; 30 results; regression suite also covers no-network operation |
| Run selection and ablation browsing | Browser tests passed; real 30-promo result and ablation also checked in the running application |
| Keyboard navigation | j/k selection tested in browser |
| Verdicts, clearing and reload persistence | Approve, would-post, reject and clear passed |
| Notes and immediate actions | Note plus immediate verdict and immediate promo navigation passed under delayed network responses |
| Cut nudging | Invalid cut, legal suggestions, accepted suggestion, persistence and reset passed |
| Clipboard and downloads | Timestamps copied; CSV and ranking downloads passed |
| Quality reports | Rendered in browser; CLI feedback refresh updated synthetic report metrics |
| Golden evaluation, feedback evaluation, stability | Passed on a fresh synthetic run with both ablations enabled |
| Model comparison | Compared the previous and new three-episode smoke artifacts successfully |
| Private packaging | Latest full run plus baseline archived; ZIP integrity and required contents checked |
| Empty state and recovery | Empty installation, failed run-list retry and refused-write recovery passed |
| Deep links | Variant and selected promo survived direct links and reloads |
| Mobile/offline assets | 390px viewport had no document overflow; local viewer issued no external requests |
| PDF build | Public and private variants rendered to temporary files; both stayed within two pages |

The real application was checked read-only: no synthetic approvals or notes were added to the real show. Private outputs and raw responses remain ignored by Git.

## Fixes made during this audit

1. **Dropped verdict after note entry:** the blur-triggered note save disabled/discarded a following click. Editor writes now queue, verdict requests include the current note, and acknowledgement updates preserve newer drafts.
2. **Wrong promo from an ablation link:** the requested promo was looked up in the main ranking. Selection now resolves against the requested variant.
3. **CLI label-file handling:** missing or malformed golden files are rejected before a model client is constructed or an output directory is reserved.
4. **Corrupt comparison artifact:** the CLI now returns a clean format error and exit code 2 instead of a traceback.
5. **Stale running viewer:** replaced the retired server process with the current application.

## Retained run evidence

- Full Gemini result: `outputs/i-married-a-broken-soldier-he-was-a-duke/20261108-153346/`.
- Cached three-episode smoke: `outputs/i-married-a-broken-soldier-he-was-a-duke/smoke-20261108-153516/`.
- Fresh Gemini smoke, including three raw responses: `outputs/i-married-a-broken-soldier-he-was-a-duke/smoke-20261108-153712/`.
- Full-run archive: `outputs/packages/i-married-a-broken-soldier-he-was-a-duke-20261108-153346.zip`.

## Limits

No actual demo video was supplied. Functional checks do not establish advertising conversion quality or replace an editor's content review. Gemini was tested live; other adapters passed mock/local-stub tests only. Docker is not installed here, so its recipe remains unbuilt. No public-hosting or load test was performed. The Python suite has one upstream Starlette warning about its httpx-backed test client; production requests and tests still work.

## Subsequent assignment completion audit

The earlier figures above describe the prior audit. The latest completion check
passes 341 Python tests at 95% coverage and 12 persistent browser tests, fixes
malformed artifact isolation, and refreshes the PDFs/distributions. See
[ASSIGNMENT_COMPLETION.md](ASSIGNMENT_COMPLETION.md) for requirement coverage,
retained source-validation evidence and the separate publication/submission status.
