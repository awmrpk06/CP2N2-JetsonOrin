# Changelog

## v5.1.1 — 2026-08-11

### Changed

- Synchronized the frozen selector artifacts with the current five-policy
  evaluation: static priority, constraint based, random admissible, weighted
  comparison, and the principal CP²N² lexicographic policy.
- Added a versioned selector reference run with holdout results and 32 seeded
  weighted-heuristic perturbations.

### Published evidence

- Added the complete A6/RQ2 reference-run archive as a release asset, including
  all 945 request records, 30 aggregate cells, configurations, traces, figures,
  and its verified per-file SHA-256 manifest.
- Bound the A6 archive and synchronized selector reference run to one v5.1.1
  evidence package and checksum set.

### Verification and release boundary

- This is an evidence-publication patch release; it does not change control-plane
  behaviour or the E3 evidence boundary.
- The selector conformance suite records 7/7 for the principal lexicographic and
  weighted-comparison policies and 6/7 for each of the three simpler baselines.
- The four-case holdout suite records 4/4, and the principal selection remains
  unchanged across all 32 seeded weight perturbations.
- A6 remains a controlled same-host software experiment and is not physical-CL1
  or biological-performance evidence.

## v5.1.0 — 2026-07-31

### Added

- A provider-facing BioPattern Gate execution port using the documented
  Cortical Labs CL API for atomic stimulation plans, analysed spike reads,
  application data streams, native HDF5 recording, interruption, and cleanup.
- A stimulation-responsive CL SDK Simulator source that exercises the same
  application path without presenting simulated activity as biological
  evidence.
- Independent reconstruction of complete and aborted native HDF5 recordings,
  including exact comparison of recorded features and decisions with the
  online result.
- A technical CL API execution guide and a versioned Cortical Cloud onboarding
  question set covering authentication, lifecycle, deployment, safety,
  provenance, export, and publication.

### Changed

- Updated the BioPattern Gate CL application to version 0.2.0.
- Replaced slash-delimited application stream names with HDF5-safe names and
  added native `cl_spikes` and `cl_stims` visualizer subscriptions.
- Rebound the constrained MCP preset and frozen E3 evidence artifacts to the
  expanded application source.

### Fixed

- Preserve partial native recordings on cooperative application abort.
- Validate observed stimulation acknowledgements and anchor response windows
  to the final observed stimulation rather than an assumed local timestamp.

### Verification and release boundary

- The complete headless test suite passes with 146 tests.
- The official CL application runner completes 14 trials, 24 stimulation
  events, and 60 spike events with exact online/offline agreement.
- This remains E3 SDK-simulator evidence. The release does not claim physical
  CL1 execution, biological performance, or an implemented Cortical Cloud
  authentication and lifecycle contract.

## v5.0 — 2026-07-30

### Added

- BioPattern Gate as an E3 Cortical Labs SDK-simulator application with a
  constrained assay surface, lifecycle/lease control, result validation,
  audit artifacts, replay fixtures, and an explanatory web demo.
- A frozen Agent-to-PNN evaluation campaign with eight prompt classes,
  deterministic oracles, per-trial audit verification, summary artifacts, and
  a University of Lübeck AI-Lab pilot.
- User documentation for the BioPattern Gate web demo and the agent-facing MCP
  surface.

### Changed

- Renamed the project from **phys-MCP** to **CP²N² — Control Plane for Physical
  Neural Networks**.
- Adopted `cp2n2`, `CP2N2`, and `CP2N2_` as the canonical technical,
  Python-class, and environment-variable forms.
- Retained compatibility aliases and legacy environment-variable fallbacks;
  no control-plane behavior changed.
- Extended the paper-facing evidence package while preserving the E3 boundary:
  the SDK simulator demonstrates software integration, not biological
  performance or physical CL1 execution.

### Fixed

- Made frozen audit-fixture hashing independent of CRLF/LF checkout
  conversion, while retaining byte-level verification of the canonical JSONL
  representation.

### Verification and release boundary

- The complete headless test suite passes with 142 tests.
- The confirmatory Agent-to-PNN v1.2 archive contains 160 decisions and 160
  independently verified audit chains; no substrate was executed.
- The BioPattern Gate control-plane trace remains E3 SDK-simulator evidence
  and makes no physical-CL1 or biological-performance claim.
- The release archive, checksum manifest, GitHub tag, Zenodo record, and paper
  citation must all identify the same immutable commit.

## v4.0 — 2026-07-24

The first release after the frozen v3.0 baseline. This release completes the
accepted A0-A7 development line.

### Added

- Versioned, substrate-neutral Physical Neural Resource Contract v1.0.
- Runtime evidence, telemetry provenance/freshness, safety, access, cost, and
  data-governance fields with fail-closed admissibility checks.
- Lifecycle and lease protocol with reservations, state-version checks,
  idempotency, phase timeouts, abort, reconciliation, and typed errors.
- Admission, feasibility, and documented backend-selection policies.
- General adapter architecture separating control adapters from substrate
  runtimes.
- Constrained agent-facing MCP surface with plan validation, approvals, audit,
  and server-owned presets.
- Gemini, Ollama, and University of Lübeck AI-Lab agent integrations.
- Distributed four-process testbed, versioned deployment/fault profiles, and
  reproducible RQ2 evaluation artifacts.
- Expanded CL SDK Simulator and attested CL integration path.
- Installation, operation, evaluation, and evidence-boundary documentation.

### Verification

- 91 automated tests pass in the release branch.
- AI-Lab reference evaluation: 2/2 successful dry-run cases.
- AI-Lab evaluation explicitly records `pnn_evidence=false` and
  `substrate_executed=false`.

### Evidence boundary

v4.0 demonstrates a substrate-aware control plane and reproducible simulator,
synthetic-twin, and agent-control workflows. It does not claim that the
prototype has executed a biological PNN. The University AI-Lab integration is
an LLM-agent runtime, not a PNN backend.

## v3.0

Frozen legacy baseline. See the `v3.0` tag for the corresponding source state.
