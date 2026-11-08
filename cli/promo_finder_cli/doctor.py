"""``promo-finder doctor [SHOW_DIR]``: preflight before a real run. Never prints a secret.

Checks, each printed as PASS / WARN / FAIL / SKIP:
  1. .env present                      4. one JSON-mode test call (costs 1 request)
  2. provider settings and key         5. remaining daily quota vs the calls this show needs
  3. provider reachable, model exists  6. input folder parses; candidates and call estimate
Exit code 1 if anything FAILs.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from promo_common.config import find_config_dir
from promo_common.errors import IngestionError
from promo_common.llm import LLMRequest, LLMSettings
from promo_common.llm.errors import LLMError
from promo_common.llm.factory import build_client, build_llm
from promo_common.llm.json_utils import extract_json

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
TIMEOUT_S = 15.0


class Report:
    def __init__(self, out: Callable[[str], None] = print) -> None:
        self.lines: list[tuple[str, str]] = []
        self.out = out

    def add(self, status: str, msg: str) -> None:
        self.lines.append((status, msg))
        self.out(f"{status}  {msg}")

    @property
    def failed(self) -> bool:
        return any(s == FAIL for s, _ in self.lines)


def _host(url: str) -> str:
    """Scheme://host[:port] only: never echo paths or query strings (they can carry tokens)."""
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}" if u.netloc else "(no host)"


def _secret(value: str | None) -> str:
    return f"set ({len(value)} chars)" if value else "not set"


def _key_for(s: LLMSettings, provider: str) -> tuple[str, str | None]:
    return {
        "gemini": ("GEMINI_API_KEY", s.gemini_api_key),
        "groq": ("GROQ_API_KEY", s.groq_api_key),
        "anthropic": ("ANTHROPIC_API_KEY", s.anthropic_api_key),
        "openai": ("OPENAI_API_KEY", s.openai_api_key),
        "openai_compatible": ("LLM_API_KEY", s.api_key),
    }.get(provider, ("", None))


def check_model(client: Any) -> tuple[str, str]:
    """Is the provider reachable and does it know this model? (status, message)."""
    p, model, base = client.provider, client.model, getattr(client, "base_url", "")
    key = getattr(client, "api_key", None)
    try:
        with httpx.Client(timeout=TIMEOUT_S) as http:
            if p == "gemini":
                r = http.get(f"{base}/models/{model}", headers={"x-goog-api-key": key or ""})
            elif p == "anthropic":
                r = http.get(
                    f"{base}/models/{model}",
                    headers={"x-api-key": key or "", "anthropic-version": "2023-06-01"},
                )
            elif p == "ollama":
                r = http.get(f"{base}/api/tags")
            else:  # openai, groq, openai_compatible
                h = dict(getattr(client, "extra_headers", {}) or {})
                if key:
                    h["Authorization"] = f"Bearer {key}"
                r = http.get(f"{base}/models", headers=h)
    except httpx.HTTPError as e:
        return FAIL, f"cannot reach {_host(base)} ({type(e).__name__})"
    where = _host(base)
    if r.status_code in (401, 403):
        return FAIL, f"{where} rejected the API key (HTTP {r.status_code})"
    if p in ("gemini", "anthropic"):
        if r.status_code == 200:
            return PASS, f"{where} reachable; model {model!r} exists"
        if r.status_code == 404:
            return FAIL, f"{where} reachable, but model {model!r} was not found"
        return WARN, f"{where} answered HTTP {r.status_code} to the model lookup"
    if r.status_code in (404, 405, 501):
        return WARN, f"{where} reachable; it has no model list, so the test call checks the model"
    if r.status_code != 200:
        return WARN, f"{where} answered HTTP {r.status_code} to the model list"
    try:
        data = r.json()
    except ValueError:
        return WARN, f"{where} reachable; model list was not JSON"
    if p == "ollama":
        names = [m.get("name", "") for m in data.get("models", [])]
        found = model in names or f"{model}:latest" in names
    else:
        names = [m.get("id", "") for m in data.get("data", [])]
        found = model in names
    if found:
        return PASS, f"{where} reachable; model {model!r} is listed ({len(names)} models)"
    hint = f"; e.g. {', '.join(names[:3])}" if names else ""
    return FAIL, f"{where} reachable, but model {model!r} is not in its list{hint}"


def estimate_calls(n_by_episode: dict[int, int], n_stitched: int) -> tuple[int, int]:
    """(likely, upper bound) LLM calls for the MAIN ranking, mirroring the scorer's batching."""
    import yaml

    cfg = yaml.safe_load((find_config_dir() / "pipeline.yaml").read_text())["scoring"]
    score = sum(math.ceil(n / cfg["max_batch"]) for n in n_by_episode.values())
    score += math.ceil(n_stitched / cfg["stitched_batch"])
    total = sum(n_by_episode.values()) + n_stitched
    top = min(cfg["cascade_top"], total)
    clarity = math.ceil(top / cfg["clarity_batch"])
    coherence_max = math.ceil(min(n_stitched, top) / cfg["stitched_batch"])
    rerank = cfg["rerank_passes"] if total else 0
    likely = score + clarity + rerank
    return likely, likely + coherence_max


