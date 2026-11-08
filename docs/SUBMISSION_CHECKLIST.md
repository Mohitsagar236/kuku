# Final submission checklist (status as of 8 Nov 2026, evening)

Nothing below is marked done unless it was verified. The placement form is submitted by you.

| Item | Status | What to do |
|---|---|---|
| Solution PDF (<= 2 pages) | READY locally: `docs/SOLUTION_submission.pdf` (private, quotes) and `docs/SOLUTION.pdf` (public). Both 2 pages | Upload the one you want reviewers to see. Open it after upload |
| Prototype link | NOT VERIFIED. Public repo opens, but it does not yet contain today's changes | Commit and push (see plan below), then open the repo URL in a private window |
| Raw outputs | READY locally: `outputs/packages/kuku-problem1-submission-20261108.zip` (199 files, SHA-256 list inside) | Upload to Drive/Dropbox with "anyone with the link", open it in a private window |
| Walkthrough video | NOT RECORDED. Script: `docs/WALKTHROUGH_SCRIPT.md` | Optional. Record, upload unlisted, open the link signed out |
| Human review | PENDING. Sheet: `outputs/<show>/review_sheet_20261108-184323.csv` (blank) | Rate ~20 promos, then `scripts/review_sheet.py compare` |
| Form fields | You must fill | Name, roll number, branch, problem chosen (Problem 1), links above |

## Commit plan (nothing is committed or pushed yet)
Propose to commit: `src/` changes (clarity_v3 prompt, clarity gate, start snap, cache namespacing,
rules-only fix), `tests/` additions, `docs/SOLUTION.html`, `docs/SOLUTION.pdf`, the three doc
updates, `docs/WALKTHROUGH_SCRIPT.md`, this file, `scripts/review_sheet.py`, `scripts/prepare_submission.py`.
Must stay private (git-ignored): `.env`, `.cache/`, `outputs/`, `docs/SOLUTION_submission.pdf`, `dist/`,
review sheets, the Kuku pack.

## How to check links
Open each link in a private/incognito window while signed out. If it asks you to sign in or
request access, it is not public enough.
