# Agent-Facing MCP Surface (A4)

## Purpose and scope

The A4 surface is the only supported boundary between an LLM agent and
`CP²N²`. It exposes resource discovery, leases, fixed assay presets, run
lifecycle operations, and sanitized results. It does not expose substrate
drivers or arbitrary physical controls.

The surface is substrate-neutral. Cortical Labs is one scenario represented by
one server-owned preset and one optional adapter. The generic chemical,
wetware, and edge resources remain first-class resources, and the Cortical Labs
SDK Simulator remains available as a development backend.

The implementation targets the stable MCP Python SDK 1.x line
(`mcp>=1.27,<2`).

## Public tools

The MCP server publishes exactly these ten tools:

| Tool | Required server-side scope | Purpose |
| --- | --- | --- |
| `discover_resources` | `resources:read` | List sanitized resources and compatible presets. |
| `describe_resource` | `resources:read` | Describe one resource without low-level controls. |
| `reserve_resource` | `leases:write` | Acquire a bounded exclusive lease. |
| `renew_lease` | `leases:write` | Renew an owned lease within the TTL bound. |
| `prepare_assay` | `assays:prepare` | Validate a fixed preset or create a lease-bound run. |
| `run_assay` | `assays:execute` | Execute one previously prepared run. |
| `get_run_status` | `resources:read` | Read an owned run's state. |
| `abort_run` | `runs:abort` | Abort an owned prepared or running run. |
| `get_result_summary` | `resources:read` | Read a sanitized terminal summary. |
| `release_resource` | `leases:write` | Release an unused owned lease. |

The server supplies the authenticated principal and its scopes. Principal,
scope, policy, and runtime evidence are not tool arguments and therefore
cannot be replaced by an agent.

All request models reject unknown fields. Identifiers and UUIDs are validated,
lease TTLs are limited to 30–600 seconds, and non-dry preparation requires an
existing lease. MCP tool annotations identify read-only, destructive, and
idempotent behavior and set `openWorldHint=false`.

## Server-owned assay presets

Agents may choose only a resource and a compatible preset:

- `edge_vector_classification_v1`
- `chemical_sensing_v1`
- `wetware_temporal_probe_v1`
- `cl_pattern_discrimination_v1`

The catalog is owned by the server. A preset may contain bounded internal
parameters needed by an adapter, but those parameters do not appear in MCP
request schemas or discovery responses. The CL preset is restricted to the CL
backend; it does not specialize the architecture as a whole.

An agent cannot submit or modify:

- electrode or channel selections;
- amplitude, pulse, timing, or observation parameters;
- arbitrary task metadata;
- iteration or unlimited-loop counts;
- fallback or selection policies;
- runtime-kind or evidence claims;
- raw reset, recalibration, stimulation, or backend invocation;
- leases owned by another principal; or
- an approval issuance operation.

The demonstration agents additionally validate the LLM plan against a strict
three-field choice: `resource_id`, `preset_id`, and `dry_run`. Their executor
performs at most one fixed reserve–prepare–run sequence.

## Dry-run guarantee

`prepare_assay` with `dry_run=true` performs admission and feasibility
planning only. It:

- accepts no lease;
- creates no lease;
- creates no run record;
- changes no resource lifecycle state; and
- invokes no adapter.

The response explicitly reports `resource_committed=false` and
`run_created=false`. This is the default mode in both example agents and in the
Gemini evaluation.

## Human approval for real biological execution

Real biological execution is defined conservatively as a resource that is both
wetware and attested as `physical_hardware`. Such a prepared run cannot execute
without a valid external approval.

Approval issuance is intentionally outside MCP. The included authority is a
reference integration hook for an operator-facing approval system. A grant is:

- bound to the exact run, resource, preset, and principal;
- short-lived;
- single-use; and
- consumed immediately when verified.

The default verifier denies all approval-protected execution. SDK simulators
and synthetic twins are not represented as physical-hardware evidence, but
their actual runtime kind remains visible in discovery and result summaries.

## Audit trail

Every received agent request generates a `pending` event before tool lookup,
schema validation, or authorization. A second event records success or denial.
This includes unknown tools, malformed payloads, unauthorized requests, and
approval failures.

Events are stored as append-only JSON Lines with timestamps, request and
principal identifiers, tool name, outcome, and a SHA-256 hash chain. The chain
is verified when the trail is opened and can be checked again with
`verify()`. Fields whose names indicate tokens, secrets, or keys are redacted;
a digest is retained for correlation without storing the secret.

The chain detects modification, reordering, and internal gaps. Detecting
deletion of a valid terminal suffix requires an independently retained
checkpoint. Production deployment would additionally require durable remote
storage, key-backed signing, rotation, checkpointing, and independent
retention controls.

## Running the MCP server

The stdio server is fail-closed unless a server operator supplies a principal
and scopes:

```dotenv
CP2N2_PRINCIPAL_ID=research-agent
CP2N2_SCOPES=resources:read,leases:write,assays:prepare,assays:execute,runs:abort
CP2N2_INCLUDE_CORTICAL_LABS=0
CP2N2_INCLUDE_BIOPATTERN_GATE_E3=0
CP2N2_AUDIT_PATH=.cp2n2/mcp-audit.jsonl
```

Start it from the project root:

```bash
python -m mcp_surface.server
```

Set `CP2N2_INCLUDE_CORTICAL_LABS=1` to register the optional generic CL
adapter. This does not authorize physical execution and does not change the
approval policy. Runtime evidence still distinguishes the SDK Simulator from
physical hardware.

Set `CP2N2_INCLUDE_BIOPATTERN_GATE_E3=1` to register the frozen, deterministic
BioPattern Gate E3 application adapter. It is labeled as SDK-simulator
evidence and cannot support a biological claim.

## Security properties and tests

`tests/test_mcp_surface.py` checks:

- the exact ten-tool protocol surface;
- absence of unsafe fields in every MCP input schema;
- rejection and auditing of unknown or malformed calls;
- rejection of hostile plans containing physical controls, policy edits,
  runtime edits, approval tokens, or loop counts;
- server-side scope enforcement;
- dry-run non-commitment;
- lease-bound preparation and sanitized results;
- abort and release behavior;
- token redaction and audit-chain integrity; and
- fail-closed, single-use external approval for E5 wetware execution.

The physical-approval test uses a local test double and invokes no hardware.
The default suite does not call Gemini, Ollama, Cortical Labs hardware, or any
external service.

## Prototype limitations

Run records and approval grants are currently in-process. Execution is
synchronous, so cross-process recovery and distributed cancellation are not
yet provided. Authentication is injected by server configuration rather than a
production identity provider. These limitations are explicit prototype
boundaries; none expands the authority of the agent-facing schema.
