"""promo-finder CLI.

run SHOW_DIR [--episodes 1-3]   the one-command in-process pipeline
smoke SHOW_DIR --episodes 1-3   a few episodes through the real LLM; prints health counters
package [--show SLUG]           zip the latest run for PRIVATE upload
check-confidential SHOW_DIR     fail if tracked repo files contain script text
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ingestion_service import load_show, parse_episode_spec
from promo_common.confidential import build_shingles, scan_repo
from promo_common.config import get_settings
from promo_common.errors import IngestionError
from promo_common.llm import LLMAuthError, LLMSettings
from promo_common.logging import setup_logging

from promo_finder_cli.outputs import write_all
from promo_finder_cli.package import package_outputs
from promo_finder_cli.pipeline import RunInterrupted, RunOptions, run_pipeline
from promo_finder_cli.smoke import smoke_report


def _add_llm_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--provider", help="override LLM_PROVIDER")
    p.add_argument("--model", help="override LLM_MODEL")
    p.add_argument("--out", default="outputs")
    p.add_argument("--episodes", help="e.g. 1-3 or 1,4,7-9 (default: all)")
    p.add_argument("--no-cache", action="store_true", help="in-memory cache only (no resume)")
    p.add_argument("-v", "--verbose", action="store_true")


def _settings(args: argparse.Namespace) -> LLMSettings:
    kw = {}
    if args.provider:
        kw["provider"] = args.provider
    if args.model:
        kw["model"] = args.model
    return LLMSettings(**kw)


def _execute(args: argparse.Namespace, opts: RunOptions, settings: LLMSettings) -> int:
    try:
        run = run_pipeline(
            args.show_dir, opts, settings, progress=lambda m: print(f"[run] {m}", flush=True)
        )
    except (LLMAuthError, IngestionError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except RunInterrupted as e:
        print(
            f"\nSTOPPED: {e}\nscoring progress so far: {e.stats}\n"
            "Validated results are cached; re-run the same command to resume without re-billing, "
            "or pass --allow-partial to finish with rule fallback.",
            file=sys.stderr,
        )
        return 3
    out = write_all(run)
    if opts.mode == "smoke":
        text, ok = smoke_report(run)
        print(text)
        print(f"outputs: {out}  (CONFIDENTIAL: contains script quotes; do not commit)")
        return 0 if ok else 4
    u = run.llm.summary()
    print(
        f"\ndone in {run.duration_s:.0f}s: {u['calls']} LLM calls "
        f"{ {k: v['calls'] for k, v in u['by_phase'].items()} }, "
        f"{u['tokens_in']:,} in / {u['tokens_out']:,} out tokens, cache hits {u['cache_hits']}"
    )
    print(f"outputs: {out}  (CONFIDENTIAL: contains script quotes; do not commit)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="promo-finder")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="find promos in a folder of episode .txt files")
    r.add_argument("show_dir")
    _add_llm_flags(r)
    r.add_argument("--golden", help="golden.yaml (default: ./golden.yaml if present)")
    r.add_argument("--no-ablation", action="store_true")
    r.add_argument("--stability", action="store_true", help="also re-score a sample (extra calls)")
    r.add_argument("--allow-partial", action="store_true",
                   help="on quota/budget stop, finish with rule-score fallback")  # fmt: skip

    s = sub.add_parser("smoke", help="few episodes, real LLM, health counters")
    s.add_argument("show_dir")
    _add_llm_flags(s)

    k = sub.add_parser("package", help="zip the latest run for private upload")
    k.add_argument("--out", default="outputs")
    k.add_argument("--show", help="show slug if several exist")
    k.add_argument("--include-smoke", action="store_true")

    c = sub.add_parser("check-confidential", help="scan files git would commit for script text")
    c.add_argument("show_dir", nargs="?", help="input folder (default: $PF_REAL_SHOW_DIR)")
    c.add_argument("--root", default=".")
    c.add_argument("--words", type=int, default=8, help="shingle length in words")

    args = ap.parse_args(argv)
    setup_logging(
        "INFO" if getattr(args, "verbose", False) else "WARNING", get_settings().log_format
    )

    if args.cmd == "package":
        try:
            dest = package_outputs(Path(args.out), args.show, args.include_smoke)
        except (FileNotFoundError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"{dest}  ({dest.stat().st_size / 1024:.0f} KB)")
        print("CONFIDENTIAL: upload privately only. This archive is git-ignored; never commit it.")
        return 0

    if args.cmd == "check-confidential":
        src = args.show_dir or os.environ.get("PF_REAL_SHOW_DIR")
        if not src:
            print(
                "check-confidential: no input folder (arg or PF_REAL_SHOW_DIR); skipped",
                file=sys.stderr,
            )
            return 0
        show = load_show(src)
        texts = [b.text for e in show.episodes for b in e.beats]
        texts += [e.logline for e in show.episodes] + [e.continuity_notes for e in show.episodes]
        findings = scan_repo(
            Path(args.root).resolve(), build_shingles(texts, args.words), args.words
        )
        if findings:
            print(
                f"FAIL: {len(findings)} passage(s) from the input appear in files git would commit:"
            )
            for f in findings[:50]:
                print(f"  {f.file}:{f.line}  [{f.shingle_hash}] x{f.matches}")
            return 1
        print(f"OK: no {args.words}-word passage from the input appears in any committable file.")
        return 0

    settings = _settings(args)
    try:
        episodes = parse_episode_spec(args.episodes)
    except IngestionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.cmd == "smoke":
        opts = RunOptions(
            out_root=Path(args.out), ablation=False, stability=False, persist_cache=not args.no_cache,
            episodes=episodes, mode="smoke", write_baseline=False,
        )  # fmt: skip
        return _execute(args, opts, settings)

    rules_only = settings.provider.lower() == "none"
    opts = RunOptions(
        out_root=Path(args.out),
        ablation=False if args.no_ablation else None,
        stability=True if args.stability else None,
        allow_partial=args.allow_partial or rules_only,
        golden_path=Path(args.golden) if args.golden else None,
        persist_cache=not args.no_cache,
        episodes=episodes,
        write_baseline=episodes is None,  # a subset run must not overwrite the full baseline
    )
    return _execute(args, opts, settings)


if __name__ == "__main__":
    raise SystemExit(main())
