# CP²N²

**CP²N² — Control Plane for Physical Neural Networks** was formerly published
as **phys-MCP**. The technical ASCII identifier is `cp2n2`; see the
[naming-migration guide](docs/naming-migration.md) for compatibility details.

The accepted scope, evidence boundaries, completed work, and gated next steps
are maintained in the versioned [project master plan](docs/project-masterplan.md).

## Release v5.1.0

Version 5.1.0 adds the provider-facing BioPattern Gate execution path through
the documented Cortical Labs CL API. It includes stimulation-responsive SDK
simulation, atomic stimulation acknowledgement checks, native HDF5 evidence,
online/offline result comparison, cooperative-abort preservation, and an
updated live visualizer. The path remains deliberately restricted to the SDK
Simulator until Cortical Labs approves the Cloud lifecycle and a physical
CL1 preset. See the
[v5.1.0 release checklist](docs/release-v5.1.0-checklist.md).

The implementation, complete local runner command, and remaining E5 boundary
are documented in the
[CL API execution guide](docs/cl-api-biopattern-gate-execution.md). Questions
requiring authoritative Cortical Cloud answers are maintained in the
[Cloud onboarding checklist](docs/cortical-labs-cloud-onboarding-questions.md).

## Release v5.0

Version 5.0 adds the BioPattern Gate E3 application and audited
web demo, platform-independent evidence hashing, and the frozen Agent-to-PNN
evaluation campaign with University of Lübeck AI-Lab results. It is the
software and evidence baseline for the journal manuscript. Its E3 Cortical
Labs integration validates the controlled software path but is not evidence
of a physical CL1 execution or biological performance. The immutable release
workflow is documented in the
[v5.0 release checklist](docs/release-v5.0-checklist.md).

## Release v4.0

Version 4.0 was released before the rename, under the former **phys-MCP**
name. It
packages the completed A0-A7 development line: versioned resource contracts,
lifecycle-aware orchestration, constrained agent access, general adapters,
distributed evaluation infrastructure, and the University of Lübeck AI-Lab
agent.

The `v3.0` tag remains the reproducible legacy baseline. v4.0 is still a
research prototype: simulator, synthetic-twin, and control-plane results must
not be interpreted as evidence of physical PNN execution unless explicitly
labelled as such.

### Five-minute quick start

