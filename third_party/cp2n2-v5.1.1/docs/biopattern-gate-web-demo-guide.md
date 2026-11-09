# BioPattern Gate Web Demo: User Guide

## Purpose of the web demo

The web demo explains what the BioPattern Gate application does and how CP2N2
controlled its execution.

It presents two connected layers:

1. **PNN application:** A simulated neural system processes temporal input
   patterns, and a fixed decoder derives a decision from its response.
2. **Control plane:** CP2N2 selects and reserves the resource, prepares and
   starts the run, validates the result, and releases the resource.

The page is a **replay of an already completed run**. Viewing the page does not
start a PNN and does not issue new MCP requests.

The control-plane information is nevertheless not illustrative sample data.
It comes from an E3 run that was actually executed through the constrained
CP2N2 MCP surface. Its audit log is hash-chained and verified before the demo
bundle is generated.

## Starting the demo

Start the demo from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\serve_biopattern_gate_demo.py
```

The page then opens at `http://127.0.0.1:8765/`.

It can also be started without activating the virtual environment:

```powershell
.\.venv\Scripts\python.exe scripts\serve_biopattern_gate_demo.py
```

## Labels in the header

The colored labels in the upper-right corner state how the displayed evidence
must be interpreted.

### E3

`E3` identifies a technical integration result produced with the Cortical Labs
SDK simulator. The software pipeline is executed, but the neural events do not
come from biological neurons.

### Replay

`REPLAY` means that the page presents a recorded run. The replay neither
changes nor repeats the original execution.

### Complete

`COMPLETE` indicates that the recorded application run finished successfully.

### MCP Audited

`MCP AUDITED` means that the run was actually performed through the constrained
MCP interface of the CP2N2 control plane and was recorded in its audit trail.

### SDK simulator · no biological claim

This label makes clear that the demo provides no evidence about the performance
of a biological PNN. Its accuracy is a technical pipeline assertion, not a
biological research result.

## Area 1: Gate — the PNN application's task

The large area on the left presents the BioPattern Gate task itself.

A temporal signal pattern is presented to the simulated neural system. The
system must distinguish whether the pattern belongs to class A or class B:

- class A is assigned to the left output;
- class B is assigned to the right output.

The colored token represents the current input pattern. After the decoder has
committed its decision, the token moves to the selected output.

Three facts are shown below the diagram:

- **Input revealed:** The pattern that was actually presented.
- **Committed route:** The output selected by the system.
- **Outcome:** Whether the decision was correct, incorrect, or a control trial.

The correct class is revealed only after the decision has been committed. The
decoder therefore cannot adjust its choice retrospectively to the correct
answer.

### Sham control trials

`SHAM` identifies a control trial without a regular input pattern. These trials
test whether the pipeline retains defined and traceable behavior in the
absence of a classification signal. They are not counted as correct or
incorrect classifications.

## Area 2: Neural View — simulated neural activity

The upper-right area shows the temporal activity produced by the simulator.

The vertical cyan lines represent individual simulated neural events. Their
horizontal position indicates when each event occurred within the observation
window.

Below the raster, the events are grouped into several time intervals called
`bins`. The bars and numbers show how many events occurred in each interval.

In simplified terms, this area answers:

> What temporal response did the simulated neural system produce for the input
> pattern?

The decoder operates on the resulting numeric features. The visualization
exists to make those inputs understandable to a human observer.

## Area 3: Decision — the decoder's result

The middle-right area shows how the fixed decoder evaluates the recorded
activity.

`P(A)` states how strongly the observation supports class A:

- a value above `0.5` produces class A;
- a value below `0.5` produces class B.

The panel also displays:

- **Predicted class:** The class selected by the decoder.
- **Decision threshold:** The immutable decision threshold.
- **Commit:** A cryptographic fingerprint of the committed trial decision.
- **Cumulative score:** Correct decisions relative to scored trials so far.

The `LOCKED` label means that the decoder cannot be changed during the run.
Neither the user nor the agent can modify its decision rule or coefficients.

## Area 4: Control Plane — governed execution

The large lower area presents the work of the CP2N2 control plane. It answers:

> Was the PNN application executed in a controlled, complete, and traceable
> manner?

### Chain Verified

`CHAIN VERIFIED` means that the hash chain of the recorded audit log passed
verification. Changing, removing, or inserting a recorded event would cause
that verification to fail.

### Resource Lifecycle

The horizontal lifecycle shows the recorded states of the selected resource:

1. **Discovered:** The control plane found the resource.
2. **Reserved:** The resource was reserved exclusively for this run.
3. **Preparing:** The application and fixed preset were prepared.
4. **Running:** The application was executed.
5. **Validating:** The result was checked against its required conditions.
6. **Cooldown:** The resource was returned to a safe post-run state.
7. **Ready:** The resource was released and became available again.

### MCP Request Evidence

The boxes below the lifecycle show the MCP operations that were actually
performed:

- discover available resources;
- check the proposed run without committing a resource;
- reserve the selected resource;
- prepare the application;
- read the prepared status;
- start the application;
- read the terminal status;
- retrieve the sanitized result;
- confirm automatic release.

A green check mark means that the step succeeded. The shortened hash below an
MCP call refers to its corresponding event in the verified audit chain.

### Technical evidence

The lower part of the control-plane panel displays additional evidence:

- **Resource:** The resource selected by CP2N2.
- **Preset:** The server-owned application preset.
- **Config:** The hash of the immutable application configuration.
- **Decoder:** The hash of the decoder used for the run.
- **Lease:** Whether the exclusive reservation was released.
- **Audit chain:** The result of audit-log verification.
- **Audit events:** The number of recorded audit events.
- **Audit head:** The hash of the final event in the audit chain.
- **Result artifact:** The hash of the sanitized result artifact.
- **Application source:** The hash of the application code that was executed.
- **Raw agent output:** Whether raw neural data crossed the agent boundary.

`Raw agent output: blocked` confirms that the agent did not receive raw neural
events. It received only the sanitized result summary released by the control
plane.

## Replay controls

The controls at the bottom affect only the presentation:

- left arrow: previous trial;
- `Play replay`: automatic playback;
- right arrow: next trial;
- `Speed`: playback speed.

They do not initiate a new execution. They only select which part of the
recorded run is currently displayed.

## How the four areas fit together

The page can be read from top to bottom:

1. The simulated PNN responds to a temporal pattern.
2. Its response is converted into a small set of features.
3. The frozen decoder makes a traceable decision.
4. CP2N2 proves how resource selection, reservation, execution, validation,
   and release were governed.

The upper three areas primarily explain the purpose of the BioPattern Gate
application. The lower area explains and substantiates the main CP2N2 research
contribution: controlled agent access to a specialized simulated or physical
neural resource through MCP.

## What the web page does not do

The web demo is not a control interface and is not required for the actual
agent workflow. In particular:

- it does not select a resource;
- it does not reserve or start a PNN;
- it does not send MCP requests during playback;
- it does not modify the configuration or decoder;
- it does not generate new scientific results.

The actual execution path works independently of the web page:

```text
User prompt
    → LLM agent
    → MCP
    → CP2N2 control plane
    → resource selection and reservation
    → PNN application
    → validated and sanitized result summary
    → agent response
```

The web page is the presentation and observation layer for this otherwise
largely invisible technical process.
