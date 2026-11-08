"""ASGI composition root. Each app owns its repository and editor state."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from promo_finder import __version__
from promo_finder.application.analysis import AnalysisError, AnalysisService
from promo_finder.application.editor import EditorError, EditorService
from promo_finder.infrastructure.runs import DOWNLOADS, RunRepository
from promo_finder.interfaces.web.schemas import AnalyzeRequest, NudgeRequest, ReviewRequest
from promo_finder.interfaces.web.security import LocalSecurityMiddleware

log = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).resolve().parents[2] / "resources" / "web"


def create_app(outputs: str | Path = "outputs", static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="Promo Finder",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    demo_mode = os.environ.get("PF_DEMO_MODE", "").lower() == "true"
    if demo_mode:
        from promo_finder.interfaces.web.demo import seed_demo

        # Public demos never enumerate the caller's private outputs directory.
        outputs = seed_demo(Path(os.environ.get("PF_DEMO_ROOT", "/tmp/promo-finder-demo")))
    runs = RunRepository(Path(outputs))
    editor = EditorService(runs)
    analysis_on = os.environ.get("PF_ANALYZE", "true").lower() != "false"
    analysis = AnalysisService(Path(outputs), public=demo_mode) if analysis_on else None
    if analysis is not None:
        analysis.purge()
    static = (static_dir or WEB_ROOT).resolve()
    app.state.runs, app.state.editor, app.state.analysis = runs, editor, analysis
    hosts = frozenset(
        h.strip().lower() for h in os.environ.get("PF_ALLOWED_HOSTS", "").split(",") if h.strip()
    )
    app.add_middleware(
        LocalSecurityMiddleware,
        allowed_hosts=hosts,
        password=os.environ.get("PF_VIEWER_PASSWORD", ""),
        public_demo=demo_mode,
    )

    @app.exception_handler(EditorError)
    async def editor_error(request: Request, exc: EditorError) -> JSONResponse:
        return JSONResponse({"error": exc.message, **exc.extra}, status_code=exc.code)

    @app.exception_handler(AnalysisError)
    async def analysis_error(request: Request, exc: AnalysisError) -> JSONResponse:
        return JSONResponse({"error": exc.message}, status_code=exc.code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's default error response echoes input values, which may be confidential.
        return JSONResponse(
            {
                "error": "invalid request",
                "fields": [".".join(str(p) for p in e["loc"]) for e in exc.errors()],
            },
            status_code=400,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("request_failed", extra={"exception_type": type(exc).__name__})
        return JSONResponse({"error": "server error (see terminal)"}, status_code=500)

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def ready() -> JSONResponse:
        built = (static / "index.html").is_file()
        return JSONResponse(
            {"status": "ready" if built else "frontend build missing"},
            status_code=200 if built else 503,
        )

    @app.get("/api/runs")
    def list_runs(extra: str = "") -> list[dict[str, Any]]:
        return runs.list_runs(extra=extra)

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        from promo_finder.application import analysis as limits

        return {
            "analyze": analysis is not None,
            "public": demo_mode,
            "needs_key_for_model": demo_mode,
            "limits": {"files": limits.MAX_FILES, "file_kb": limits.MAX_FILE_BYTES // 1000,
                       "total_mb": limits.MAX_TOTAL_BYTES // 1_000_000},
            "retention_minutes": int((analysis.ttl_s if analysis else 0) // 60),
        }  # fmt: skip

    @app.post("/api/analyze")
    def analyze(body: AnalyzeRequest) -> JSONResponse:
        if analysis is None:
            raise EditorError(404, "uploads are disabled on this server")
        token = analysis.submit(
            [(f.name, f.text) for f in body.files],
            mode=body.mode, title=body.title, provider=body.provider,
            model=body.model, api_key=body.api_key,
        )  # fmt: skip
        return JSONResponse({"job": token}, status_code=202)

    @app.get("/api/analyze/status")
    def analyze_status(job: str = "") -> dict[str, Any]:
        status = analysis.status(job) if analysis is not None else None
        if status is None:
            raise EditorError(404, "job not found (it may have expired)")
        return status

    @app.get("/api/run")
    def load_run(id: str = "") -> dict[str, Any]:
        try:
            run = runs.load_run(id)
        except (ValueError, OSError) as e:
            raise EditorError(500, "run has a corrupt or unreadable promos file") from e
        if run is None:
            raise EditorError(404, "run not found")
        return run

    @app.get("/api/feedback")
    def feedback(id: str = "") -> dict[str, Any]:
        return editor.feedback_state(id)

    @app.post("/api/review")
    def review(body: ReviewRequest, id: str = Query(default="")) -> dict[str, Any]:
        return editor.post_review(id, body.model_dump(exclude_unset=True))

    @app.post("/api/nudge")
    def nudge(body: NudgeRequest, id: str = Query(default="")) -> dict[str, Any]:
        return editor.post_nudge(id, body.model_dump(exclude_unset=True))

    @app.get("/api/file")
    def download(id: str = "", name: str = "") -> FileResponse:
        d = runs.run_dir(id)
        if d is None or name not in DOWNLOADS:
            raise EditorError(404, "file not found")
        file = d / name
        if not file.is_file() or file.is_symlink() or file.resolve().parent != d:
            raise EditorError(404, "file not found")
        return FileResponse(file, media_type=DOWNLOADS[name], filename=f"{d.name}-{name}")

    @app.post("/{path:path}", include_in_schema=False)
    def unknown_write(path: str) -> None:
        raise EditorError(404, "not found")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def assets(path: str) -> FileResponse:
        if not path:
            path = "index.html"
        file = (static / path).resolve()
        if (
            static not in file.parents
            or not file.is_file()
            or Path(path).suffix not in {".html", ".js", ".css", ".svg", ".ico", ".woff2"}
        ):
            raise EditorError(404, "not found")
        return FileResponse(file)

    return app
