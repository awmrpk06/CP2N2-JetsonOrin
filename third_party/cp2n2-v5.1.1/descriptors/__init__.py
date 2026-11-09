"""Descriptor and resource-contract models for the CP²N² prototype."""

from descriptors.resource_contract import (
    CONTRACT_SCHEMA_VERSION,
    ContractAdmissionResult,
    PhysicalNeuralResourceContract,
    assess_contract_admission,
)

__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "ContractAdmissionResult",
    "PhysicalNeuralResourceContract",
    "assess_contract_admission",
]
