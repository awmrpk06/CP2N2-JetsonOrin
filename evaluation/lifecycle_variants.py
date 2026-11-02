"""Evaluation-only lifecycle-history corrections applied in-process to a pinned CP2N2 LifecycleStore.

The pinned checkout is never edited. ``indexed`` keeps every transition and every query result of the
original store but answers ``history(correlation_id=...)`` from a per-correlation index instead of a scan
of the whole history. ``bounded`` adds the index and also keeps only the newest ``retention_limit``
transitions in memory, which changes audit semantics: older transitions are no longer queryable.

This module imports nothing from this repository, so the worker can load it by file location next to the
pinned upstream, and ``pytest -p lifecycle_variants`` (with a directory holding only this module on
PYTHONPATH) can run the upstream suite against a variant (``PHYS_MCP_LIFECYCLE_VARIANT`` and
``PHYS_MCP_RETENTION_LIMIT`` select it).
"""

from __future__ import annotations

import os
from collections import deque

VARIANTS = ("pinned", "indexed", "bounded")
SEMANTICS = {
    "pinned": "unmodified v5.1.1 LifecycleStore",
    "indexed": "all transitions retained; history(correlation_id=...) answered from a per-correlation index; query results identical to the pinned store",
    "bounded": "per-correlation index plus in-memory retention of only the newest retention_limit transitions; older transitions are evicted and no longer queryable (audit semantics changed)",
}
_CLASSES: dict[tuple[type, str], type] = {}


def validate(variant: str, retention_limit: int | None) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"Lifecycle variant must be one of {VARIANTS}")
    if variant == "bounded":
        if type(retention_limit) is not int or retention_limit < 1:
            raise ValueError("The bounded variant needs an integer retention_limit >= 1")
    elif retention_limit is not None:
        raise ValueError("retention_limit applies only to the bounded variant")


def variant_class(base: type, variant: str) -> type:
    """Subclass of the pinned store class; its methods take the store's own reentrant lock around the original ones."""
    key = (base, variant)
    if key in _CLASSES:
        return _CLASSES[key]

    class CorrectedLifecycleStore(base):
        lifecycle_variant = variant

        def _prepare_variant(self, retention_limit: int | None) -> None:
            with self._lock:
                self._retention_limit = retention_limit
                self._evicted = 0
                self._by_correlation: dict[str | None, deque] = {}
                if variant == "bounded":
                    self._history = deque(self._history)
                for item in self._history:
                    self._by_correlation.setdefault(item.correlation_id, deque()).append(item)
                self._evict()

        def _evict(self) -> None:
            limit = self._retention_limit
            while limit is not None and len(self._history) > limit:
                old = self._history.popleft()
                bucket = self._by_correlation[old.correlation_id]
                # Per-correlation buckets preserve global order, so the evicted record is the bucket's oldest.
                bucket.popleft()
                if not bucket:
                    del self._by_correlation[old.correlation_id]
                self._evicted += 1

        def transition(self, resource_id, to_state, **options):
            with self._lock:
                tail = self._history[-1] if self._history else None
                updated = super().transition(resource_id, to_state, **options)
                if self._history and self._history[-1] is not tail:
                    item = self._history[-1]
                    self._by_correlation.setdefault(item.correlation_id, deque()).append(item)
                    self._evict()
                return updated

        def history(self, *, resource_id=None, correlation_id=None):
            if correlation_id is None:
                return super().history(resource_id=resource_id)
            with self._lock:
                records = self._by_correlation.get(correlation_id, ())
                if resource_id is not None:
                    records = [item for item in records if item.resource_id == resource_id]
                return [item.model_copy(deep=True) for item in records]

        def variant_counts(self) -> dict:
            with self._lock:
                return {
                    "history": len(self._history), "indexed_correlations": len(self._by_correlation),
                    "indexed_records": sum(len(bucket) for bucket in self._by_correlation.values()), "evicted": self._evicted,
                }

    CorrectedLifecycleStore.__name__ = CorrectedLifecycleStore.__qualname__ = f"{variant.capitalize()}LifecycleStore"
    _CLASSES[key] = CorrectedLifecycleStore
    return CorrectedLifecycleStore


def install(store, variant: str, retention_limit: int | None = None) -> dict:
    """Switch one live store to a variant before any measured request; returns a provenance record."""
    validate(variant, retention_limit)
    base = type(store)
    record = {"variant": variant, "retention_limit": retention_limit, "semantics": SEMANTICS[variant], "base_class": f"{base.__module__}.{base.__qualname__}"}
    if variant == "pinned":
        return record
    if getattr(base, "lifecycle_variant", None) is not None:
        raise RuntimeError("Lifecycle store already carries a variant")
    with store._lock:
        store.__class__ = variant_class(base, variant)
        store._prepare_variant(retention_limit)
    record.update(installed_class=type(store).__name__, counts_at_install=store.variant_counts())
    return record


def counts(store) -> dict | None:
    return store.variant_counts() if hasattr(store, "variant_counts") else None


def pytest_configure(config) -> None:
    """Run the pinned upstream suite with a variant: put a directory holding only this module on PYTHONPATH, then ``pytest -p lifecycle_variants``."""
    variant = os.environ.get("PHYS_MCP_LIFECYCLE_VARIANT", "pinned")
    limit = os.environ.get("PHYS_MCP_RETENTION_LIMIT")
    limit = int(limit) if limit else None
    validate(variant, limit)
    if variant == "pinned":
        return
    from core import lifecycle

    base = lifecycle.LifecycleStore
    corrected = variant_class(base, variant)
    original_init = base.__init__

    # Patching construction catches every store, however the class name was imported.
    def __init__(self) -> None:
        original_init(self)
        if type(self) is base:
            self.__class__ = corrected
            self._prepare_variant(limit)

    base.__init__ = __init__
    config.stash_lifecycle_variant = (variant, limit)


def pytest_report_header(config) -> str | None:
    selected = getattr(config, "stash_lifecycle_variant", None)
    return None if selected is None else f"lifecycle variant: {selected[0]} (retention_limit={selected[1]})"
