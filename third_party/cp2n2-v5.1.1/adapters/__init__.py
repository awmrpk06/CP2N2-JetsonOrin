"""Adapter package exports for CP²N²."""

from .base_adapter import BaseAdapter, AdapterInvocationResult, AdapterPreparationResult
from .contracts import (
    ADAPTER_ARCHITECTURE_VERSION,
    AdapterCapabilityDeclaration,
    RuntimeCapabilityDeclaration,
)
from .chemical_adapter import ChemicalAdapter
from .edge_adapter import EdgeAdapter
from .fault_injecting_adapter import FaultInjectingAdapter
from .remote_edge_adapter import RemoteEdgeAdapter
from .wetware_adapter import WetwareAdapter

__all__ = [
    "BaseAdapter",
    "AdapterInvocationResult",
    "AdapterPreparationResult",
    "ADAPTER_ARCHITECTURE_VERSION",
    "AdapterCapabilityDeclaration",
    "ChemicalAdapter",
    "EdgeAdapter",
    "FaultInjectingAdapter",
    "RemoteEdgeAdapter",
    "RuntimeCapabilityDeclaration",
    "WetwareAdapter",
    "CorticalLabsAdapter",
]


def __getattr__(name: str):
    """Load the optional CL integration only when explicitly requested."""

    if name == "CorticalLabsAdapter":
        from .cortical_labs_adapter import CorticalLabsAdapter

        return CorticalLabsAdapter
    raise AttributeError(name)
