"""Where a request's time goes: NetBox calls, Annet runs, Prometheus queries; and the
request's NetBox read cache.

Each API request gets a fresh tally in a context variable; the clients add to it, and the
middleware in app.py logs one line per request and sends it back as a `Server-Timing` header
(shown in the browser's developer tools, Network tab, Timing). Background work (drift, deploys,
ZTP) runs outside any request and is not counted.

The same object caches the request's NetBox reads (orchestrator/netbox.py): one request often
looks up the same device or context several times. Any write empties the cache. A task started
by a request (a diff or deploy job) inherits the context variable, so the request closes its
tally when it ends: a closed one neither counts nor caches.
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class Tally:
    calls: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    reads: dict = field(default_factory=dict)   # (path, params) -> NetBox response body
    open: bool = True

    def add(self, kind: str, seconds: float) -> None:
        if not self.open:
            return
        self.calls[kind] = self.calls.get(kind, 0) + 1
        self.seconds[kind] = self.seconds.get(kind, 0.0) + seconds

    def summary(self) -> str:
        return " ".join(f"{k}={self.calls[k]}/{self.seconds[k] * 1000:.0f}ms" for k in sorted(self.calls))

    def header(self, total: float) -> str:
        parts = [f'{k};dur={self.seconds[k] * 1000:.1f};desc="{self.calls[k]} calls"' for k in sorted(self.calls)]
        return ", ".join([*parts, f"total;dur={total * 1000:.1f}"])


current: ContextVar[Tally | None] = ContextVar("timing", default=None)

# reads made here may come from the NetBox client's cache shared across requests (at most
# SHARED_SECONDS old, emptied by any write the orchestrator makes). Set for GET requests and the
# read-only background loops; never for writes or jobs, which always read NetBox itself.
shared: ContextVar[bool] = ContextVar("shared_reads", default=False)


def request_reads() -> dict | None:
    """The current request's NetBox read cache, or None outside a request."""
    tally = current.get()
    return tally.reads if tally is not None and tally.open else None


@contextmanager
def measure(kind: str):
    """Count the enclosed call against the current request, if there is one."""
    tally = current.get()
    start = time.perf_counter()
    try:
        yield
    finally:
        if tally is not None:
            tally.add(kind, time.perf_counter() - start)
