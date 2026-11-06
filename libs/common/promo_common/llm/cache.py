"""Content-hash cache. File-backed by default so a re-run resumes with zero re-billing."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Protocol


def make_key(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode())
        h.update(b"\x1f")
    return h.hexdigest()


class Cache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(self, key: str, value: dict[str, Any]) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self._d: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._d.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._d[key] = value


class FileCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        p = self._path(key)
        try:
            data: dict[str, Any] = json.loads(p.read_text())
            return data
        except (OSError, ValueError):
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(value))
        tmp.replace(p)  # atomic: a crash never leaves a half-written entry
