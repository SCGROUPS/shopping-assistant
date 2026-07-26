"""Counters for the failures that do not show up as failures.

When intent extraction breaks, `search/service.py` catches the exception and
falls back to deterministic parsing. The shopper gets HTTP 200 and a full page
of products, which is the right call - a page beats a 500 - but it means the
service looks perfectly healthy while it has stopped understanding anything
anyone types. Two production outages ran for days behind that 200.

So the fallback is kept and made *countable*. Nothing here changes what a
shopper sees; it changes whether we can tell that it happened.

The counts are per-process and reset on restart. With several replicas each
reports its own, which is a feature rather than a limitation: a single sick
replica shows up as one bad ratio instead of being averaged into invisibility.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class DegradationSnapshot:
    calls: int
    failures: int

    @property
    def failure_ratio(self) -> float:
        """Failures per call, 0.0 before any call.

        A ratio rather than a raw count because the raw count only means
        something next to traffic: five failures is a blip on a busy day and a
        total outage on a quiet one.
        """
        if not self.calls:
            return 0.0
        return self.failures / self.calls


@dataclass(frozen=True)
class ProbeStatus:
    ok: bool
    detail: str


class _IntentHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls = 0
        self._failures = 0
        self._probe: ProbeStatus | None = None

    def record_call(self) -> None:
        with self._lock:
            self._calls += 1

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1

    def record_probe(self, *, ok: bool, detail: str) -> None:
        with self._lock:
            self._probe = ProbeStatus(ok=ok, detail=detail)

    def probe(self) -> ProbeStatus | None:
        """None means the startup probe has not answered yet, not that it passed."""
        with self._lock:
            return self._probe

    def snapshot(self) -> DegradationSnapshot:
        with self._lock:
            return DegradationSnapshot(calls=self._calls, failures=self._failures)

    def reset(self) -> None:
        """Only for tests; a counter shared between them proves nothing."""
        with self._lock:
            self._calls = 0
            self._failures = 0
            self._probe = None


intent_health = _IntentHealth()
