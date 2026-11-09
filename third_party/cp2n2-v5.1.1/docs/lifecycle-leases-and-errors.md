# Lifecycle, Leases, and Error Semantics

## Scope

A2 turns the CP²N² invocation path into an explicit execution protocol.
Every state-changing request is correlated, every execution owns a
time-bounded exclusive lease, and every lifecycle transition carries an
optimistic-concurrency version.

These semantics are substrate-neutral. They apply to synthetic twins,
same-host and remote services, SDK simulators, and physical PNNs. The Cortical
Labs adapter is one consumer of the protocol, not a special control plane.

## Lifecycle

The normative successful path is:

```text
DISCOVERED -> READY -> RESERVED -> PREPARING -> RUNNING
          -> VALIDATING -> COOLDOWN -> READY
```

Failure and uncertainty use:

```text
active state -> ABORTING -> READY | DEGRADED | FAILED | UNREACHABLE
READY/RUNNING -> UNREACHABLE
UNREACHABLE -> READY only through explicit provider reconciliation
```

`LifecycleStore` serializes transitions under a lock. Each accepted transition
increments `version` and records the time, reason, and correlation ID.
A caller may provide an expected version. A mismatch returns
`STATE_VERSION_CONFLICT`; an illegal edge returns
`INVALID_STATE_TRANSITION`.

The registry overlays the control-plane lifecycle and state version on the
published Physical Neural Resource Contract. Provider-reported readiness and
control-plane ownership therefore remain distinguishable.

## Lease semantics

`InMemoryLeaseStore` provides atomic exclusive leases:

- one active lease per resource;
- explicit owner identity;
- acquisition and expiry timestamps;
- time-to-live in milliseconds;
- lease version for optimistic renewal;
- ownership checks for validation, renewal, release, and abort;
- idempotent acquisition only for the same owner and idempotency key.

The orchestrator supports two modes:

1. `execute_task()` acquires and releases a lease automatically.
2. A client calls `reserve_backend()`, optionally `renew_lease()`, and then
   supplies the lease in a directed `TaskRequest`.

An unused `RESERVED` lease can be released with `release_lease()`. Once
preparation has started, a client must use `abort_backend()` rather than merely
dropping ownership.

Expired leases are never accepted. If a client disappears after moving the
resource into an active state, a later client cannot assume the resource is
ready merely because the lease expired. The resource must be reconciled with
the provider.

### Current implementation boundary

The A2 lease store is atomic across threads and across orchestrators that share
one `TwinRegistry`. It is an in-process reference implementation, not a
distributed consensus service. A production multi-process or multi-node
deployment must replace it with a transactional external store while
preserving the same interface and version semantics.

## Idempotency and correlation

`TaskRequest` accepts:

- `client_id`;
- `correlation_id`;
- `idempotency_key`;
- expected resource and lease versions.

If no correlation ID is supplied, CP²N² creates one. The ID is attached to
all lifecycle transitions for the run.

Idempotency keys are scoped by client. Repeating the same completed request
returns the recorded result and does not invoke the substrate again. Reusing a
key for a different request returns `IDEMPOTENCY_CONFLICT`. A concurrent repeat
returns `REQUEST_IN_PROGRESS`. Retryable failures that occur before a lease is
acquired do not permanently consume the key.

Like the lease store, the current idempotency store is in-process. A
distributed deployment requires durable shared storage.

## Phase timeouts

Timeouts are independent:

| Phase | Task field | Timeout consequence |
|---|---|---|
| Preparation | `preparation_timeout_ms` | `PREPARATION_TIMEOUT`; abort and reconcile |
| Invocation | `invocation_timeout_ms` | `EXECUTION_STATUS_UNKNOWN`; never reported as success |
| Validation/admission | `validation_timeout_ms` | `VALIDATION_TIMEOUT` or `TELEMETRY_STALE` |
| Cooldown/recovery | `cooldown_timeout_ms` | uncertain status; abort and reconcile |
| Abort | `abort_timeout_ms` | provider remains uncertain and resource becomes `UNREACHABLE` |

Python cannot safely terminate an arbitrary provider call. The timeout helper
therefore stops waiting but does not claim cancellation. A timed-out worker may
finish later; its late return cannot modify the already returned orchestration
result. Unless the provider confirms abort, the control plane records
`UNREACHABLE`, releases the expired execution ownership, and requires explicit
reconciliation.

## Abort and reconciliation

`BaseAdapter.abort()` is conservative and returns `False` unless an adapter has
a meaningful provider operation. `abort_supported()` controls whether `abort`
is advertised in the resource contract. The CL adapter implements abort as a
session close without issuing further stimulation.

`abort_backend()` requires the correct lease owner. It transitions the resource
to `ABORTING`, invokes the provider abort with a deadline, reads provider state,
and settles in `READY`, `DEGRADED`, `FAILED`, or `UNREACHABLE`.

`reconcile_backend()` is an explicit read-and-compare operation after
uncertainty. It refuses to override an active lease. Provider telemetry is
mapped conservatively:

- ready/healthy -> `READY`;
- degraded/warning -> `DEGRADED`;
- offline/unavailable -> `UNREACHABLE`;
- no conclusive state after active uncertainty -> `FAILED`.

## Error codes

The stable error envelope contains `code`, `message`, `retryable`, and
structured `details`. A2 defines:

```text
INADMISSIBLE
POLICY_DENIED
RESOURCE_NOT_FOUND
RESOURCE_BUSY
LEASE_EXPIRED
LEASE_NOT_FOUND
STATE_VERSION_CONFLICT
INVALID_STATE_TRANSITION
TELEMETRY_STALE
PREPARATION_FAILED
PREPARATION_TIMEOUT
INVOCATION_FAILED
EXECUTION_STATUS_UNKNOWN
VALIDATION_TIMEOUT
POSTCONDITION_FAILED
ABORT_FAILED
RECONCILIATION_FAILED
IDEMPOTENCY_CONFLICT
REQUEST_IN_PROGRESS
```

`failure_reason` retains a concise `CODE: message` form for existing clients.
New clients should inspect the typed `error` field.

## Conformance evidence

The safe test suite covers:

- every declared lifecycle edge and invalid transition rejection;
- stale state-version conflicts;
- eight concurrent clients competing for one lease;
- two orchestrators sharing a registry and attempting concurrent execution;
- lease expiry, renewal, ownership, and explicit release;
- idempotent replay and key conflicts;
- successful lifecycle history;
- preparation, invocation, and validation timeouts;
- explicit abort;
- provider reconciliation after timeout uncertainty;
- control-plane state publication in the resource contract.

No CL stimulation, recording, or physical-hardware operation is part of these
tests.