def run_doctor(
    show_dir: str | None,
    settings: LLMSettings,
    env_path: Path = Path(".env"),
    call: bool = True,
    out: Callable[[str], None] = print,
) -> int:
    rep = Report(out)
    logging.getLogger().setLevel(logging.ERROR)  # parser warnings are summarised below instead
    out("promo-finder doctor (no secrets are printed)\n")

    # 1. .env
    if env_path.is_file():
        n = sum(
            1
            for line in env_path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        )
        rep.add(PASS, f".env found ({n} settings)")
    else:
        rep.add(
            WARN, ".env not found: using environment variables and defaults (cp .env.example .env)"
        )

    # 2. provider settings
    provider = settings.provider.lower()
    client = None
    var, key = _key_for(settings, provider)
    try:
        client = build_client(settings)
    except (LLMError, ValueError) as e:
        rep.add(FAIL, f"provider settings: {e}")
    if client is not None:
        extra = f"; {var} {_secret(key)}" if var else ""
        if provider == "openai_compatible":
            extra += f"; LLM_BASE_URL host {_host(settings.base_url)}"
            if settings.extra_headers.strip():
                hdrs = json.loads(settings.extra_headers)
                extra += f"; extra headers: {', '.join(sorted(hdrs))} (values hidden)"
        rep.add(PASS, f"provider={client.provider} model={client.model}{extra}")

    live = client is not None and provider not in ("mock", "none")
    needed: int | None = None

    # 6 first (it is local and tells us how many calls the run needs)
    if show_dir:
        from candidate_service import generate_candidates, load_config, load_lexicon
        from ingestion_service import load_show

        try:
            t0 = time.perf_counter()
            show = load_show(show_dir)
            beats = [b for e in show.episodes for b in e.beats]
            dialogue = sum(b.type.value == "dialogue" for b in beats)
            ep_warn = Counter(w.code for e in show.episodes for w in e.warnings)
            ep_warn.update(w.code for w in show.warnings)
            warn_txt = ", ".join(f"{k} x{v}" for k, v in ep_warn.most_common(5)) or "none"
            rep.add(
                PASS,
                f"input parses: {len(show.episodes)} episodes, {len(beats)} beats "
                f"({100 * dialogue / max(1, len(beats)):.0f}% dialogue); warnings: {warn_txt}",
            )
            if beats and dialogue / len(beats) < 0.05:
                rep.add(WARN, "under 5% of beats are dialogue: the file format may have drifted")
            cands, _ = generate_candidates(show, load_config(), load_lexicon())
            by_ep: Counter[int] = Counter(
                c.segments[0].episode_number for c in cands if c.kind == "single"
            )
            stitched = sum(c.kind != "single" for c in cands)
            likely, upper = estimate_calls(dict(by_ep), stitched)
            needed = upper
            rep.add(
                PASS if cands else FAIL,
                f"candidates: {len(cands)} ({stitched} stitched) in "
                f"{time.perf_counter() - t0:.1f}s; main ranking needs ~{likely} LLM calls "
                f"(at most {upper}); ablations add calls only for candidates they change",
            )
        except IngestionError as e:
            rep.add(FAIL, f"input folder: {e}")
    else:
        rep.add(SKIP, "input folder: none given (make doctor SHOW_DIR=path/to/show)")

    # 3. reachable + model exists
    if live and client is not None:
        rep.add(*check_model(client))
    else:
        rep.add(SKIP, f"network checks: provider {provider!r} needs no network")

    # 4. JSON-mode test call (through the managed client so quota accounting stays honest)
    if live and call:
        llm = build_llm(settings)
        req = LLMRequest(
            system="You are a health check. Reply with JSON only.",
            user='Return exactly this JSON object: {"ok": true, "n": 3}',
            task="doctor",
            max_tokens=200,
        )
        try:
            resp = llm.complete(req)
            obj = extract_json(resp.text)
            good = isinstance(obj, dict) and obj.get("ok") is True
            rep.add(
                PASS if good else FAIL,
                f"JSON-mode test call: {'valid JSON' if good else 'reply was not the JSON asked for'}"
                f" in {resp.latency_ms:.0f} ms ({resp.tokens_in} in / {resp.tokens_out} out tokens)"
                + ("; reply truncated" if resp.truncated else ""),
            )
        except (LLMError, ValueError) as e:
            rep.add(FAIL, f"JSON-mode test call failed: {type(e).__name__}: {str(e)[:160]}")
    elif live:
        rep.add(SKIP, "JSON-mode test call: skipped (--no-call)")

    # 5. daily quota (the client's own count; the provider may know better)
    if live:
        lim = build_llm(settings).limiter
        if lim.rpd:
            left = max(0, lim.rpd - lim.used_today)
            msg = f"daily quota (client count): {lim.used_today} of {lim.rpd} used, {left} left"
            if needed is not None and left < needed:
                rep.add(WARN, f"{msg}: fewer than this show may need ({needed}); "
                              "the run stops cleanly and resumes from cache tomorrow")  # fmt: skip
            else:
                rep.add(PASS, msg)
        else:
            rep.add(PASS, "daily quota: no client-side limit set (LLM_RPD=0)")

    out("")
    out("RESULT: " + ("FAIL - fix the lines marked FAIL" if rep.failed else "ready to run"))
    return 1 if rep.failed else 0
