"""Ephemeral browser-test server. Never reads .env or modifies real output folders."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from promo_finder.application.models import RunOptions  # noqa: E402
from promo_finder.application.pipeline import run_pipeline  # noqa: E402
from promo_finder.infrastructure.artifacts import write_all  # noqa: E402
from promo_finder.infrastructure.llm import LLMSettings  # noqa: E402
from promo_finder.interfaces.web.app import create_app  # noqa: E402
from tests.e2e.helpers import write_show  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="promo-browser-") as directory:
        root = Path(directory)
        show = write_show(root, n_eps=6)
        outputs = root / "outputs"
        for model in ("browser-primary", "browser-secondary"):
            settings = LLMSettings(
                _env_file=None,
                provider="mock",
                model=model,
                rpm=0,
                rpd=0,
            )
            result = run_pipeline(
                show,
                RunOptions(out_root=outputs, persist_cache=False, ablation=True, stability=True),
                settings,
            )
            try:
                write_all(result)
            finally:
                result.llm.client.close()
        uvicorn.run(
            create_app(outputs),
            host="127.0.0.1",
            port=int(os.environ.get("PF_TEST_PORT", "8877")),
            access_log=False,
        )


if __name__ == "__main__":
    main()
