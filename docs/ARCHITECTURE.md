# Architecture and operating model

Promo Finder is a modular monolith: one installable Python application with a separate React build. Analysis runs are batch CLI jobs; HTTP serves completed artifacts and editor actions. A screenplay is processed locally until an explicitly selected provider is called. No web request starts an expensive analysis job.

## Boundaries

```
src/promo_finder/
  domain/             Timeline, candidates, scores, promos, cuts and validation
  ingestion/          Screenplay parsing and speaker normalization
  candidates/         Windows, stitching, recap removal and heuristic signals
  scoring/            Batched judgments, ID validation, blind clarity and refinement
  ranking/            Fusion, hard gates, diversity and final cut construction
  evaluation/         Golden matching, feedback metrics, stability and reporting
  application/        Pipeline orchestration, contracts, variants and editor use cases
  infrastructure/     Provider HTTP clients, cache, budgets, files and run repository
  interfaces/cli/     Argument parsing, diagnostics and terminal presentation
  interfaces/web/     FastAPI composition root, request schemas and security middleware
  resources/          Versioned config, prompts and built viewer assets
frontend/src/
  components/         Editor and presentation components
  App.js              Run selection and asynchronous view state
  api.js              HTTP client
  format.js           Timestamp/score utilities
  navigation.js       Shareable view state
```

Domain code does not import infrastructure, application or delivery code. Processing features never import the CLI or HTTP layer. Import Linter enforces these rules. Pydantic validates data at input boundaries; deterministic Python owns cut eligibility and source quotes. The LLM chooses only valid IDs, never authors the delivered screenplay text. Feature settings load versioned resources; orchestration makes the cross-feature decisions.

The CLI and API share application contracts. FastAPI owns HTTP serialization and schema validation. Each `create_app` owns its repository and editor service; tests create isolated applications. Existing artifact schemas and query-based API URLs remain compatible. Public Python imports changed: `promo_common`, `*_service` and `promo_finder_cli` are replaced by `promo_finder.*`. Use `python -m promo_finder` or the installed entry points.

## Persistence and concurrency

Artifacts remain files because they are the assignment's deliverables and are useful for inspection, archival and reruns. Adding a database for immutable JSON/CSV exports would duplicate the source of truth. Every output file is replaced atomically. `promos.json` is published last, so discovery ignores new incomplete runs. Readers can continue using older outputs without a migration. The mutable rules baseline is a convenience snapshot; concurrent replacements are per-file atomic, not a cross-file transaction. Use timestamped runs for reproducible comparisons.

Editor reviews remain compatible YAML, with atomic replacement and a process lock on the show directory around read-modify-write. Locks require a local POSIX filesystem (macOS/Linux). Source hashes are checked again before enabling cut changes; an earlier read cannot cache away subsequent source edits. Invalid review files are preserved and surfaced as conflicts.

Response caching retains the original content keys and prompt hashes, so moving files does not discard paid judgments. Budget admission reserves logical calls atomically before parallel work; transport retries belong to that logical call. USD limits stop admission after observed spend reaches the cap; they are not a prepaid reservation and in-flight calls can add spend. Provider pricing can be unknown and is flagged. Daily quota counters and RPM limiting belong to one analysis process; run one analysis process per provider key. Separate simultaneous batch jobs do not share an RPM window. A shared scheduler is required before supporting distributed workers.

## Security and supported deployment

The supported product is a confidential local editor tool. Uvicorn binds loopback; the ASGI middleware additionally restricts Host and checks the exact Origin on writes, requires JSON, bounds streamed bodies, adds request IDs and hardening headers, and avoids logging script text or query strings. Requests cannot choose arbitrary filesystem paths. Symlinked artifacts, exports, metadata and feedback files are rejected or hidden. React is bundled locally; the browser makes no CDN requests. React escapes source text, including the quality-report renderer.

The API exposes `/healthz`, `/readyz` and `/api/openapi.json`. Readiness checks the viewer build. It does not require outputs to exist because an empty installation is valid. Uvicorn runs one local process by default; editor locks also protect cooperating processes. Runtime dependencies and frontend dependencies have committed lock files.

Private remote hosting is opt-in with an explicit allowed hostname and HTTP Basic password. `PF_DEMO_MODE=true` is the only password-free remote mode: it replaces the outputs repository with an isolated temporary root and installs bundled fictional demo data (real model scores generated offline; a mock fallback only if that data is absent). It never uses confidential local outputs. Visitor uploads run in private, unlisted, expiring runs; their AI runs use the server's own key from its environment (never sent to the browser) and are capped per day, while Rules only needs no key. Shared reviews on the sample are ephemeral.

There is no multi-tenant isolation, upload endpoint, analysis queue or HA storage contract. A shared production editor would need tenant authorization and durable storage in addition to the controls above. The public demo is an evaluation workspace, not a hosted confidential screenplay service.

## Verification and delivery

`make check` runs lint/format, whole-package typing, dependency-boundary checks, Python regressions, frontend lint/tests and production build. Provider tests use mock transports and local HTTP stubs, never real credentials. `make browser-test` runs the real browser suite against isolated synthetic runs; CI installs Chromium and runs it as well. CI also builds a wheel and installs it outside the checkout to verify packaged configuration, prompts and static assets.

`make wheel` builds the browser first and includes its assets in the wheel. `Dockerfile` builds React in a Node stage, installs Python with locked constraints, runs as a non-root user and checks readiness. The Docker image recipe is provided; run it on a Docker host to validate image building and mounted-volume permissions. CI does not currently build the image.

## Migration decisions

The previous source tree is replaced, including uncommitted editor-feedback functionality. The existing parser, candidate rules, prompts, scoring semantics, ranking and evaluation algorithms were retained and rehomed under enforceable boundaries. They are tested domain behavior, so this migration does not replace them with new unmeasured heuristics. The HTTP transport and frontend delivery were rewritten. Unused database/Redis settings were removed. There is no reason to add microservices, a vector database or an orchestration framework to this workload.


## In-app analysis (uploads)

`application/analysis.py` runs the normal pipeline on files uploaded through `POST /api/analyze` (JSON text, 8 MB cap on that path only). One worker thread, a short queue and hard size limits keep it bounded. Privacy: each job has a 128-bit token; its run lives under `upload-<token>`, is excluded from `/api/runs` (only the owner's own link adds it), and uploads/results are deleted after `ttl_s` by folder age, so cleanup survives restarts. The response cache is never used (`persist_cache=False`). Model runs use the server's own key from its environment: the browser never sends or receives a key (the request schema forbids one), the key is scrubbed from error text, and capabilities report only the model name. A public server caps AI analyses per day (`PF_MODEL_JOBS_PER_DAY`, default 3) so visitors cannot exhaust the owner's free quota; Rules only needs no key and is not capped.
