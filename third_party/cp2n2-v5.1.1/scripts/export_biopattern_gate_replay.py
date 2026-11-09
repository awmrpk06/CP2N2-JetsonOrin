"""Regenerate the deterministic E3 success replay fixture."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from applications.biopattern_gate.config import BioPatternGateConfig  # noqa: E402
from applications.biopattern_gate.scheduler import (  # noqa: E402
    TrialKind,
    build_trial_schedule,
)
from applications.biopattern_gate.simulator import (  # noqa: E402
    DeterministicReservoirSimulator,
)


APP_ROOT = ROOT / "applications" / "biopattern_gate"
TARGET = (
    ROOT
    / "evaluation"
    / "fixtures"
    / "biopattern-gate-replay-success-v1.json"
)


def main() -> int:
    config = BioPatternGateConfig.model_validate_json(
        (
            APP_ROOT / "presets" / "simulator" / "technical-e3.json"
        ).read_text(encoding="utf-8")
    )
    simulator = DeterministicReservoirSimulator()
    simulator.prepare(config)
    observations = []
    for plan in build_trial_schedule(config.schedule):
        sequence = {
            TrialKind.PATTERN_A: config.pattern_a.sequence,
            TrialKind.PATTERN_B: config.pattern_b.sequence,
            TrialKind.SHAM: None,
        }[plan.kind]
        observation = simulator.observe_trial(
            plan,
            logical_sequence=sequence,
            config=config,
        )
        observations.append(
            {
                "trial_index": observation.trial_index,
                "events": [
                    {
                        "timestamp_ms": event.timestamp_ms,
                        "readout_group_ref": event.readout_group_ref,
                    }
                    for event in observation.events
                ],
                "telemetry": observation.telemetry,
            }
        )
    bundle = {
        "bundle_version": "1.0",
        "config_sha256": config.sha256(),
        "runtime_kind": "sdk_simulator",
        "evidence_label": "E3_REPLAY",
        "observations": observations,
    }
    TARGET.write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