From a fresh clone:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
pytest -q
python -m demos.demo_cortical_labs_adapter
```

The copied `.env` is local configuration and must not be committed. The quick
start uses the CL SDK Simulator; it does not contact physical hardware. For
agent examples, local Ollama or the corresponding API credentials must be
configured separately. The AI-Lab evaluation additionally requires University
network/VPN access and an `AI_LAB_API_KEY`; run it only with the explicit
acknowledgement described in [the AI-Lab guide](docs/ai-lab-agent.md).

`CP²N²` is a substrate-aware control-plane prototype for exposing heterogeneous **physical neural network (PNN)** resources as discoverable, invocable, and monitorable software-visible backends.

The system is designed for settings in which materially different computational substrates cannot be treated as ordinary stateless accelerators. Instead, they expose distinct I/O modalities, timing regimes, lifecycle constraints, observability limits, and health or validity conditions. `CP²N²` provides a single orchestration layer above such backends while preserving these substrate-specific semantics.

This repository contains:

- a Python reference implementation of the `CP²N²` control plane
- representative local prototype backends for chemical, wetware, and fast edge-style execution
- an externalized remote edge backend path
- an attested integration path for the **Cortical Labs CL API / CL SDK**
- minimal **Gemini-based**, **Ollama-based**, and **University of Lübeck
  AI-Lab-based** agents that plan and execute tasks through `CP²N²`
- demos, tests, and evaluation scripts

The current code base should be understood as a **research prototype**: it is operational, structured, and demonstrable, but not a production runtime.

---

## 1. Purpose of the system

`CP²N²` exists to answer a practical systems problem:

> How can heterogeneous physical neural substrates be exposed to software in a way that supports discovery, task matching, invocation, monitoring, and lifecycle-aware control without flattening away the properties that actually matter?

The prototype treats physical AI resources as **managed backends** rather than opaque one-off lab integrations. The central idea is that software should be able to ask:

- What backends are available?
- Which task types do they support?
- Which input and output modalities do they require?
- What timing regime do they operate in?
- Are they ready right now?
- What telemetry do they expose?
- Can they be reset, recalibrated, or reused safely?
- Can an agent or orchestrator choose among them in a principled way?

`CP²N²` answers these questions through a substrate-aware descriptor model, a matcher, an orchestrator, and backend-specific adapters.

The post-v3.0 development line also publishes a versioned, substrate-neutral
[Physical Neural Resource Contract](docs/physical-neural-resource-contract-v1.0.md).
It preserves the legacy capability descriptor while adding runtime evidence,
telemetry provenance and freshness, safety constraints, access, cost, and data
governance. Missing safety-relevant information produces an explicit
`INADMISSIBLE` decision before invocation.

It also implements a
[lifecycle and lease protocol](docs/lifecycle-leases-and-errors.md) with
exclusive time-bounded reservations, state-version checks, client-scoped
idempotency, independent phase timeouts, explicit abort, provider
reconciliation, and typed error codes.

Resource choice follows a documented
[admission, feasibility, and selection protocol](docs/admission-feasibility-selection.md).
The default is a non-compensatory lexicographic policy. Latency-, safety- and
locality-oriented profiles, an explicit weighted comparison, and three
reproducible baselines operate only on resources that passed all hard checks.

Backend integrations follow a versioned
[general adapter architecture](docs/general-adapter-architecture.md). Every
control adapter publishes reservation, deployment, state, abort, artifact,
runtime-location, and evidence-ceiling capabilities and delegates
time-critical work to a separate substrate runtime. The CL SDK Simulator is
one optional E3 target; generic chemical, wetware, edge, and service-backed
integrations do not depend on it.

---

## 2. High-level architecture

The implementation follows a three-part structure:

### Control plane
The control plane is responsible for:

- backend discovery
- task-to-substrate matching
- policy checking
- directed or capability-based invocation
- validation and fallback handling
- collection of normalized result and telemetry information

Every integration exposes a small control adapter here. Provider-specific
execution is not implemented in the orchestrator.

### Twin / runtime state
The prototype keeps state that is relevant for runtime decisions, such as:

- readiness
- health
- drift-related signals
- telemetry freshness
- calibration- or validity-like metadata

This is not a full digital twin framework, but it is enough to make runtime state visible to the control logic.

### Data / backend integration layer
The data-plane side is implemented through separate substrate runtimes and
backend-specific client logic:

- local synthetic backends for representative substrate regimes
- a remote edge path via HTTP
- an explicitly attested Cortical Labs path, currently exercised with the CL SDK Simulator
- a foundation for additional future integrations

The compatibility adapter methods delegate to these runtimes, so existing
orchestrator callers remain valid while control and execution responsibilities
are independently testable.

---

## 3. What the prototype can do

### 3.1 Discover heterogeneous backends
The orchestrator can enumerate backends described through a shared descriptor model.

For compatibility, `discover_backends()` returns the v3.0 descriptors.
`discover_resource_contracts()` returns the complete v1.0 resource contracts.
The latter includes the current control-plane lifecycle and its version, not
only provider telemetry.

Each backend publishes information such as:

- substrate class
- supported task types
- input/output contracts
- timing semantics
- lifecycle/reset semantics
- telemetry fields
- locality and tenancy constraints
- health and observability characteristics

### 3.2 Match tasks to backends
Tasks can be routed in two ways:

- **capability-driven**: let the matcher select the best compatible backend
- **directed**: explicitly target a backend such as `cortical-labs-backend`

Matching first separates hard admission constraints from dynamic feasibility.
Only admitted and currently feasible resources are ranked. Every exclusion
names its violated constraint, and every selected resource carries a
machine-readable policy, rank key, normalized criteria, and comparison-weight
breakdown.

### 3.3 Execute tasks with telemetry-aware control
A task execution can include:

- preparation / readiness checks
- session opening
- backend invocation
- postcondition validation
- telemetry collection before and after execution
- optional fallback to another backend

### 3.4 Exercise representative synthetic backend regimes
The prototype includes three core local regimes:

- **chemical backend**
- **wetware backend**
- **edge backend**

These are not intended as faithful physical simulators. Their role is to exercise control-plane behavior under clearly different operational conditions.

### 3.5 Use an externalized backend path
The remote edge path demonstrates that the same control-plane logic also works across an explicit service boundary.

### 3.6 Use an attested Cortical Labs path
The repository includes an adapter and client path for the **Cortical Labs CL API / CL SDK**. The adapter attests the active runtime as `sdk_simulator`, `physical_hardware`, or `unknown`; verified v3.0 results use `sdk_simulator`.

Through this path, `CP²N²` can:

- open a CL session
- submit a simple stimulation/recording task
- collect normalized result data
- capture structured recording artifact metadata
- expose session readiness, runtime kind, latency, recording-path metadata, and explicitly sourced telemetry

### 3.7 Evaluate a distributed control path

The A6 testbed isolates the agent load generator, gateway, control plane, and
adapter runtime in four operating-system processes. Versioned profiles cover
latency, jitter, request loss, partitions, and stale telemetry. Runs propagate
one trace identifier across all services and archive raw JSONL spans, request
tables, summaries, figures, exact configuration copies, and SHA-256 checksums.
The testbed is generic and uses the non-CL remote edge integration.

### 3.8 Use LLM-based agents
The repository also includes:

- a **Gemini-based agent**
- an **Ollama-based agent**

These agents can:

- discover sanitized resources and server-owned assay presets
- ask an LLM to choose only a resource, preset, and dry-run mode
- execute at most one lease-bound assay through the constrained MCP surface
- receive only a sanitized result summary
- ask the model to summarize the outcome

They cannot supply electrodes, stimulation parameters, loop counts, policy
changes, runtime claims, or lease bypasses. Real biological execution requires
an external one-time human approval that the agent cannot issue. The full A4
boundary is documented in
[Agent-Facing MCP Surface](docs/agent-facing-mcp-surface.md).

---

## 4. Repository structure

The current repository layout is:

```text
cp2n2/
  .env
  LICENSE
  README.md
  __init__.py
  requirements.txt

  adapters/
    __init__.py
    base_adapter.py
    contracts.py
    chemical_adapter.py
    cortical_labs_adapter.py
    edge_adapter.py
    fault_injecting_adapter.py
    remote_edge_adapter.py
    wetware_adapter.py

  runtimes/
    __init__.py
    base_runtime.py
    twin_runtimes.py
    remote_edge_runtime.py
    cortical_labs_runtime.py

  agent/
    __init__.py
    gemini_agent.py
    ollama_agent.py

  backends/
    cortical/
      cl_client.py

  core/
    __init__.py
    matcher.py
    orchestrator.py
    task_model.py
    twin_registry.py

  demos/
    __init__.py
    common.py
    demo_cortical_labs_adapter.py
    demo_discovery_and_matching.py
    demo_fallback_and_recalibration.py
    demo_invocation_and_telemetry.py

  descriptors/
    __init__.py
    capability_schema.py

  evaluation/
    __init__.py
    common.py
    evaluate_cortical_runtime.py
    evaluate_externalized_backend.py
    evaluate_failure_campaign.py
    evaluate_gemini_agent.py
    evaluate_matching.py
    evaluate_matching_baselines.py
    evaluate_selection_robustness.py
    evaluate_overhead.py
    evaluate_portability.py
    plots.py
    run_all_evaluations.py
    results/

  remote/
    __init__.py
    edge_service.py
    service_controller.py

  scripts/
    cl_smoketest.py
    cl_stim_record_test.py

  tests/
    conftest.py
    test_cortical_labs_adapter.py
    test_fullpaper_extensions.py

  twins/
    __init__.py
    chemical_twin.py
    edge_twin.py
    wetware_twin.py
