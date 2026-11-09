"""Deterministic application-layer network and telemetry fault injection."""

from __future__ import annotations

import hashlib
import random
import threading
import time
from dataclasses import dataclass

from .config import LinkProfile, NetworkProfile


@dataclass(frozen=True)
class FaultDecision:
    delay_ms: float
    dropped: bool
    partitioned: bool


class DeterministicFaultEngine:
    """Seeded impairment engine whose decisions are reproducible per request."""

    def __init__(self, profile: NetworkProfile, link_name: str) -> None:
        self.profile = profile
        self.link_name = link_name
        self.link: LinkProfile = getattr(profile, link_name)
        self._lock = threading.Lock()
        self._request_index = 0

    def decide(self, trace_id: str, operation: str) -> FaultDecision:
        with self._lock:
            self._request_index += 1
            request_index = self._request_index

        digest = hashlib.sha256(
            (
                f"{self.profile.seed}:{self.link_name}:{trace_id}:"
                f"{operation}"
            ).encode("utf-8")
        ).digest()
        rng = random.Random(int.from_bytes(digest[:8], "big"))
        jitter = rng.uniform(-self.link.jitter_ms, self.link.jitter_ms)
        delay_ms = max(0.0, self.link.latency_ms + jitter)
        partitioned = (
            self.link.partition_every_n_requests is not None
            and request_index % self.link.partition_every_n_requests == 0
        )
        dropped = partitioned or rng.random() < self.link.loss_rate
        if partitioned:
            delay_ms += self.link.partition_duration_ms
        return FaultDecision(
            delay_ms=round(delay_ms, 6),
            dropped=dropped,
            partitioned=partitioned,
        )

    @staticmethod
    def apply(decision: FaultDecision) -> None:
        if decision.delay_ms > 0:
            time.sleep(decision.delay_ms / 1000.0)


class InjectedNetworkFault(RuntimeError):
    def __init__(self, decision: FaultDecision) -> None:
        kind = "partition" if decision.partitioned else "loss"
        super().__init__(f"deterministic {kind} fault")
        self.kind = kind
        self.decision = decision
