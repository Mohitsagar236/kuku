# AI usage log

Tool: Claude Code (Claude Sonnet 5.5) wrote the code; I reviewed and decide the design.
Format: what the AI got wrong -> how it was caught -> fix.

## Phase 0 / 1

1. **Wrong scene count.** The AI's first scan of the real data reported 106 scenes. The parser
   found 100. The scan regex `^SCENE ` also matched the 6 upper-case label lines
   (`SCENE HEADING/START/END`) in two files. Caught by comparing the scan with parser output
   (block counts matched exactly, scene count did not). Fix: trust the parser; scene count = 100.
2. **Test bug, not code bug.** `test_crlf_and_nbsp_are_normalized` inserted a non-breaking
   space after "Marta", which really adds a character ("Marta !"). The AI first suspected the
   parser. Fix: replace an existing space instead.
3. **Tooling rewrote my test.** `ruff format` converted the ` ` escape in the test source
   into a literal NBSP, so a later string-replace silently matched nothing. Fix: rewrote the
   test with `\\u` escapes after the formatter run and checked the file bytes.
4. **Plan gaps the AI missed on first pass** (found by re-reading the assignment PDF and the
   data): the brief wants a 2-page solution doc and a free-tier, no-Docker-required build; speaker
   labels are role names (`THE FORMER GROOM`) so name-based identity is unreliable.

## Phase 2

5. **Recap dedupe deleted legitimate candidates (logic bug).** First version compared candidates
   from different episode sets by shared dialogue lines. On the real show it removed 31 candidates
   although only 3 beats in the whole show are real recaps. 18 were singles dropped because a
   stitched candidate *reused the same beats*. Caught by sanity-checking "31 removed vs 3 recap
   beats". Fix: fingerprints remember the source beat of every line; only lines from DIFFERENT
   source beats count as a replay. Regression test added. Result on real data: 0 removed.
6. **Recap matcher took the first match, not the best.** Found by a failing test: templated lines
   all mapped to one source line. Fix: best-ratio match plus a word-level Jaccard guard (>=0.75)
   so lines that differ by one key word are not treated as replays.
7. **Weak test I wrote:** `assert ... or True` made a validation test pass vacuously, which hid a
   wrong expectation (a cut at 31.0 s is in a gap, not mid-line). Found on review; fixed.
8. **Synthetic test data collided by accident** (3 times): with a 100-word vocabulary, two
   "different" episodes produced identical sentences, which the recap detector correctly
   flagged. The detector was right, the fixtures were wrong. Fix: per-episode word stride.
9. **Verified the property tests are not vacuous**: 33/40 random shows yield candidates (17 with
   stitched); a mutant that disables the cut rule is killed.

## Phase 3

10. **Ablation silently re-paid for every candidate (cache-key bug).** My redactor normalised
    whitespace even when it removed nothing, which changed every episode's context string and so
    every cache key. Caught from the mock run's own numbers: 0 cache hits on the main scoring
    call in the ablation run (expected ~300 of 429). Fix: the redactor returns the input
    byte-identical when nothing matched. Regression test asserts the exact hit count.
11. **Another vacuous assertion** (`assert ... is False or True`), same pattern as item 7. Found on
    review of my own tests; replaced with an assertion that can fail. Lesson: grep tests for `or True`.
12. **Test data used timestamps that are not beat boundaries** (25.5 s, 40.5 s) in the ranking
    tests: the failures were mine, not the code's. The tests now derive times from the fixture.
13. **Duplicate promo found only by reading real output.** The rules-only top 10 had #5 and #10
    ending on the identical cliffhanger line; IoU dedupe could not see it (little time overlap).
    Added a same-ending dedupe with a test.
14. **Likely silent failure on Gemini (not yet observed live).** Gemini 2.5 "thinking" tokens count
    against the output limit and could truncate the JSON. Found by reviewing the adapter, not by a
    test. Fix: thinking disabled for non-Pro models, plus a fallback if a newer model rejects the field.
15. **Script text leaked into a log line** (parser warning included a text snippet). Removed; logs
    now carry codes and counts only.
16. **Unverified, to be tested live:** adapters are tested against mocked HTTP only. Default model
    names and free-tier limits are my assumptions, not checked against the providers today.

## Phase 3.5 (smoke, cascade, baseline, confidentiality)

17. **My first leak test was wrong, not the checker.** I planted a "quote" extracted with a regex
    between two quote marks; it grabbed formatting text between two lines, not script text, so the
    scanner (correctly) found nothing and I briefly read that as "checker broken". Redone with a
    real 15-word beat from the parsed input: caught (exit 1), then clean (exit 0).
18. **Leak findings were noisy:** one 14-word quote produced 7 findings (one per overlapping
    shingle). Collapsed to one finding per file/line with a match count.
19. **Lowering the episode-end weight (0.45 -> 0.25) helped less than assumed.** Rules baseline: 19 of
    30 delivered promos still end at an episode end (was 26 of 30), 7 of the top 10. Reported as is;
    the prior-off ablation is there to measure it, no cap added as requested.
