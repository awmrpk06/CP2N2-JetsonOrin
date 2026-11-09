# BioPattern Gate

BioPattern Gate is the access-independent application core for the Cortical
Labs case study. Two hidden, charge-equivalent logical input patterns differ
only in temporal order. A dynamic reservoir response is converted into a
small, fixed feature vector and passed to a frozen linear readout. The
committed decision routes the result to the `left` or `right` gate before the
expected label is revealed.

The current `technical-e3` line has two complementary implementations:

- the fast provider-neutral deterministic test double used by the CP2N2
  lifecycle and replay demonstrations; and
- a complete CL API integration path that executes `StimPlan`, recording,
  analysed reads, application data streams, abort, and HDF5 reconstruction
  against a stimulation-responsive CL SDK data source.

Both are E3 software evidence. The responsive source is not a biological
model, and neither implementation's accuracy may be reported as PNN
performance.

Run the local demonstration from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\run_biopattern_gate_demo.py
```

Run the same frozen application through the complete constrained CP²N²
lifecycle (including dry run, exclusive lease, prepare, execute, result,
audit references, and automatic release):

```powershell
.\.venv\Scripts\python.exe scripts\run_biopattern_gate_mcp_e3.py
```

The MCP result exposes only a sanitized aggregate and checksum-bearing
artifact references. It binds the server-owned preset to exact application
source, configuration, and decoder hashes. No raw spike events or physical
control parameters cross the agent boundary.

## Visual replay demo

The paper-oriented dashboard combines two recorded evidence sources:

- deterministic simulated trial observations for the gate, neural, and
  decision views; and
- a real execution through the constrained MCP surface for the control-plane
  view, including the MCP request transcript, hash-chained audit log,
  LifecycleStore transitions, sanitized result reference, and automatic lease
  release.

The checked-in control-plane capture is verified whenever the visual replay
bundle is regenerated. The resulting dashboard remains a replay: it does not
contact a substrate or execute MCP calls while it is being viewed. Start it
from an activated virtual environment:

```powershell
python scripts\serve_biopattern_gate_demo.py
```

The command opens `http://127.0.0.1:8765/`. Use `--no-open` when the browser
should not be opened automatically. The dashboard is unmistakably labeled
`E3`, `REPLAY`, `MCP AUDITED`, and `NO BIOLOGICAL CLAIM`; its score is a
deterministic pipeline assertion, not PNN performance.

The recorded source evidence lives in
`evaluation/fixtures/biopattern-gate-control-plane-e3-transcript-v1.json` and
`evaluation/fixtures/biopattern-gate-control-plane-e3-audit-v1.jsonl`. Verify
their binding to the visual bundle with:

```powershell
python scripts\export_biopattern_gate_demo_bundle.py --check
```

A user-oriented explanation of every visible area and its evidence meaning is
available in
[`docs/biopattern-gate-web-demo-guide.md`](../../docs/biopattern-gate-web-demo-guide.md).

Verify that the checked-in JSON Schema matches the frozen configuration model:

```powershell
.\.venv\Scripts\python.exe scripts\export_biopattern_gate_schema.py --check
```

## Hardware boundary

The core exposes a narrow `BioPatternGatePort`. `CLApiBioPatternGatePort`
already translates logical groups into the documented CL API, but its only
registered runtime configuration is simulator-only. The repository
intentionally contains no executable provider preset. Hardware modes fail
validation unless all of the following are present:

- `provider_approved` preset namespace;
- attested `cl1` runtime and E5 evidence context;
- verified provider API contract;
- exact approval references;
- calibration reference;
- matching frozen decoder and configuration hashes.

The provider-approved namespace remains empty until those facts can be
verified with the granted Cortical Labs environment.

The implemented CL API flow, local execution command, expected native HDF5
evidence, and remaining provider boundary are documented in
[`docs/cl-api-biopattern-gate-execution.md`](../../docs/cl-api-biopattern-gate-execution.md).
The questions to resolve during grant onboarding are maintained in
[`docs/cortical-labs-cloud-onboarding-questions.md`](../../docs/cortical-labs-cloud-onboarding-questions.md).
