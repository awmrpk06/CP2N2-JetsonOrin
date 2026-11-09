# General Adapter Architecture (A5)

## Goal

A5 separates provider- and resource-facing control from time-critical
substrate execution. The architecture is substrate-neutral: chemical twins,
wetware twins, edge runtimes, service-backed resources, SDK simulators, and
future physical systems use the same composition boundary.

Each integration has two objects:

1. a **control adapter**, which publishes discovery information, resource and
   capability contracts, reservation/deployment semantics, state, abort
   behavior, and artifact inventory; and
2. a **substrate runtime**, which prepares and executes a task and returns
   runtime telemetry from the environment in which time-critical work occurs.

The orchestrator continues to interact with `BaseAdapter`, preserving the
A1–A4 API. `BaseAdapter` now delegates execution to a separate
`SubstrateRuntime`.

## Versioned declarations

Every registered adapter must publish an
`AdapterCapabilityDeclaration` with schema version `1.0`. It contains:

- stable adapter, backend, substrate, and runtime identifiers;
- all control operations;
- reservation and deployment modes;
- an evidence ceiling;
- runtime location and runtime kind;
- supported runtime operations;
- artifact kinds;
- whether time-critical execution is local to the runtime; and
- whether the provider exposes meaningful abort behavior.

The declaration is not runtime evidence. For example, the CL simulator
declaration has an E3 ceiling, but its Physical Neural Resource Contract
remains E0/unknown until the SDK attests the active runtime. Evidence is never
promoted from configuration alone.

Registration calls `validate_conformance()` and rejects a declaration whose
backend, substrate class, or embedded runtime does not match the actual
adapter composition.

## Control-adapter interface

The control-plane interface provides:

- `describe()` and `resource_contract()` for discovery;
- `capability_declaration` for the A5 topology and capability contract;
- control-plane leases through the existing registry and orchestrator;
- `deployment_status()` with an explicit deployment mode;
- `collect_telemetry()` for normalized state;
- `abort()` and `abort_supported()` with conservative semantics;
- `list_artifacts()` for normalized runtime artifacts; and
- the compatibility methods `prepare()`, `invoke()`, `reset()`, and
  `recalibrate()`, which delegate to the bound runtime.

The current prototypes are predeployed, provider-managed, or controlled by the
existing lease store. A declaration does not imply an unimplemented provider
deployment or reservation API.

## Substrate-runtime interface

`SubstrateRuntime` requires:

- `prepare(task)`;
- `execute(task)`;
- `telemetry()`;
- `reset(mode)`; and
- `recalibrate()`.

It provides conservative defaults for abort and artifact enumeration. A
runtime may advertise abort only when it has a meaningful provider or process
operation.

Application-specific task translation belongs in the runtime. The control
adapter does not perform the edge vector inference, chemical ODE execution,
wetware-twin stimulation/observation cycle, HTTP invocation, or CL SDK
stimulation/recording sequence.

## Current integration matrix

| Control adapter | Substrate runtime | Location | Evidence ceiling | Deployment | Artifacts |
| --- | --- | --- | --- | --- | --- |
| `ChemicalAdapter` | `ChemicalTwinRuntime` | in process | E1 | predeployed | none |
| `WetwareAdapter` | `WetwareTwinRuntime` | in process | E1 | predeployed | none |
| `EdgeAdapter` | `EdgeTwinRuntime` | in process | E1 | predeployed | none |
| `RemoteEdgeAdapter` | `RemoteEdgeRuntime` | same-host HTTP service | E2 | predeployed | none |
| `CorticalLabsAdapter` | `CorticalLabsRuntime` | local SDK application | E3 for simulator configuration | provider-managed | HDF5 recording reference |

The CL integration is optional and loaded lazily. Generic adapter and runtime
packages, the default orchestrator, contracts, lifecycle management, and
non-CL evaluations import and execute when all modules whose names contain
`cortical` are blocked.

`use_simulator=False` remains only a guarded runtime expectation inherited
from the prototype. It is not a completed or validated real-provider control
adapter. Provider deployment, reservation, safety, and artifact APIs must not
be claimed or implemented until current access details are confirmed.

## Conformance suite

`tests/test_adapter_conformance.py` checks:

- a separate runtime object for every adapter;
- complete versioned declarations;
- descriptor/declaration/runtime identity alignment;
- required control and runtime operations;
- reservation, deployment, location, and evidence semantics;
- registry rejection of a misaligned declaration;
- the externalized edge adapter through a same-host HTTP runtime;
- successful generic core execution while CL imports are blocked; and
- explicit non-CL coverage for every core evaluation.

The CL tests use a fake SDK module or unavailable-SDK path. The default suite
does not invoke CL hardware.

## Adding another substrate

1. Implement a `SubstrateRuntime`.
2. Build the substrate-neutral descriptor and A1 resource contract inputs.
3. Bind both through a small `BaseAdapter` subclass.
4. Publish an explicit declaration with a conservative evidence ceiling.
5. Pass the shared conformance suite.
6. Add at least one non-provider-specific evaluation case.
7. Add provider-specific execution only after access, safety, and data terms
   are documented.

This sequence keeps provider code outside the generic control plane and makes
the adapter removable without invalidating the architecture.
