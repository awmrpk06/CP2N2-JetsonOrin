"""Substrate-side runtime implementations for the A5 architecture."""

from runtimes.base_runtime import SubstrateRuntime

__all__ = [
    "ChemicalTwinRuntime",
    "CorticalLabsRuntime",
    "EdgeTwinRuntime",
    "RemoteEdgeRuntime",
    "SubstrateRuntime",
    "WetwareTwinRuntime",
]


def __getattr__(name: str):
    """Keep optional/provider runtimes out of the generic import path."""

    if name == "CorticalLabsRuntime":
        from runtimes.cortical_labs_runtime import CorticalLabsRuntime

        return CorticalLabsRuntime
    if name == "RemoteEdgeRuntime":
        from runtimes.remote_edge_runtime import RemoteEdgeRuntime

        return RemoteEdgeRuntime
    if name in {
        "ChemicalTwinRuntime",
        "EdgeTwinRuntime",
        "WetwareTwinRuntime",
    }:
        from runtimes.twin_runtimes import (
            ChemicalTwinRuntime,
            EdgeTwinRuntime,
            WetwareTwinRuntime,
        )

        return {
            "ChemicalTwinRuntime": ChemicalTwinRuntime,
            "EdgeTwinRuntime": EdgeTwinRuntime,
            "WetwareTwinRuntime": WetwareTwinRuntime,
        }[name]
    raise AttributeError(name)