```

The repository may also contain local cache folders such as `.pytest_cache/` or Python bytecode directories; these are not functionally relevant.

---

## 5. Installation

### 5.1 Create and activate a virtual environment

#### Windows CMD

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

#### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

#### Linux / macOS

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 5.2 Required Python packages

At minimum, the consolidated setup should include:

```txt
python-dotenv
cl-sdk
google-genai
requests
pytest
pydantic>=2,<3
mcp>=1.27,<2
```

Optional but useful:

```txt
jupyterlab
ipywidgets
```

### 5.3 Runtime configuration

Copy `.env.example` to an untracked `.env` file in the project root. Do not commit credentials. A typical starting point is:

```dotenv
# Cortical Labs SDK / Simulator
CL_SDK_DURATION_SEC=60
CL_SDK_RANDOM_SEED=42
CL_SDK_ACCELERATED_TIME=1
CL_SDK_SAMPLE_MEAN=170
CL_SDK_SPIKE_PERCENTILE=99.995

# Optional replay input
# CL_SDK_REPLAY_PATH=
# CL_SDK_REPLAY_START_OFFSET=0

# Optional visualisation / websocket support
# CL_SDK_WEBSOCKET=1
# CL_SDK_WEBSOCKET_PORT=1025
# CL_SDK_WEBSOCKET_HOST=127.0.0.1

