# Walkthrough script (about 2 min 45 s, ~430 words)

Record this yourself, in your own voice. Show the real pack only in a private recording;
use the synthetic demo for anything you upload publicly. Hide `.env`.

**0:00 - Problem (20 s)**
"Kuku's editors watch every episode to pick 20 to 60 second promos. I built Promo Finder
to give them a ranked shortlist from the screenplay. It proposes. The editor decides."

**0:20 - Input (15 s)** *(terminal)*
"The input is a folder of timestamped screenplay files. One command reads them:
`make run SHOW_DIR=...`. For a free run with no model there is `make run-rules`."

**0:35 - How it finds promos (35 s)** *(show the final lines of the run output)*
"Code parses 50 episodes, 3,727 beats, and builds every 20 to 60 second window that starts
and ends on a beat. A model scores hook, cliffhanger, intensity and spoiler risk. It answers
with beat IDs, never free-text timestamps, so it cannot invent a cut. The top 60 get a blind
check: does this make sense to someone who has never seen the show? Code validates every cut."

**1:10 - Ranking and one example (40 s)** *(open `make ui`, promo 1)*
"The 30 promos are ranked by a blend of rule and model scores plus a listwise rerank. Number
one is Episode 32, 01:59.50 to 02:34.38, 35 seconds. The viewer shows the hook line, the
cliffhanger line, the exact times and why it was ranked first. Editors can approve, reject,
add a note, or nudge the cut, and export CSV."

**1:50 - A failure (30 s)** *(quality report, section on failures)*
"It is not perfect. Rank 23 opens on a generic line and ends on a reaction shot; a stronger
beat comes a few seconds later. No human has labelled these results, so every judgement is
the model's own. 11 of 30 end at an episode's end, which I have not checked with an editor.
No joined promo reached the top 30."

**2:20 - Rerun and swap (25 s)** *(show `.env` provider lines with the key hidden)*
"To rerun on a new show, point `SHOW_DIR` at the new folder. To change the model, change
`LLM_PROVIDER` and `LLM_MODEL`. Gemini is tested live; the other providers are tested only
against mocked replies. Thank you."

## Before recording
- `make ui` running; pick the run `20261108-184323`.
- Do not open any file that shows the API key.
- Keep under 3:00. Upload as unlisted and set "anyone with the link".
