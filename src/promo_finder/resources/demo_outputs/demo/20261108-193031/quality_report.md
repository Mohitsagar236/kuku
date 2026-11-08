# Quality report: The Quarterly Close - Sample Story

## 1. Run summary

- Episodes: 6; beats parsed: 93
- Candidate windows examined: 342; sent to scoring: 19 (0 stitched)
- Cascade: hook/cliffhanger/intensity scored on all 19; blind clarity + stitched coherence on the top 9 after first-pass scoring (the rest cannot be delivered)
- Promos delivered: 6 (top 6 shown in ranking.md)
- LLM: gemini / gemini-3.5-flash-lite, 5 calls, 7,100 in / 407 out tokens
- Complete run: yes

## 2. Validation and reliability

- Final validation failures: 0 of 6 selected (0.0%)
- LLM answers rejected after retries (candidate-level): score 0, clarity 0, coherence 0; served from cache: score 19, clarity 0
- Candidates that fell back to rule scores: 0 (hook/cliff/intensity), 0 (clarity)
- LLM trim suggestions applied: 0, rejected (would break a rule): 0
- Dropped by selection: {'scene_cap': 2, 'arc_cap': 1}

## 2b. Hard gates and end-point refinement

- Gates on the LLM's own scores (never delivered below): hook < 5, cliffhanger < 5 (cliffhanger = the end that is delivered), blind clarity < 5. Rule-only fallback candidates are not gated.
- Removed before blind scoring (hook gate, first pass): 7 of 19 candidates
- Removed after blind scoring + end refinement, of 9 fully scored: hook only 0, cliffhanger only 0, clarity only 0, several 0 (total 0)
- Start snap: 0 segment start(s) moved back (<= 0.6 s) onto the camera shot that introduces the first line, so clips do not open mid-shot
- End refinement: 2 delivered promo(s) had their end moved to a stronger unresolved cut; 0 proposals were rejected by validation
  - #1 ep5: end 00:40.70 -> 00:38.00, duration 24.0s -> 21.3s
  - #6 ep1: end 01:02.90 -> 01:08.40, duration 25.5s -> 31.0s

## 2c. Final-score formula and rule vs LLM share

```
dim   = llm_share * LLM + (1 - llm_share) * rule    (hook/cliffhanger/intensity: 0.7; clarity: 0.85)
score = 0.275*hook + 0.275*cliffhanger + 0.25*clarity + 0.2*intensity - 0.05*spoiler_risk
stitched: score *= 0.7 + 0.03*coherence
shortlist (top 30): final = 0.5*score + 0.5*listwise-rerank score (LLM)
```
- By construction the LLM contributes 74% of the absolute score (rule 26%), plus 100% of the spoiler penalty and of the rerank blend.
- Measured on this run's selected promos (before rerank): rule 15% / LLM 85%
- After the rerank blend, the rule signals' share of the final score is about 13%.

## 3. Score distributions

min / q1 / median / q3 / max

| dimension | delivered promos | all scored candidates |
|---|---|---|
| hook | 4.8 / 4.9 / 5.1 / 5.4 / 5.9 | 4.6 / 4.9 / 5.1 / 5.6 / 6.5 |
| cliffhanger | 6.3 / 6.3 / 6.3 / 6.4 / 6.4 | 5.6 / 6.3 / 6.3 / 6.4 / 6.4 |
| clarity | 7.4 / 7.4 / 7.5 / 7.6 / 7.7 | 7.4 / 7.4 / 7.5 / 7.6 / 8.0 |
| intensity | 4.2 / 4.6 / 4.9 / 5.4 / 6.2 | 3.5 / 3.9 / 4.7 / 5.0 / 6.2 |
| final score | 5.5 / 6.1 / 6.5 / 6.8 / 7.0 | 5.6 / 5.7 / 5.7 / 5.9 / 6.2 |

## 4. LLM vs rule disagreement

- hook: mean |LLM-rule| = 1.61, share differing by >=4 points: 5%
- cliffhanger: mean |LLM-rule| = 4.10, share differing by >=4 points: 47%
- intensity: mean |LLM-rule| = 4.83, share differing by >=4 points: 79%
- Spearman correlation, rule score vs mean LLM score: 0.36
- Biggest disagreements (candidate, LLM hook/cliff vs rule hook/cliff):
  - df41da23373c ep4 0.2s: LLM 8/5 vs rule 3.0/0.0
  - 3c5ba036440b ep2 36.3s: LLM 5/8 vs rule 7.9/2.5
  - 94774ade111e ep2 3.2s: LLM 2/6 vs rule 3.8/0.0

## 5. Calibration (cross-show listwise rerank)

- Rerank applied to the top 6. Top-6 before vs after rerank: 6/6 unchanged (0 replaced).

## 5b. Rules baseline vs rules + LLM

