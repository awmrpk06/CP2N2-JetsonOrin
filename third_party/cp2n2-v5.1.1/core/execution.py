"""Bounded waiting for potentially blocking adapter phases."""

from __future__ import annotations

from contextvars import copy_context
from dataclasses import dataclass
from queue import Queue
from threading import Thread
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


@dataclass
class PhaseOutcome(Generic[T]):
    """Result of waiting for one adapter/control-plane phase."""

    value: T | None = None
    error: Exception | None = None
    timed_out: bool = False


def run_with_timeout(call: Callable[[], T], timeout_ms: float) -> PhaseOutcome[T]:
    """Wait at most *timeout_ms* without claiming cancellation on timeout.

    The worker is daemonized because Python cannot safely terminate an
    arbitrary provider call.  A timeout therefore means status uncertainty;
    the caller must abort or reconcile explicitly.
    """

    results: Queue[tuple[str, object]] = Queue(maxsize=1)
    context = copy_context()

    def target() -> None:
        try:
            results.put(("value", context.run(call)))
        except Exception as exc:  # pragma: no cover - exercised through callers
            results.put(("error", exc))

    worker = Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout=timeout_ms / 1000.0)
    if worker.is_alive():
        return PhaseOutcome(timed_out=True)

    kind, payload = results.get()
    if kind == "error":
        return PhaseOutcome(error=payload)  # type: ignore[arg-type]
    return PhaseOutcome(value=payload)  # type: ignore[arg-type]
