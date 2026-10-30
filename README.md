# phys-MCP v3.0

Current upstream: [fischesn/cp2n2](https://github.com/fischesn/cp2n2).
This repository preserves the measured phys-MCP baseline and Jetson evaluation;
its results must not be attributed to untested newer upstream versions.

## Embedded Experiment Results

**Results and guides are the current deliverable. Paper writing is deferred.**
Any previously published manuscript is an earlier working draft, not the current
interpretation of these experiments or an accepted publication.

- [Results index and reproduction guide](docs/embedded-feasibility/README.md)
- [October 29 operating-limits campaign: verified results](evaluation/results/operating-limits/campaign-20261029/results-summary.md)
- [Long-run, load-sweep, local-control and power run guide](docs/experiments/operating-limits/README.md)
- [Original-paper protocol and comparison](docs/embedded-feasibility/original-protocol.md)
- [CPU, memory, temperature, GPU and power report](evaluation/results/upstream-profile-20261024/profile-report.md)
- [Resource measurement protocol](docs/embedded-feasibility/upstream-resource-protocol.md)

The October 24 experiments use unmodified CP2N2 v5.1.1 in an isolated checkout.
The original local and simulator workloads ran, while the distributed campaign
remains incomplete. A separate five-minute run completed 3,000 requests at 10 Hz
but showed rising memory, CPU and latency. Raw observations, failures, plots,
tables, complete software-simulator recordings and checksum manifests are retained.
This is evidence about embedded feasibility and its limits, not Jetson superiority.

The October 29 campaign completed all 64 planned trials, including three
30-minute runs, repeated 1/10/25/50 Hz loads, 24 local-control scenarios, and five
direct/orchestrated power pairs. All trial archives verified without infrastructure
failures. Fourteen trials recorded performance-limit events such as completion
deadline misses or skipped releases; they are completed measurements, not missing
or discarded trials. The linked summary keeps these outcomes and the diagnostic
profiles separate from ordinary timing measurements.

`phys-MCP` is a substrate-aware control-plane prototype for exposing heterogeneous **physical neural network (PNN)** resources as discoverable, invocable, and monitorable software-visible backends.

The system is designed for settings in which materially different computational substrates cannot be treated as ordinary stateless accelerators. Instead, they expose distinct I/O modalities, timing regimes, lifecycle constraints, observability limits, and health or validity conditions. `phys-MCP` provides a single orchestration layer above such backends while preserving these substrate-specific semantics.

This repository contains:

- a Python reference implementation of the `phys-MCP` control plane
- representative local prototype backends for chemical, wetware, and fast edge-style execution
- an externalized remote edge backend path
- a real API-backed integration path for **Cortical Labs**
- minimal **Gemini-based** and **Ollama-based** agents that plan and execute tasks through `phys-MCP`
- demos, tests, and evaluation scripts

The current code base should be understood as a **research prototype**: it is operational, structured, and demonstrable, but not a production runtime.

The [embedded feasibility protocol](docs/embedded-feasibility/protocol.md) documents
the isolated Jetson evaluation harness, smoke campaign, measurement boundaries,
and remaining gates before publication experiments. New campaigns preserve the
existing results and do not modify control-plane behavior.

---

## 1. Purpose of the system

`phys-MCP` exists to answer a practical systems problem:

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

`phys-MCP` answers these questions through a substrate-aware descriptor model, a matcher, an orchestrator, and backend-specific adapters.

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

### Twin / runtime state
The prototype keeps state that is relevant for runtime decisions, such as:

- readiness
- health
- drift-related signals
- telemetry freshness
- calibration- or validity-like metadata

This is not a full digital twin framework, but it is enough to make runtime state visible to the control logic.

### Data / backend integration layer
The data-plane side is implemented through adapters and backend-specific client logic:

- local synthetic backends for representative substrate regimes
- a remote edge path via HTTP
- a real API-backed path via the Cortical Labs CL SDK / simulator
- a foundation for additional future integrations

---

## 3. What the prototype can do

### 3.1 Discover heterogeneous backends
The orchestrator can enumerate backends described through a shared descriptor model.

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

Matching is based on descriptor compatibility and runtime signals rather than on mere endpoint presence.

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

### 3.6 Use a real Cortical Labs path
The repository includes a real adapter and client path for the **Cortical Labs CL API / CL SDK simulator**.

Through this path, `phys-MCP` can:

- open a CL session
- submit a simple stimulation/recording task
- collect normalized result data
- capture structured recording artifact metadata
- expose readiness, health, backend latency, observation latency, and recording path as telemetry

### 3.7 Use LLM-based agents
The repository also includes:

- a **Gemini-based agent**
- an **Ollama-based agent**

These agents can:

- discover backends through `phys-MCP`
- ask an LLM to produce a structured execution plan
- execute that plan only via `phys-MCP`
- receive the result and telemetry
- ask the model to summarize the outcome

The agents do **not** call substrate APIs directly. This is intentional: `phys-MCP` remains the sole control plane.

---

## 4. Repository structure

The current repository layout is:

```text
phys-mcp/
  .env
  LICENSE
  README.md
  __init__.py
  requirements.txt

  adapters/
    __init__.py
    base_adapter.py
    chemical_adapter.py
    cortical_labs_adapter.py
    edge_adapter.py
    fault_injecting_adapter.py
    remote_edge_adapter.py
    wetware_adapter.py

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
```

Optional but useful:

```txt
jupyterlab
ipywidgets
```

### 5.3 Runtime configuration

Create a `.env` file in the project root. A typical starting point is:

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
```

Important: the same Python environment that runs `phys-MCP` must also have `cl-sdk` installed.

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
- `health_status`
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

### 8.2 Real Cortical runtime evaluation

```bash
python -m evaluation.evaluate_cortical_runtime
```

This performs several directed runs against the Cortical Labs integration path and stores JSON/CSV results under `evaluation/results/`.

### 8.3 Gemini agent evaluation

```bash
python -m evaluation.evaluate_gemini_agent
```

This performs several agent-driven runs and stores JSON/CSV results under `evaluation/results/`.

### 8.4 Additional evaluation scripts

The repository also contains dedicated scripts for:

- `evaluation.evaluate_externalized_backend`
- `evaluation.evaluate_failure_campaign`
- `evaluation.evaluate_matching`
- `evaluation.evaluate_matching_baselines`
- `evaluation.evaluate_overhead`
- `evaluation.evaluate_portability`
- `evaluation.evaluate_tegra_resources` on NVIDIA Jetson devices with `tegrastats`

These scripts can be run individually from the project root with `python -m ...`.

### 8.5 Jetson / Orin resource evaluation

On Jetson devices, `tegrastats` can be used to collect power, memory, CPU, GPU,
and temperature samples while a phys-MCP workload is running.

```bash
python -m evaluation.evaluate_tegra_resources --workload edge_control --iterations 5000 --interval-ms 250
```

The script writes JSON, CSV, raw `tegrastats` logs, and PNG bar charts under `evaluation/results/`.
This evaluation is intentionally not part of the default bundled evaluation suite,
because power and memory measurements depend on the Jetson power mode, clocks,
temperature, and background system load.

A visual PDF report can be generated from the collected artifacts with:

```bash
python -m evaluation.create_embedded_edge_report_pdf
```

---

## 9. Agent-based access

The repository provides two minimal agent clients on top of `phys-MCP`:

- **Gemini-based agent**
- **Ollama-based agent**

Both agents follow the same principle:

1. discover resources through `phys-MCP`
2. ask an LLM to produce a structured execution plan
3. execute that plan only through the `phys-MCP` orchestrator
4. summarize the result and telemetry in natural language

The agents do **not** call backend APIs such as Cortical Labs directly.  
`phys-MCP` remains the sole control plane.

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

### 9.3 Current scope

The current agent implementations are intentionally minimal. They focus on:

- backend discovery
- structured planning
- directed execution against the Cortical Labs path
- concise result summarization

They are operational demonstrations of **agent-facing control-plane access**, not full autonomous multi-agent systems.

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

The tests validate descriptor structure, adapter behavior, and integration assumptions. They complement, but do not replace, the real simulator runs.

---

## 11. How the Cortical Labs integration works

The Cortical Labs path consists of two layers:

### `backends/cortical/cl_client.py`
This is the low-level client wrapper around the CL SDK. It handles:

- session open/close
- simple stimulation/recording cycles
- health/readiness retrieval
- recording artifact normalization

### `adapters/cortical_labs_adapter.py`
This is the `phys-MCP` adapter layer. It translates between:

- `phys-MCP` task and telemetry semantics
- and the CL client’s concrete runtime calls

This separation keeps backend-specific API handling in the client and control-plane semantics in the adapter.

---

## 12. How the agent integrations work

### Planning
The LLM receives:
- a planning prompt
- a user goal

It returns structured JSON such as:

```json
{
  "action": "run_cortical_screen",
  "arguments": {
    "preferred_backend_id": "cortical-labs-backend",
    "channel": 24,
    "amplitude": 0.6,
    "observation_window_ms": 100,
    "pre_delay_ms": 10,
    "allow_fallback": false,
    "human_supervision_available": true
  },
  "rationale": "..."
}
```

### Execution
The agent converts this plan into a `phys-MCP` task and calls the orchestrator.

### Summarization
The LLM then receives the structured result and telemetry and produces a short human-readable explanation.

This keeps the LLM in a **planning and summarization role**, while all actual backend control remains in `phys-MCP`.

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

If the first two scripts fail, there is no point debugging the adapter or the agents yet.

---

## 14. Known scope and limitations

This repository is a research prototype and should be interpreted accordingly.

### What it already demonstrates
- substrate-aware backend discovery
- task matching and directed execution
- telemetry-aware control
- an externalized remote backend path
- a real API-backed Cortical Labs integration path
- working Gemini- and Ollama-based agents on top of `phys-MCP`

### What it does not claim
- production readiness
- broad performance benchmarking of real wetware systems
- full digital-twin lifecycle management
- general-purpose autonomous multi-agent orchestration
- complete support for all physical substrate classes

The Cortical Labs integration should currently be understood as:
- a real wetware-facing API path
- successfully exercised end to end
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

---

## 16. Summary

`phys-MCP v3.0` is a unified research prototype for treating heterogeneous physical AI resources as discoverable, invocable, telemetry-aware backends under a common control plane.

Its current strengths are:

- coherent substrate-aware control semantics
- a working real API-backed Cortical Labs path
- reproducible runtime evaluation of that path
- and minimal but functional Gemini- and Ollama-based agents on top of the same control plane

That makes the repository useful both as:

- a systems research prototype
- and a practical experimental platform for future integrations and demonstrations
