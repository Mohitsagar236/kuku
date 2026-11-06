"""Client-side RPM + RPD enforcement. The daily counter is persisted so runs can resume."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from promo_common.llm.errors import DailyQuotaExhausted


class RateLimiter:
    def __init__(
        self,
        rpm: int = 0,
        rpd: int = 0,
        state_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], str] = lambda: datetime.now(UTC).date().isoformat(),
    ) -> None:
        self.rpm, self.rpd = rpm, rpd
        self._path, self._clock, self._sleep, self._today = state_path, clock, sleep, today
        self._window: deque[float] = deque()
        self._lock = threading.Lock()
        self._day, self._count = self._load()

    def _load(self) -> tuple[str, int]:
        today = self._today()
        if self._path and self._path.is_file():
            try:
                d = json.loads(self._path.read_text())
                if d.get("date") == today:
                    return today, int(d.get("count", 0))
            except (ValueError, OSError):
                pass
        return today, 0

    def _persist(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"date": self._day, "count": self._count}))

    @property
    def used_today(self) -> int:
        return self._count

    def mark_exhausted(self) -> None:
        """The provider told us the daily quota is gone: stop spending for today."""
        with self._lock:
            if self.rpd:
                self._count = max(self._count, self.rpd)
            else:
                self._count, self.rpd = 10**9, 1
            self._persist()

    def acquire(self) -> None:
        """Block until a request may be sent; raise DailyQuotaExhausted if today's cap is hit."""
        while True:
            with self._lock:
                today = self._today()
                if today != self._day:
                    self._day, self._count = today, 0
                if self.rpd and self._count >= self.rpd:
                    raise DailyQuotaExhausted(f"client-side daily limit reached ({self.rpd})")
                now = self._clock()
                while self._window and now - self._window[0] >= 60.0:
                    self._window.popleft()
                if not self.rpm or len(self._window) < self.rpm:
                    self._window.append(now)
                    self._count += 1
                    self._persist()
                    return
                wait = 60.0 - (now - self._window[0])
            self._sleep(max(wait, 0.01))
