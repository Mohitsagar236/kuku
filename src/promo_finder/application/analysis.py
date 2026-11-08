"""In-app analysis of an uploaded screenplay: validate, run the pipeline, expose progress.

Privacy model (the pack is confidential, the viewer may be public):
* every upload gets an unguessable token; its run lives under ``upload-<token>`` and is never
  listed, so other visitors cannot browse it;
* uploads and results are deleted after ``ttl_s`` (default 1 hour), also after a restart;
* nothing is written to the shared response cache, and API keys exist only in memory for the job
  that was given them: never stored, logged or returned.
"""

from __future__ import annotations

import contextlib
import logging
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from promo_finder.application.models import RunOptions
from promo_finder.application.pipeline import run_pipeline
from promo_finder.domain.errors import IngestionError
from promo_finder.infrastructure.artifacts import write_all
from promo_finder.infrastructure.llm import LLMSettings, ManagedLLM
from promo_finder.infrastructure.runs import PRIVATE_PREFIX, RunRepository

log = logging.getLogger(__name__)

MAX_FILES = 60
MAX_FILE_BYTES = 400_000
MAX_TOTAL_BYTES = 8_000_000
MAX_TITLE = 80
MODEL_CALL_BUDGET = 150  # hard cap on logical calls for one uploaded show
KEY_FIELD = {
    "gemini": "gemini_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "groq": "groq_api_key",
}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class AnalysisError(Exception):
    """A request the user can fix. ``code`` is the HTTP status."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Job:
    token: str
    mode: str
    title: str
    state: str = "queued"  # queued | running | done | error
    stage: str = "Queued"
    run_id: str = ""
    error: str = ""
    created: float = 0.0
    secret: str = field(default="", repr=False)  # the caller's API key, if any


class AnalysisService:
    def __init__(
        self,
        outputs: Path,
        *,
        public: bool = False,
        ttl_s: float = 3600.0,
        max_waiting: int = 2,
        settings_factory: Callable[[], LLMSettings] | None = None,
        llm_factory: Callable[[LLMSettings], ManagedLLM] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.outputs = outputs.resolve()
        self.public, self.ttl_s, self.max_waiting = public, ttl_s, max_waiting
        self._settings_factory = settings_factory or LLMSettings
        self._llm_factory = llm_factory  # tests inject a mock; production builds from settings
        self._clock = clock
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")
        self.repo = RunRepository(self.outputs)

    # ------------------------------------------------------------------ housekeeping
    def _uploads(self) -> Path:
        return self.outputs / ".uploads"

    def purge(self) -> int:
        """Delete expired uploads and results (by folder age, so it survives restarts)."""
        cutoff, n = self._clock() - self.ttl_s, 0
        roots = [*self.outputs.glob(f"{PRIVATE_PREFIX}*"), *self._uploads().glob("*")]
        for p in roots:
            try:
                if p.is_symlink() or not p.is_dir() or p.stat().st_mtime >= cutoff:
                    continue
                shutil.rmtree(p, ignore_errors=True)
                n += 1
            except OSError:
                continue
        with self._lock:
            for token in [t for t, j in self._jobs.items() if j.created < cutoff]:
                self._jobs.pop(token, None)
        return n

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ submit
    @staticmethod
    def _clean_name(raw: str, used: set[str]) -> str:
        base = SAFE_NAME.sub("_", Path(str(raw).replace("\\", "/")).name).strip(" .") or "episode"
        if not base.lower().endswith(".txt"):
            raise AnalysisError(400, f"'{base[:60]}' is not a .txt file")
        name, i = base, 1
        while name.lower() in used:
            i += 1
            name = f"{base[:-4]}-{i}.txt"
        used.add(name.lower())
        return name[:150]

    def _settings(
        self, mode: str, provider: str | None, model: str | None, key: str | None
    ) -> LLMSettings:
        if mode == "rules":
            return LLMSettings(_env_file=None, provider="none", model="rules-only")
        if key:
            prov = (provider or "gemini").lower()
            if prov not in KEY_FIELD:
                raise AnalysisError(400, f"provider must be one of {sorted(KEY_FIELD)}")
            from promo_finder.infrastructure.llm.settings import DEFAULT_MODELS

            base = LLMSettings(
                _env_file=None, provider=prov, model=(model or DEFAULT_MODELS[prov]),
                budget_calls=MODEL_CALL_BUDGET, rpd=0,
            )  # fmt: skip
            return base.model_copy(update={KEY_FIELD[prov]: key})
        if self.public:
            raise AnalysisError(
                400, "Paste your own API key to use a model here, or choose Rules only (no key)."
            )
        settings = self._settings_factory()  # local viewer: the key already in .env
        if settings.provider in {"none", "mock"} and self._llm_factory is None:
            raise AnalysisError(
                400,
                "No model is configured (set LLM_PROVIDER and a key in .env), or use Rules only.",
            )
        if settings.budget_calls is None:
            settings = settings.model_copy(update={"budget_calls": MODEL_CALL_BUDGET})
        return settings

    def submit(
        self,
        files: list[tuple[str, str]],
        *,
        mode: str = "rules",
        title: str = "",
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> str:
        self.purge()
        if mode not in {"rules", "model"}:
            raise AnalysisError(400, "mode must be 'rules' or 'model'")
        if not files:
            raise AnalysisError(400, "Add at least one episode .txt file.")
        if len(files) > MAX_FILES:
            raise AnalysisError(413, f"Too many files ({len(files)}); the limit is {MAX_FILES}.")
        used: set[str] = set()
        cleaned: list[tuple[str, str]] = []
        total = 0
        for raw_name, text in files:
            name = self._clean_name(raw_name, used)
            size = len(text.encode("utf-8", "ignore"))
            if "\x00" in text or not text.strip():
                raise AnalysisError(400, f"'{name}' is empty or not plain text.")
            if size > MAX_FILE_BYTES:
                raise AnalysisError(
                    413, f"'{name}' is too large (limit {MAX_FILE_BYTES // 1000} KB)."
                )
            total += size
            cleaned.append((name, text))
        if total > MAX_TOTAL_BYTES:
            raise AnalysisError(413, f"Upload too large (limit {MAX_TOTAL_BYTES // 1_000_000} MB).")
        settings = self._settings(mode, provider, model, (api_key or "").strip() or None)
        with self._lock:
            busy = sum(j.state in {"queued", "running"} for j in self._jobs.values())
            if busy > self.max_waiting:
                raise AnalysisError(429, "The analyser is busy; try again in a minute.")
            token = secrets.token_hex(16)
            clean_title = " ".join(title.split())[:MAX_TITLE] or "Your screenplay"
            job = Job(
                token, mode, clean_title, created=self._clock(), secret=(api_key or "").strip()
            )
            self._jobs[token] = job
        folder = self._uploads() / token / f"{PRIVATE_PREFIX}{token}"
        folder.mkdir(parents=True)
        for name, text in cleaned:
            (folder / name).write_text(text, encoding="utf-8")
        self._pool.submit(self._run, job, folder, settings)
        return token

    # ------------------------------------------------------------------ run
    def _scrub(self, message: str, job: Job) -> str:
        if job.secret:
            message = message.replace(job.secret, "***")
        return message[:300]

    def _run(self, job: Job, folder: Path, settings: LLMSettings) -> None:
        def progress(message: str) -> None:
            job.stage = self._scrub(message.replace("[run] ", ""), job)

        job.state, job.stage = "running", "Reading the screenplay"
        result = None
        try:
            opts = RunOptions(
                out_root=self.outputs, ablation=False, stability=False, persist_cache=False,
                allow_partial=True, write_baseline=job.mode == "model",
            )  # fmt: skip
            llm = (
                self._llm_factory(settings) if (self._llm_factory and job.mode == "model") else None
            )
            result = run_pipeline(folder, opts, settings, progress=progress, llm=llm)
            result.show.title = job.title
            out = write_all(result)
            job.run_id = out.relative_to(self.outputs).as_posix()
            job.state, job.stage = "done", "Done"
        except IngestionError as e:
            job.state, job.error = "error", self._scrub(str(e), job)
        except Exception as e:  # never leak provider details or the key to the browser
            log.error("analysis_failed", extra={"exception_type": type(e).__name__})
            job.state = "error"
            job.error = self._scrub(
                f"The analysis failed ({type(e).__name__}). Check the screenplay format and, "
                "for a model run, your API key and quota.",
                job,
            )
        finally:
            job.secret = ""  # the key is not needed (or kept) any longer
            if result is not None:
                with contextlib.suppress(Exception):  # best effort
                    result.llm.client.close()

    # ------------------------------------------------------------------ status
    def status(self, token: str) -> dict[str, Any] | None:
        self.purge()
        with self._lock:
            job = self._jobs.get(token)
        if job is None:
            return None
        out: dict[str, Any] = {"state": job.state, "stage": job.stage, "mode": job.mode}
        if job.state == "error":
            out["error"] = job.error
        if job.state == "done":
            out["run"] = self.repo.describe(job.run_id)
            out["run_id"] = job.run_id
            out["expires_in_s"] = int(max(0, job.created + self.ttl_s - self._clock()))
        return out
