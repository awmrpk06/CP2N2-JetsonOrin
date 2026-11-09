# BioPattern Gate Through the Cortical Labs CL API

## Purpose and evidence boundary

This document describes the provider-facing execution path implemented for
BioPattern Gate. The path uses the documented Cortical Labs API for
stimulation, recording, spike observation, application data streams, and
cleanup. It currently runs only with the CL SDK Simulator and therefore has an
evidence ceiling of E3.

The stimulation-responsive simulator source is a deterministic software
fixture. It is not a culture model, does not evaluate a PNN, and produces no
biological evidence. A physical CL1 run remains impossible until a separate
provider-approved preset, allocation, approval, calibration reference, and
Cloud lifecycle contract exist.

## Implemented execution path

The CL application performs the following sequence:

1. validate the frozen BioPattern Gate configuration and decoder hashes;
2. reject execution unless `cl.is_simulator()` is true;
3. register an importable stimulation-responsive `cl.sim` data source;
4. open the CL API with control and recording readiness enabled;
5. verify the 25 kHz sample rate and 64-channel shape;
6. capture `cl.get_system_attributes()`;
7. create six HDF5-safe application data streams;
8. start one native HDF5 recording with explicit modality settings;
9. submit every non-sham temporal pattern as one atomic `StimPlan`;
10. read a bounded window through `neurons.read(..., analysis=True)`;
11. validate the observed stimulation channels, order, and interval;
12. extract only approved readout-channel spikes after the blanking boundary;
13. record features and the blinded decoder commitment in data streams;
14. stop the recording, return a compact `RunSummary`, and write the complete
    per-trial result to `result.json`;
15. reopen the HDF5 independently and prove that recorded features and
    decisions match the online result.

An application failure interrupts every configured input channel, marks the
recording as aborted, preserves partial streams, and closes the CL connection.

## Fixed technical E3 channel and stimulation mapping

The E3-only runtime configuration is server-owned:

- `sim-input-left`: channel 8;
- `sim-input-right`: channel 9;
- `sim-readout`: channels 20, 21, 22, and 23;
- biphasic stimulation: 160 microseconds at -1 microampere followed by
  160 microseconds at +1 microampere;
- base lead time: 200 microseconds;
- raw samples: explicitly disabled for the compact E3 recording.

These values comply with the documented SDK constraints but have no authority
for a physical CL1. They must not be copied into an E5 preset without provider
and researcher approval.

## Temporal pattern and observation

Pattern A stimulates the left logical group and then the right logical group.
Pattern B reverses the order. Both use the same pulse design and the same
20-millisecond inter-step interval.

The port does not assume that a submitted stimulation occurred. It validates
the `DetectionResult.stims` acknowledgements from the CL API. The observation
window is anchored to the final observed stimulation timestamp. Spikes are
accepted only when they:

- occur inside the frozen observation window;
- belong to one of the approved readout channels; and
- map to a declared logical readout group.

Unknown groups and out-of-window events invalidate the run.

## Application data streams

The application publishes:

- `pattern_gate_session`;
- `pattern_gate_trial`;
- `pattern_gate_gate`;
- `pattern_gate_features`;
- `pattern_gate_decision`;
- `pattern_gate_control_status`.

Underscores are intentional. Slash-delimited names fail when the SDK creates
the corresponding HDF5 groups. The visualizer also subscribes to the native
`cl_spikes` and `cl_stims` streams.

The control-status stream reports only what the on-device application knows.
It does not fabricate CP2N2 lease or Cloud lifecycle evidence. The
authoritative lease, approval, and remote lifecycle remain in the CP2N2 audit
and provider records.

## Running the complete CL application locally

From the repository root:

```powershell
$env:PYTHONUTF8 = "1"
$env:CL_SDK_VISUALISATION = "0"
$env:CL_SDK_ACCELERATED_TIME = "1"
.\.venv\Scripts\python.exe -m cl.app.run `
  cl-apps\cp2n2-biopattern-gate `
  cl-apps\cp2n2-biopattern-gate\default.json `
  .physmcp\cl-api-app-run
```

The expected deterministic result contains:

- 14 trials;
- 12 scored A/B trials;
- 2 sham trials;
- 24 native stimulation events;
- 60 native spike events;
- exact agreement between online and HDF5-reconstructed decisions;
- `runtime_kind=sdk_simulator`;
- `evidence_ceiling=E3`;
- `biological_claim=false`.

The accelerated mode is a development convenience. It does not replace
wall-clock testing, device-side safety enforcement, or an E5 pilot.

## Packaging

Build the official ZIP and deterministic manifest with:

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe scripts\package_biopattern_gate_cl_app.py
```

The script synchronizes the canonical core into the self-contained
application, invokes `cl.app.pack`, and records per-entry and archive SHA-256
values.

## Remaining CL1 boundary

The following items remain blocked:

- official Cloud authentication and credential handling;
- resource discovery and culture selection;
- application upload, installation, versioning, and removal;
- reservation, queueing, run start, status, abort, and reconciliation;
- artifact export and retention;
- provider-approved physical stimulation, channel mapping, blanking,
  recording, cooldown, and safety rules;
- provider run identity and any signed or otherwise verifiable attestation;
- installation smoke test and every E5 execution.

The device-side `cl.is_simulator()` result and system attributes are useful
runtime provenance. They must not be described as cryptographic attestation
unless Cortical Labs provides an attestation contract.
