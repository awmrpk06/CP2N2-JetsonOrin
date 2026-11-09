"""Frozen identifiers binding BioPattern Gate to the CP²N² E3 preset."""

from __future__ import annotations


APPLICATION_ID = "cp2n2-biopattern-gate"
BACKEND_ID = "cortical-labs-biopattern-gate-e3"
ASSAY_PRESET = "pattern_gate_v1"
CONFIG_ID = "technical-e3"
CONFIG_SHA256 = (
    "5fccaac3022e223fc181508833eaad387"
    "55279556b43b6b2df4e2f7e032a08e4"
)
DECODER_SHA256 = (
    "42789a20ea16e048f1a23b28e601ff34"
    "45e64b125c48de8656b11e612991afbf"
)
RUNTIME_KIND = "sdk_simulator"
EVIDENCE_LEVEL = "E3"

TASK_METADATA = {
    "assay_preset": ASSAY_PRESET,
    "application_id": APPLICATION_ID,
    "config_id": CONFIG_ID,
    "config_sha256": CONFIG_SHA256,
    "decoder_sha256": DECODER_SHA256,
    "runtime_kind_required": RUNTIME_KIND,
    "evidence_ceiling": EVIDENCE_LEVEL,
}

