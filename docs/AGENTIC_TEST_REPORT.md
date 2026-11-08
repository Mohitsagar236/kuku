# Promo Finder test report

Date: 2026-11-08

## Results

- Python: 327 passed, 10 failed; 95% total coverage (210.71 seconds).
- Browser: 11 passed in installed Google Chrome (19.9 seconds).
- Frontend: both unit tests, ESLint and production build passed.
- Python Ruff lint/format, mypy and both import architecture contracts passed.

## Finding: malformed run artifacts are not isolated consistently

**Resolved in the subsequent assignment completion audit.** See
[ASSIGNMENT_COMPLETION.md](ASSIGNMENT_COMPLETION.md) for the current status and checks.
The results below are retained as the original test record.

All ten Python failures are parameter cases of
`tests/frontend/test_server.py::test_malformed_artifact_shape_does_not_disable_other_runs`.

`RunRepository.list_runs` parses JSON without validating its shape. A `promos`
value of null or an integer reaches `len(promos)` and causes `/api/runs` to return
500. Invalid metadata can also reach sorting or be included as a valid run.
This can prevent browsing healthy runs when one saved artifact is malformed.

`RunRepository.load_run` likewise accepts syntactically valid JSON such as null
or an array, returning HTTP 200 rather than the expected corrupt-artifact error.

Relevant code: `src/promo_finder/infrastructure/runs.py:32-48` and `:80`.
Recommended correction: validate artifact structure consistently before listing
or loading a run; skip invalid runs in the list and reject direct loads clearly.

Reproduce:

```sh
.venv/bin/python -m pytest tests/frontend/test_server.py -k malformed_artifact_shape
```

## Browser coverage and environment

Covered run/variant selection, keyboard navigation, verdict persistence, note
save races, cut suggestions/reset persistence, clipboard, CSV/ranking downloads,
quality report, deep links, mobile overflow, absence of external browser requests,
empty state, retry after list failure and recovery after a refused write.

Tests used synthetic runs in temporary output directories. No live model
validation was performed. Existing application output was not edited.

The shell required the existing Node runtime at
`/private/tmp/node-v24.22.1-darwin-arm64/bin`. Local socket tests required execution
outside the sandbox. Port 8877 was occupied, so temporary copies of the browser
suite/config/server used port 18877. The expected Playwright browser was missing;
`PF_BROWSER_EXECUTABLE` selected installed Google Chrome instead. Temporary copies
were removed after testing. Application source was not changed.
