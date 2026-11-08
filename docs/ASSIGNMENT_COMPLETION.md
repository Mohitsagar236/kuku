# Assignment completion audit - 8 November 2026

Scope: Kuku AI Generalist, **Problem 1: Find the Promos**. The brief asks for one
problem; Problems 2 and 3 are alternatives, not additional deliverables.

## Requirement coverage

| Requirement | Verified local result |
|---|---|
| Read the full input pack | Source hash matches the retained run; 50 episodes, 3,727 beats, no parse warnings |
| Find 20-60 second promos | 30 main promos, 20.3-60.0 seconds, from 27 episodes (run 20261108-184323) |
| Ordered episode/start/end for every segment | JSON, CSV and editor cut list; deterministic cut and timestamp checks passed |
| Hook within the first three seconds | Every delivered hook is inside the first three seconds and matches its source beat |
| Cliffhanger and why it works | Source-backed closing moment, rationale and posting-priority reason present on every promo |
| Rank what to post first and why | Contiguous ranks, priority reasons and ranking.md; top-three IDs in the PDF match the retained run |
| Show where it picks badly | Quality report, baseline, two ablations, risks and explicit limitations |
| Rerun on new input | Locked install, rules-only mode and model-backed CLI; synthetic/unseen-show regressions and fresh-wheel run |
| Swap models | Provider/model settings in .env; adapters tested with local/mock HTTP; Gemini checked live |
| Solution PDF, at most two pages | Updated public and private variants; both rendered and visually inspected as two pages |
| Prototype with run instructions | Current source archive, wheel and README prepared locally; publication status below |
| Unedited full-pack outputs | Retained completed run plus original model-reply provenance; private ZIP includes SHA-256 checksums |
| AI disclosure and a concrete mistake | Updated solution section 9 and AI_USAGE.md |
| Optional walkthrough, at most three minutes | Recording outline below; no own-voice video has been recorded |

## Current verification evidence

- `make check`: **351 Python tests passed, 95% coverage**, frontend utility tests,
  frontend production build, Ruff, mypy and both architecture contracts passed.
- `PF_TEST_PORT=18877 make browser-test`: **12 persistent browser tests passed**,
  covering filters, keyboard navigation, reviews, note races, cut suggestions/reset,
  exports, deep links, mobile width and request-failure recovery.
- A focused pipeline regression also passed after adding the source-audit check;
  intentionally removing CSV rows is detected as a ranking mismatch.
- `scripts/audit_deliverables.py` checked all **90 promos** across the main run and
  both ablations against the unchanged source pack: no violations, quote mismatches,
  timing errors, missing reasons or CSV order discrepancies.
- Gemini doctor: provider and model found, generic JSON-mode call passed, full-pack
  parsing/candidate diagnostics passed. The generic call sent no screenplay text.
  This audit reused existing full-pack judgments and did not claim a new live full run.
- Both solution PDFs rendered as two pages. Top-three IDs match run 20261108-184323.
- Fresh wheel installed with locked dependencies outside the checkout; packaged
  configuration/prompts/assets, rules pipeline, health/readiness, browsing, review
  persistence and CSV download checked independently.
- Confidentiality scan passed for committable files, including the public PDF.

## Fixed during this audit

1. Malformed JSON shapes could crash the run list or be served as valid runs.
   Artifact envelopes and promo records are now validated, unhealthy main runs are
   skipped, and corrupt direct loads/variants return a clear JSON error. Four new
   regressions complement the ten previously failing malformed-shape cases.
2. Solution text referenced retired resource paths and old verification figures.
   It now describes the current editor, identifies Codex assistance, points to the
   retained full run and avoids hard-coded test counts that would become stale.
3. Distributions predated the new editor. The latest frontend is bundled into fresh
   wheel/source packages; the source manifest includes run instructions, config
   example and verification documents while excluding private outputs.
4. Browser testing needed temporary copies when its default port was occupied.
   `PF_TEST_PORT` now configures the test runner and isolated synthetic server.

## Remaining quality limits

The application meets the engineering scope; it does **not** establish that every
suggested promo is a good advertisement. The main run's model flags **1 of 30** promos as needing context (it was **23 of 30** before the clarity fix described below) and 11 of 30 end at an episode's end; the report documents spoiler and dead-air risks.
These are visible failures, not human-labelled accuracy measurements. An editor
must approve the final choices. No human labels or endorsements were fabricated.
Stitched candidates are exercised by synthetic tests, but none reached the real
pack's final 30. Stronger models can be connected without code changes. Docker
was not available for an image-build test; public deployment is not required by
the brief and is outside the local editor's supported security model.

