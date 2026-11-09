# Physical Neural Resource Contract v1.0

## Purpose and scope

The Physical Neural Resource Contract (PNRC) is the substrate-neutral
publication format used by CP²N². It describes what a resource is, what it
can do, what runtime is actually active, what is known about its current state,
and which operations may be admitted.

The contract applies equally to chemical, biological, memristive, photonic,
neuromorphic, conventional edge, and simulated resources. Cortical Labs is one
adapter and case study; no CL-specific field is part of the core contract.

The existing `SubstrateDescriptor` remains the v3.0 compatibility descriptor
and is embedded as `capabilities`. New integrations should publish the complete
contract through `BaseAdapter.resource_contract()`.

## Version

- Contract version: `1.0`
- JSON Schema dialect: JSON Schema Draft 2020-12
- Canonical schema:
  `schemas/physical-neural-resource-contract-v1.0.schema.json`

The top-level `schema_version` is required. Readers must reject unsupported
versions. An absent version is not interpreted as v1.0. Minor-version
migrations may be added only as explicit deterministic functions. A migration
must not silently invent evidence, safety limits, telemetry, or provider
declarations, and no implicit migration may cross a major version.

The current implementation deliberately has no migration path from the legacy
unversioned `SubstrateDescriptor`: it is preserved inside the new envelope
rather than rewritten with fabricated metadata.

## Top-level fields

| Field | Meaning |
|---|---|
| `identity` | Resource, provider, adapter, substrate, and hardware identities |
| `evidence` | Runtime kind, evidence level, attestation method, and time |
| `capabilities` | Existing substrate-aware I/O, task, timing, and lifecycle descriptor |
| `state` | Lifecycle, occupancy, health, calibration, and drift observations |
| `telemetry` | Named dynamic observations with provenance and freshness |
| `safety` | Explicitly permitted operations, hard limits, and supervision requirements |
| `access` | Locality, tenancy, authentication, authorization, and reservation metadata |
| `cost` | Billing model and optional cost estimate |
| `data_governance` | Storage, retention, residency, and provider terms |

`safety`, `access`, `cost`, and `data_governance` may be absent so that an
incomplete provider description remains representable and diagnosable.
Absence does not mean that no constraint exists. In particular, a missing
`safety` block makes execution inadmissible.

## Evidence levels

Runtime kind and evidence level are coupled and validated:

| Runtime kind | Level | Meaning |
|---|---:|---|
| `unknown` or `mock` | E0 | No execution evidence or mock-only behavior |
| `synthetic_twin` | E1 | In-process synthetic twin |
| `same_host_service` | E2 | A separate service on the same host |
| `sdk_simulator` | E3 | Official or provider SDK simulator |
| `remote_simulator` | E4 | Simulator across a remote service boundary |
| `physical_hardware` | E5 | Attested physical resource |

A configured expectation is not a runtime attestation. For example, the
Cortical Labs adapter remains `unknown/E0` until the CL SDK reports whether the
opened runtime is its simulator or physical hardware. It cannot publish
`physical_hardware/E3`, or any other mismatched pair.

## Dynamic observation semantics

Every dynamic field uses the same `TelemetryObservation` structure and always
contains these keys:

| Key | Rule |
|---|---|
| `value` | May be `null` when the value is unknown; zero is never an unknown sentinel |
| `unit` | Explicit unit, or `null` if no unit is known; `null` does not imply dimensionless |
| `source` | Exactly one of `observed`, `provider_reported`, `estimated`, or `configured` |
| `observed_at` | Time the value was observed; may be `null` only for configured values |
| `received_at` | Time CP²N² received or constructed the observation |
| `uncertainty` | Explicit uncertainty kind; unknown uncertainty is not encoded as zero |
| `valid_until` | Optional validity horizon; an expired value is stale |

All timestamps must carry a timezone. Live adapter publications use UTC and a
short validity horizon. Synthetic twins use `estimated`; SDK measurements use
`observed`; remote provider values use `provider_reported`; static policy
values use `configured`.

## Conservative admission

`assess_contract_admission()` evaluates a structurally valid contract for one
operation. It returns a decision and all reasons, rather than silently filling
gaps.

Invocation is inadmissible when, among other cases:

- the runtime kind is not attested;
- the operation is not explicitly permitted;
- the safety contract is missing;
- supervision or exclusive-access requirements are unknown;
- lifecycle or health is missing, stale, or incompatible with execution;
- E5 lacks a hardware identity, hard limits, emergency-stop declaration, or
  operator-acknowledgement declaration;
- E5 health is unknown.

After an adapter prepares a task, the orchestrator republishes its contract and
performs this admission check before calling `invoke()`. A failed check returns
an `INADMISSIBLE` result and the backend is not invoked. Detailed lifecycle
leases and policy rules are addressed in later master-plan steps.

## Validation and examples

Validate an example from the repository root:

```bash
python scripts/validate_resource_contract.py \
  examples/resource-contract-v1.0/valid-chemical-synthetic-twin.json
```

The validator uses exit code `0` for valid and admissible, `2` for structurally
invalid, and `3` for structurally valid but inadmissible. Use
`--allow-inadmissible` only for inspection workflows.

Checked-in examples cover:

- valid chemical and edge E1 synthetic twins;
- a valid CL SDK Simulator E3 resource;
- an E5 wetware resource that is structurally valid but inadmissible because
  hardware identity and safety information are missing;
- a structurally invalid chemical resource with unsupported telemetry
  provenance.

Regenerate the schema and examples with:

```bash
python scripts/export_resource_contract_artifacts.py
```

The conformance suite is part of `pytest`.
