"""Versioned prompt templates: prompts/<name>_v<N>.md with ## SYSTEM and ## USER sections."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from promo_finder.infrastructure.config import find_prompts_dir


@dataclass(frozen=True)
class Prompt:
    name: str  # e.g. score_v1
    system: str
    user: str
    sha: str

    @property
    def version(self) -> str:
        return self.name.rsplit("_", 1)[-1]

    def render(self, **values: str) -> tuple[str, str]:
        def fill(t: str) -> str:
            return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), t)

        return fill(self.system), fill(self.user)


def load_prompt(name: str, directory: Path | None = None) -> Prompt:
    path = (directory or find_prompts_dir()) / f"{name}.md"
    raw = path.read_text()
    m = re.search(r"^## SYSTEM\s*\n(.*?)^## USER\s*\n(.*)$", raw, re.S | re.M)
    if not m:
        raise ValueError(f"{path.name}: needs '## SYSTEM' and '## USER' sections")
    return Prompt(
        name, m.group(1).strip(), m.group(2).strip(), hashlib.sha256(raw.encode()).hexdigest()[:12]
    )