20. **Design trap caught before it shipped:** with a cascade, candidates that skip the blind judges
    would carry rule-only clarity and could outrank fully scored ones on an incompatible scale.
    Only fully scored candidates are rankable; the shortlist uses the final dedupe/diversity logic
    with caps loosened 2x for headroom.
21. **Tests I had to rewrite because I changed behaviour** (stability now opt-in, ablations are a
    list). Expected, but note the suite only caught it because the e2e tests assert on report text.
22. **Mock numbers are not evidence.** Every "LLM" number in this phase (overlaps, 40 output
    tokens/candidate, ablation verdicts) comes from the deterministic mock. They prove the wiring,
    not the model. Real numbers come from the smoke and full runs.

## First real-LLM run (Gemini smoke)

23. **Default model was wrong.** I had set `gemini-2.5-flash` from memory (flagged as unverified
    earlier). The live model list showed newer models, and `gemini-2.5-flash-lite` is "no longer
    available to new users". Chose `gemini-3.5-flash-lite` after probing JSON output.
24. **Adapter fallback relied on a hint that does not exist.** `gemini-3.5-flash-lite` rejects
    `thinkingBudget: 0` with a bare `INVALID_ARGUMENT`; my fallback only reacted to messages that
    mentioned "thinking". Fixed to drop optional parameters on a hint-free 400, plus a regression test.