- Top-6 overlap, LLM run vs rules-only baseline: **3/6** (IoU >= 0.5 = same promo)
- Top-6 ending at an episode's end: LLM run 3/6, rules baseline 2/6

**Promoted by the LLM** (in its top 6, not in the rules top 6):
- LLM #1 (ep5 00:16.70-00:38.00); rules rank >6: LLM blind clarity 8 vs rule 4.0; Opens with a tense confession about a hidden ledger and cuts off right as they resolve to break into the house.
- LLM #2 (ep3 00:17.90-00:47.70); rules rank >6: LLM cliffhanger 8 vs rule 2.5; The confrontation escalates quickly with physical tension and a sharp threat, leaving the outcome completely unresolved.
- LLM #5 (ep6 00:14.90-00:44.00); rules rank >6: LLM cliffhanger 8 vs rule 2.5; The sudden light reveal works well as a mid-tier hook and the unexpected chairman arrival creates a sharp cliffhanger.

**Demoted by the LLM** (in the rules top 6, not in its top 6):
- rules #3 (ep6 00:00.20-00:26.90): cut after first-pass scoring; LLM hook 2 vs rule 4.8
- rules #4 (ep2 00:18.90-00:48.40): outside the delivered list (score, dedupe or caps); LLM hook 5 vs rule 5.4
- rules #6 (ep1 00:07.70-00:49.50): cut after first-pass scoring; LLM hook 5 vs rule 4.2

## 6. Ablations: how much does the tool lean on fragile signals?

Main run: 3/6 of the top 6 end at an episode's end (3/6 of all delivered). No cap is applied to this share; it is only reported.

### literal_cliffhanger: literal annotations ['cliffhanger'] removed from rules and prompts
- Top-6 overlap with/without: **6/6** (IoU >= 0.5 = same promo); top-6: 6/6
- Top-6 ending at an episode end: **3/6 with vs 3/6 without**; all delivered: 3/6 vs 3/6
- Verdict: not applicable: signal absent in this show (0 annotations to remove).

### episode_end_prior: episode-end prior switched off (weight 0)
- Top-5 overlap with/without: **4/5** (IoU >= 0.5 = same promo); top-5: 5/5
- Top-5 ending at an episode end: **3/5 with vs 2/5 without**; all delivered: 3/6 vs 2/5
- Reading: if the share drops a lot without the prior, the prior is steering picks; if overlap stays high, the text signals agree with it.

## 7. Stability (temperature 0, re-scored a second time)

- Stability not run.

## 8. Golden set

- No golden.yaml provided. Copy golden.template.yaml to golden.yaml, label ~10 promos, re-run. (Or label in the viewer: see 8b.)

## 8b. Editor feedback

- No editor feedback yet. In `make ui`, press Approve / Would post / Reject on promos; verdicts are saved to `editor_feedback.yaml`, then refresh this report with `make feedback RUN=<run folder>` (or just re-run: it is cached).

## 9. Where the tool picks badly

Detected automatically on the delivered promos; examples are real rows from this run.

**Episode-ending bias**: 3 of 6. 50% of delivered promos end exactly at an episode's end. Real cliffhangers, but every episode has one, so this signal alone cannot rank them.
  - #2 (ep3 00:17.90-00:47.70), hook: "Marguerite, you look radiant. Is that the new acquisition suit?"
  - #3 (ep4 00:19.00-00:39.10), hook: "Let her speak."
  - #4 (ep2 00:36.30-01:00.30), hook: "Then who has your keycard?"

**Needs backstory (low standalone clarity)**: 0 of 6. A stranger would miss who the characters are or what came before.

**Spoils a major beat**: 0 of 6. Reveals an outcome that a promo should tease, not show.

**Stitched clip may confuse newcomers**: 0 of 6. 0 stitched promos delivered; flagged lower confidence.

**LLM and rules disagree about the hook**: 0 of 6. One of the two is wrong; check these first.

**Dead air inside the clip**: 0 of 6. Long stretch with no dialogue or action.

**Coverage**: the top 6 come from 6 distinct episodes (1, 2, 3, 4, 5, 6); promos overall from 6 of 6 episodes.

**Structural limits (not measurable from the script alone)**
- Script-only: no picture or audio, so slaps and music stings are inferred from text.
- Timestamps are the script's; the produced video may differ slightly, so editors should verify cuts.
- LLM judges favour melodrama; a quiet but effective hook scores low.
- Hook/cliffhanger text is judged by one model; use the golden set to check it against your taste.

## 10. Tokens and cost

- Scoring output tokens per candidate: not measurable in this run (every main-ranking answer came from the response cache); the calls below are from phases scored fresh.
- clarity: 3 calls, 5,832 in / 365 out
- rerank: 2 calls, 1,268 in / 42 out
- Estimated cost: $0.0000 (model price unknown: shown as 0, check your provider)
