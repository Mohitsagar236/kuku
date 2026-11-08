"""Generate the public demo's data ONCE with a real model, from the fictional demo screenplays.

    .venv/bin/python scripts/build_demo_data.py [--out DIR]

Reads provider/model/key from .env like a normal run (typically Gemini), scores the fictional
show in src/promo_finder/resources/demo, and writes the run plus rules baseline into
src/promo_finder/resources/demo_outputs. Only fictional content is involved. Raw model replies
(llm_raw) and the CONFIDENTIAL notice are not copied. The old demo_outputs folder is replaced
only after a complete, successful run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from promo_finder.application.models import RunOptions  # noqa: E402
from promo_finder.application.pipeline import run_pipeline  # noqa: E402
from promo_finder.infrastructure.artifacts import write_all  # noqa: E402
from promo_finder.infrastructure.llm import LLMSettings  # noqa: E402
from promo_finder.interfaces.web.demo import DEMO_OUTPUTS, DEMO_SOURCE, DEMO_TITLE  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEMO_OUTPUTS)
    args = ap.parse_args()
    settings = LLMSettings()  # .env: provider, model, key
    if settings.provider in {"mock", "none"}:
        print(f"refusing: LLM_PROVIDER={settings.provider} would produce simulated demo data")
        return 2
    with tempfile.TemporaryDirectory(prefix="promo-demo-") as tmp:
        tmp_out = Path(tmp) / "outputs"
        result = run_pipeline(
            DEMO_SOURCE,
            RunOptions(out_root=tmp_out, ablation=True, stability=False),
            settings,
        )
        result.show.title = DEMO_TITLE
        try:
            write_all(result)
        finally:
            result.llm.client.close()
        if result.partial:
            print("refusing: the run was partial (quota, budget or key problem); nothing written")
            return 3
        staged = Path(tmp) / "ship"
        shutil.copytree(
            tmp_out, staged, ignore=shutil.ignore_patterns("llm_raw", "CONFIDENTIAL.txt")
        )
        for meta in staged.glob(
            "*/*/run_metadata.json"
        ):  # no local absolute paths in a public repo
            data = json.loads(meta.read_text())
            data["source_dir"] = "resources/demo"
            meta.write_text(json.dumps(data, indent=2))
        if args.out.exists():
            shutil.rmtree(args.out)
        shutil.copytree(staged, args.out)
    runs = sorted(args.out.glob("*/*/promos.json"))
    print(
        f"wrote {len(runs)} run(s) under {args.out}; model {result.llm.provider}/{result.llm.model}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