25. **Case-sensitive id matching rejected ~30% of real answers.** Gemini answered `"id": "C1"`
    (copying my prompt's uppercase header); I matched `c1`. The mock always answered in lowercase, so
    169 tests missed it. Smoke caught it: 11/36 rejections. Fixed (case-insensitive + lowercase
    labels in prompts); second smoke: 0 rejections, 0 retries, 0 truncations.
26. **Two more real-model habits, found only in llm_raw:** an unescaped double quote inside "why"
    (invalid JSON) and re-using a neighbouring candidate's hook id. Prompt hardened for both.
27. **Quota file name bug:** `Path.with_suffix` treated `.5-flash-lite` as an extension, so
    `gemini-3.5-...` and `gemini-3.1-...` shared one daily counter (`quota_gemini_gemini-3.json`).
    Found by looking at the state file, not by a test. Fixed with a regression test.
28. **A test hazard I created:** once `.env` held a real key and provider, any test calling
    `LLMSettings()` could have spent real quota. I verified none did (counter matched the smoke
    run exactly), then added an autouse fixture that forces the mock and removes all API keys.
29. **A latent flaky test since Phase 3.5:** `next(glob("*/*"))` can return `rules_baseline/`
    instead of the run directory; it only passed by directory-order luck. Fixed.
30. **Missing observability:** the 3 transient retries in the smoke log did not say which error
    occurred. Added the error type to the retry log.

## Audit pass on the viewer and CLI (2026-11-08)

31. **Two runs in the same second overwrote each other.** Run folders were named by
    `strftime("%Y%m%d-%H%M%S")` with `mkdir(exist_ok=True)`, so a second run in the same second
    wrote into the first run's folder. An existing e2e test had been failing on this. Fixed with an
    atomic `mkdir` that adds `-2`, `-3`; regression test added.
32. **A folder of non-screenplay `.txt` files "succeeded" with 0 promos** instead of saying the
    input was wrong. The loader now skips beat-less files with a `no_beats` warning and fails
    clearly if nothing parses.
33. **Unknown `--provider` / `LLM_PROVIDER` crashed with a traceback.** Now a one-line error, exit 2.
34. **The first viewer had no loading/empty/error states** (it showed "No promos." while loading
    and one failed request blanked the app), raw-Markdown report, mouse-only list, and a server
    that dropped the connection on a corrupt JSON file and answered any Host header (DNS
    rebinding could read the confidential outputs). All fixed, 15 server tests added.
35. **My own mistakes during that fix:** (a) server functions took `OUTPUTS` as a default
    argument, bound at import, so tests that patched it silently read the real folder (caught by
    the tests); (b) my mobile CSS override sat before the base rule and lost the cascade (caught
    in a headless-Chrome screenshot, not by any test).
36. **The quality report contradicted itself** (found while writing the solution doc, this pass by
    Claude Opus 5.5): §9 counted episode-ending promos on the *pre-refinement* candidate (14/30)
    while §6 used the delivered cut (1/30, correct); and "tokens per candidate" divided by
    `max(1, live calls)`, printing a phase total when every main answer came from the cache. Both
    fixed with regression tests; the delivered run's raw outputs were left unedited.
37. **Two gaps in the confidentiality guard** (pass A, Claude Opus 5.5): it skipped PDFs, and an
    8-word window misses short quoted lines. Added PDF text extraction and a 4-word quoted-dialogue
    check. Its first run found one real 4-word overlap (an invented test line that matched a line
    of dialogue; replaced) and one false positive of mine (a Python docstring read as a quotation;
    the scanner now ignores triple-quote delimiters).
38. **I wrote a test count into the doc before measuring it** (243; the suite has 242). Caught by
    running the suite before committing. Rule now: numbers in the doc come from a run, not a plan.
39. **Rehearsal on an invented, drifted show found two real bugs the 50-episode run never hit**
    (pass B, Claude Sonnet 5.5): (a) a speaker block with unquoted text and no parenthetical was
    read as an unknown block, so its lines lost their speaker and dialogue status; now promoted to
    dialogue only when that name speaks (quoted) elsewhere in the show, with tests including the
    negative case; (b) end refinement could move a promo's end onto another promo's ending *after*
    selection had de-duplicated, delivering two near-identical clips (the real run happened to have
    none). Fixed in `finalize`, with a regression test I checked fails when the fix is disabled.
40. **My own test mistakes while adding the doctor tests:** a base URL with `?token=` appended
    (breaks `/models`), assertions that were too weak to prove anything (`rc in (0, 1)`, a tautological
    estimate check), and a wrong attribute name. All caught by running the tests, then strengthened.


## Architecture migration (2026-11-08)

Codex inspected the application and migrated it to a single src-layout Python package, split pipeline contracts from CLI presentation, replaced the custom HTTP server with FastAPI/Uvicorn, and converted the CDN viewer to modular React built by Vite. It retained the existing algorithms, prompts and editor-feedback work. Added checks cover strict API input, stale screenplay detection, symlink confinement, concurrent call-budget admission and package installation. Whole-package typing replaced shared-library-only typing.

One implementation correction: the app-bundled Node runtime rejected Rollup's native module on macOS because of its signing policy. A standard Node runtime was used to build and test the viewer. Initial API tests also exposed unknown-POST routing and missing-build behavior; those were resolved before verification. No real LLM calls were made to validate this migration. See docs/ARCHITECTURE.md for supported deployment limits.


## Functional audit (2026-11-08)

Codex ran the complete suite and added a persistent Playwright browser suite and CI step. Browser tests reproduced two issues missed by API tests: a note blur-save could discard the following verdict click, and a deep link to an ablation promo was resolved against the main ranking. The editor now serializes writes and preserves drafts; selection resolves against the requested variant. Added CLI regressions reject invalid golden files before model calls and handle corrupt comparison JSON cleanly. Final results are recorded in docs/VERIFICATION.md. Fresh Gemini scoring used three real API calls on one supplied episode; the 50-episode run and both ablations reused cached judgments.


## Assignment completion audit and editor design (2026-11-08)

Codex refined the React editor with search, review filters, keyboard-aware candidate navigation, responsive layouts and a teal/sage/coral visual theme. It then compared Problem 1 against the six-page assignment brief, verified the unchanged 50-episode source and all 90 delivered cuts across main/ablation rankings without new screenplay/model calls, and refreshed the two-page solution documents and distribution packages. A generic Gemini doctor call checked connectivity without sending source text.

One error in the earlier test-only audit: it reported ten malformed-artifact regressions but did not fix them before UI work continued. The completion audit closes that gap. The run repository now validates artifact envelopes and promo records before listing or serving them, skips a malformed main run without hiding healthy runs, and returns a clean corrupt-artifact error on direct access. Regression coverage includes invalid metadata, missing segments, invalid moments and corrupt variants. A reusable source audit checks quotes, timing and CSV ranking, including a negative test that detects a damaged CSV.

The browser runner now accepts PF_TEST_PORT instead of requiring temporary test copies when the default port is busy. Source content, keys and confidential outputs remain excluded from public packages. Test evidence, remaining human evaluation limits, and actual publication/submission status are recorded in docs/ASSIGNMENT_COMPLETION.md.


## Demo-data build: call estimate vs reality (2026-11-08)

I estimated **14** Gemini calls for generating the public demo's data and the real build made **5**
(3 clarity + 2 rerank, 0 first-pass scoring). I got the 14 by running the demo through the mock
provider (9 main + 5 ablation, 10 of them first-pass scoring calls), and then told the user the gap
was because "the mock retries and batches differently from Gemini". That explanation was wrong.
The real reason: the same fictional screenplays had already been scored by real Gemini in three
earlier "rehearsal" runs (25 live calls, 14:46-14:53), so all 10 scoring calls were answered from the
response cache; only the clarity prompt (v3) and the rerank inputs were new. The mock estimator has no
view of the real response cache, so it reports a cold-start count, not what a warm re-run will cost.

Rule from now on: any calls/tokens/cost figure in the documents must come from a real run's
`run_metadata.json` (and say whether the run was cold or cache-assisted); anything computed from batch
sizes or the mock must be labelled an estimate. INTERVIEW.md had the ~70-calls figure labelled
"measured"; it is an estimate and now says so. Its "1.5-3 s per call" latency was also true only for
small runs; the full-pack run measured a 7.9 s mean.
