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