# Gemini
GEMINI_API_KEY=YOUR_KEY_HERE

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct

# University of Lübeck AI-Lab
AI_LAB_API_KEY=YOUR_PERSONAL_KEY
AI_LAB_BASE_URL=https://llm-api.ai-lab.uni-luebeck.de
AI_LAB_MODEL=minimax-m2.7

# Constrained MCP server (fail-closed if principal or scopes are absent)
CP2N2_PRINCIPAL_ID=research-agent
CP2N2_SCOPES=resources:read,leases:write,assays:prepare,assays:execute,runs:abort
CP2N2_INCLUDE_CORTICAL_LABS=0
CP2N2_INCLUDE_BIOPATTERN_GATE_E3=0
CP2N2_AUDIT_PATH=.cp2n2/mcp-audit.jsonl
```

Important: the same Python environment that runs `CP²N²` must also have `cl-sdk` installed.

---

## 6. Basic smoke tests

### 6.1 SDK smoke test

```bash
python scripts/cl_smoketest.py
```

### 6.2 Stimulation and recording smoke test

```bash
python scripts/cl_stim_record_test.py
```

These tests verify the raw Cortical Labs path before the adapter, orchestrator, and agent layers are exercised.

---

## 7. Main demos

### 7.1 Discovery and matching demo

```bash
python -m demos.demo_discovery_and_matching
```

This demonstrates descriptor publication, backend discovery, and matcher decisions across the representative backend set.

### 7.2 Invocation and telemetry demo

```bash
python -m demos.demo_invocation_and_telemetry
```

This demonstrates execution and telemetry collection on the representative backend set.

### 7.3 Fallback and recalibration demo

```bash
python -m demos.demo_fallback_and_recalibration
```

This demonstrates recovery-oriented behavior such as fallback and recalibration handling.

### 7.4 Cortical Labs adapter demo

```bash
python -m demos.demo_cortical_labs_adapter
```

This demo exercises:

- orchestrator creation
- backend discovery
- directed task targeting `cortical-labs-backend`
- backend preparation
- invocation through the CL client
- result and telemetry collection

Typical result fields include:

- `response_fingerprint`
- `stim_channel`
- `stim_amplitude_ua`
- `observation_window_ms`
- `recording_artifact`

Typical telemetry includes:

- `readiness_state`
- `health_status` (only provider-reported; otherwise `unknown`)
- `runtime_kind`
- `telemetry_source`
- `backend_latency_ms`
- `observation_latency_ms`
- `recording_path`
- `channel_count`
- `fps`

---

## 8. Evaluation scripts

### 8.1 Run all bundled evaluations

```bash
python -m evaluation.run_all_evaluations
```

This includes the non-networked Agent-to-PNN reference campaign without E3
execution. Model-backed campaigns and the explicit E3 lifecycle remain
separate opt-in runs.

### 8.2 Cortical runtime evaluation — explicit execution gate

```bash
python -m evaluation.evaluate_cortical_runtime
```

This performs directed stimulation/recording runs. It is **not** part of the default test suite and must only be run after confirming the active runtime and obtaining the appropriate hardware-safety approval. Its results must state `runtime_kind` and may not be presented as physical-hardware evidence when the SDK Simulator is active. Evaluation outputs are written to a fresh `evaluation/results/run-<UTC timestamp>/` directory by default; set `CP2N2_RESULTS_DIR` to select another location.

### 8.3 Gemini agent evaluation

```bash
python -m evaluation.evaluate_gemini_agent
```

This calls Gemini to produce several constrained **dry-run** plans and stores
JSON/CSV results under `evaluation/results/`. It does not execute a substrate
and is not part of the default test suite.

### 8.4 Additional evaluation scripts

The repository also contains dedicated scripts for:

- `evaluation.evaluate_distributed_testbed`
- `evaluation.evaluate_ai_lab_agent` (explicit network acknowledgement required)
- `evaluation.evaluate_externalized_backend`
- `evaluation.evaluate_failure_campaign`
- `evaluation.evaluate_matching`
- `evaluation.evaluate_matching_baselines`
- `evaluation.evaluate_overhead`
- `evaluation.evaluate_portability`

The BioPattern Gate Agent-to-PNN campaign evaluates canonical, paraphrased,
ambiguous, and adversarial prompts against an independent safety oracle:

```powershell
python -m evaluation.agent_to_pnn_campaign --planner reference
```

It executes no substrate by default. Add `--execute-e3` only for the single
reviewed SDK-simulator lifecycle. AI-Lab, Gemini, and Ollama planners are
available behind `--confirm-model-access`. See
[Agent-to-PNN Evaluation Campaign](docs/agent-to-pnn-evaluation-campaign.md).

These scripts can be run individually from the project root with `python -m ...`.

### 8.5 Distributed RQ2 campaign

```bash
python -m evaluation.evaluate_distributed_testbed
```

The committed campaign uses 1, 2, 4, 8, 16, and 32 competing clients across
five versioned network/fault profiles. For a short local installation check,
add `--quick`. The complete method, archive layout, interpretation boundary,
and figure-regeneration command are documented in
`docs/distributed-testbed.md`.

### 8.6 University of Lübeck AI-Lab dry-run evaluation

```bash
python -m evaluation.evaluate_ai_lab_agent --confirm-network
```

This performs two LLM planning cases against the University AI-Lab and
consumes provider budget units. Both cases remain dry runs and execute no PNN
substrate. Results are explicitly labeled as AI-Lab inference evidence, not
PNN evidence. See `docs/ai-lab-agent.md`.

---

## 9. Agent-based access

The repository provides three minimal agent clients on top of `CP²N²`:

- **Gemini-based agent**
- **Ollama-based agent**
- **University of Lübeck AI-Lab agent**

All three agents follow the same constrained flow:

1. discover sanitized resources and compatible server-owned presets
2. ask an LLM to choose `resource_id`, `preset_id`, and `dry_run`
3. validate the plan with an extra-fields-forbidden schema
4. call only the high-level MCP service operations
5. summarize a sanitized result

The agents do not construct arbitrary `TaskRequest` objects and do not call
backend APIs such as Cortical Labs directly. `CP²N²` remains the sole
control plane. Both examples default to dry-run behavior.

### 9.1 Gemini agent

Expected location:

```text
agent/gemini_agent.py
```

Requirements:
- `google-genai`
- `python-dotenv`
- `GEMINI_API_KEY` in `.env`

Run from the project root:

```bash
python -m agent.gemini_agent
```

This agent is useful when a stronger cloud LLM is available and a Gemini API key is already configured.

### 9.2 Ollama agent

Expected location:

```text
agent/ollama_agent.py
```

Requirements:
- `requests`
- a running Ollama server
- a locally installed model, for example:
  - `qwen2.5:7b-instruct`
  - `qwen2.5:14b-instruct`

Typical setup:

```bash
ollama pull qwen2.5:7b-instruct
python -m agent.ollama_agent
```

This agent is the preferred free and local option for immediate experimentation.

### 9.3 University of Lübeck AI-Lab agent

Expected location:

```text
agent/ai_lab_agent.py
```

The client uses the provider's OpenAI-compatible LiteLLM endpoint. It pins
credentials to the official HTTPS host and uses the same strict plan schema
and constrained executor as the other agents. Configuration and the explicitly
networked dry-run evaluation are documented in `docs/ai-lab-agent.md`.

```bash
python -m agent.ai_lab_agent
```

### 9.4 Current scope

The current agent implementations are intentionally minimal. They focus on:

- resource and preset discovery
- strictly bounded planning
- dry-run admission checks or one fixed reserve–prepare–run sequence
- concise sanitized result summarization

They are operational demonstrations of **agent-facing control-plane access**, not full autonomous multi-agent systems.

### 9.5 MCP server

Run the official MCP 1.x stdio binding with:

```bash
python -m mcp_surface.server
```

It publishes exactly ten tools and is unauthenticated and unscoped by default.
Configure the server-owned principal and scopes through the variables shown
above. The optional CL adapter remains one scenario and is disabled by default
for the stdio server; enabling it does not bypass runtime attestation or human
approval.

---

## 10. Tests

Run the bundled tests with:

```bash
pytest -q
```

For the Cortical Labs adapter specifically:

```bash
pytest tests/test_cortical_labs_adapter.py -q
```

The tests validate descriptor structure, adapter behaviour, and integration assumptions. `pytest.ini` restricts default collection to `tests/`; scripts capable of stimulation are deliberately excluded.

The A4 security tests additionally validate malformed and hostile MCP calls,
server-side authorization, dry-run non-commitment, append-only audit integrity,
sanitized outputs, and external approval. They use only local simulators and
test doubles.

### 10.2 Adapter conformance

```bash
pytest tests/test_adapter_conformance.py -q
```

This validates the A5 control-adapter/runtime split for local twins, the
same-host HTTP backend, the optional CL simulator path, capability
declarations, and operation without any CL module. Core evaluations publish a
versioned backend matrix and each retains at least one non-CL backend.

### 10.3 Distributed-testbed validation

```bash
pytest tests/test_distributed_testbed.py -q
```

These tests validate the versioned topology and profile matrix, seeded fault
decisions, trace propagation through bounded worker threads, metric
aggregation, and a real four-process smoke campaign with manifest checksum
verification.

### 10.4 Resource-contract validation

```bash
python scripts/validate_resource_contract.py examples/resource-contract-v1.0/valid-chemical-synthetic-twin.json
```

The canonical Draft 2020-12 JSON Schema is stored under `schemas/`, with
cross-substrate valid, invalid, and conservatively inadmissible examples under
`examples/resource-contract-v1.0/`.

---

## 11. How the Cortical Labs integration works

The Cortical Labs path consists of two layers:

### `backends/cortical/cl_client.py`
This is the low-level client wrapper around the CL SDK. It handles:

- session open/close
- simple stimulation/recording cycles
- session readiness and runtime-kind attestation
- recording artifact normalization

### `adapters/cortical_labs_adapter.py`
This is the `CP²N²` adapter layer. It translates between:

- `CP²N²` task and telemetry semantics
- and the CL client’s concrete runtime calls

This separation keeps backend-specific API handling in the client and control-plane semantics in the adapter.

---

## 12. How the agent integrations work

### Planning
The LLM receives a user goal plus sanitized discovery data. It can choose only
a compatible server-owned preset and resource:

```json
{
  "action": "prepare_assay",
  "arguments": {
    "resource_id": "cortical-labs-backend",
    "preset_id": "cl_pattern_discrimination_v1",
    "dry_run": true
  },
  "rationale": "..."
}
```

### Execution
The shared constrained executor validates the plan and calls the MCP service.
For a dry run it performs no reservation, lifecycle change, run creation, or
adapter invocation. For explicit execution it performs one fixed
reserve–prepare–run sequence. Preset internals remain server-owned.

### Summarization
The LLM receives a sanitized result without raw recordings, raw substrate
output, or physical control parameters.

This keeps the LLM in a planning and summarization role. The approval issuer
for physical wetware is deliberately external to MCP.

---

## 13. Recommended workflow for development

Use this order:

```bash
python scripts/cl_smoketest.py
python scripts/cl_stim_record_test.py
python -m demos.demo_cortical_labs_adapter
pytest tests/test_cortical_labs_adapter.py -q
python -m evaluation.evaluate_cortical_runtime
python -m agent.gemini_agent
```

or, for the free local agent path:

```bash
python -m agent.ollama_agent
```

or, through the University AI-Lab:

```bash
python -m agent.ai_lab_agent
```

If the first two scripts fail, there is no point debugging the adapter or the agents yet.

---

## 14. Known scope and limitations

This repository is a research prototype and should be interpreted accordingly.

### What it already demonstrates
- substrate-aware backend discovery
- task matching and directed execution
- telemetry-aware control
- an externalized remote backend path
- an attested Cortical Labs integration path, currently demonstrated with the CL SDK Simulator
- working Gemini-, Ollama-, and University AI-Lab-based agents on top of
  `CP²N²`

### What it does not claim
- production readiness
- broad performance benchmarking of real wetware systems
- full digital-twin lifecycle management
- general-purpose autonomous multi-agent orchestration
- complete support for all physical substrate classes

The Cortical Labs integration should currently be understood as:
- an API-compatible E3 CL SDK Simulator path in the verified v3.0 evidence
- capable of targeting physical hardware only when that runtime is explicitly attested
- useful for research and demonstration
- still narrow in scope

---

## 15. Practical debugging advice

### If `cl-sdk` import fails
Check that you are running the command inside the correct project virtual environment.

### If the Cortical demo fails
Re-run:

```bash
python scripts/cl_smoketest.py
python scripts/cl_stim_record_test.py
```

before debugging the adapter.

### If the Gemini agent fails
Check:

- `GEMINI_API_KEY`
- `google-genai` installation
- whether `python -m agent.gemini_agent` is executed from the project root
- whether the Cortical Labs demo already works independently

### If the Ollama agent fails
Check:

- whether `ollama serve` is running
- whether the configured model is installed
- `OLLAMA_BASE_URL`
- `OLLAMA_MODEL`
- whether the Cortical Labs demo already works independently

### If the AI-Lab agent fails

Check:

- University network/VPN connectivity
- `AI_LAB_API_KEY`
- `AI_LAB_MODEL` against the current `/v1/models` response
- the remaining weekly provider budget
- that the official endpoint remains
  `https://llm-api.ai-lab.uni-luebeck.de`

---

## 16. Summary

`phys-MCP v3.0`, the pre-rename baseline of CP²N², is a unified research
prototype for treating heterogeneous physical AI resources as discoverable,
invocable, telemetry-aware backends under a common control plane.

Its current strengths are:

- coherent substrate-aware control semantics
- an attested Cortical Labs CL SDK integration path
- reproducible SDK Simulator evaluation of that path
- and minimal but functional Gemini-, Ollama-, and University AI-Lab-based
  agents on top of the same control plane

That makes the repository useful both as:

- a systems research prototype
- and a practical experimental platform for future integrations and demonstrations