## Handoff files and actual submission status

- Public explanation: `docs/SOLUTION.pdf`.
- Private explanation with source excerpts: `docs/SOLUTION_submission.pdf`.
- Private output handoff: `outputs/packages/kuku-problem1-submission-20261108.zip`.
  Contains the completed run, baseline, original unedited full-run model replies,
  source validation result, private PDF and per-file checksums. No source pack,
  credentials, cache or editor labels are included.
- Current distributable source and wheel: `dist/`; `promo-finder-source.zip` is
  the source-only handoff with README and .env.example for publication/upload.
- Source audit result: `outputs/assignment-audit.json`.

**The placement-cell form has not been submitted.** On 8 November 2026, the user
confirmed they will submit it themselves. Publication/upload and final form receipt must be
verified separately from local technical completion.

The existing public repository `https://github.com/Mohitsagar236/kuku` opens, but
its inspected main revision `ef09f701a99c79a5a0931887cf5cbdf536d28a8c` contains the
older services/libs layout and lacks the current README/editor/docs. Do not
mistake it for this locally verified implementation. A source-only update must
be published, or the current source archive must be uploaded to an accessible
prototype link. Confidential outputs/private PDF belong only in the assignment's
review handoff; do not add them to that public repository.

## Optional recording outline (about 2 minutes 45 seconds)

1. 0:00-0:20 - State Problem 1, the 50-episode input and the editor-shortlist scope.
2. 0:20-1:00 - Show a ranked candidate, exact source hook/cliffhanger and segment times.
3. 1:00-1:35 - On synthetic data, demonstrate a verdict, note, suggested cut and export.
4. 1:35-2:10 - Open the quality report and explain the context/spoiler weaknesses.
5. 2:10-2:45 - Show rerun/model-switch commands and state the lack of human accuracy labels.

Record in your own voice. Hide .env values and keep any source-content recording
in the private review channel. The video is optional, so its absence does not make
the required local deliverables incomplete.

## Re-test 8 November 2026 (evening)

Independent re-test against the brief: all deliverable checks passed again, including
`make check` (351 tests, frontend lint/tests/build, Ruff, mypy, both architecture contracts)
and 12 browser tests. Fixed: rules-only runs (`LLM_PROVIDER=none`) no longer log a
misleading "DailyQuotaExhausted" warning or touch the rate-limit counter (new
`LLMDisabled` error plus a regression test). Also fixed: the 23-of-30 "needs context" weakness. Cause: the blind judge scored
clips 7-8 while naming a missing backstory. Change: `clarity_v3` (a named gap caps
clarity at 6), a blind-clarity gate of 5, clarity weight 0.20 to 0.25. A live re-run
(24 Gemini calls, scoring served from cache) produced run 20261108-184323, which is now
the submission run; the previous run 20261108-153346 stays on disk unedited. Result:
1 of 30 flagged, dead air 3 to 1, weak endings 14 to 10, hook/cliffhanger means unchanged,
audit script clean. Trade-off: promos ending at an episode's end rose from 1/30 to 11/30;
not human-checked. Still unchanged: no stitched promos reached the real top 30, 27 of 30
start mid-scene, and no human accuracy labels exist.

### Final-pass fixes (same evening)

- Cut-start bug: 13 of 30 promos began 0.1-0.6 s after a camera direction that frames their
  first line (including rank 1). Starts now snap back onto that shot when the cut still
  validates (new `_snap_starts`, 3 tests). Same 30 promos, same order, durations 20.3-60.0 s;
  new run `20261108-184323` (0 live calls, all judgments cached) replaces `-182755` as the
  submission run. `-153346` and `-182755` stay on disk unedited.
- Cache bug: response-cache keys ignored the provider, so a mock run and a Gemini run with
  the same model name shared entries. Non-Gemini providers are now namespaced; Gemini keys
  are unchanged so the retained run still resumes with 0 live calls (verified).
- Added `scripts/review_sheet.py` (blank 1-5 review sheet and a comparison against the model
  ranking). No ratings exist yet: **human validation is pending**.
- Examined by hand against the screenplay: rank 1 (hook weak as text but the weapon-raising
  shot sat 0.1 s before the cut, now included; the episode-end cut is the episode's own
  unresolved cliffhanger), rank 9 (ending is unresolved; spoiler concern fair: 56 s covers
  the whole rescue), rank 23 (weak end on a reaction shot; stronger beat only 4 s later, not
  changed: one case), rank 24 (justified: quiet exposition hook and dead air; ranked 24 of 30).
