# Distributed testbed and RQ2 reproducibility

## Scope

The A6 testbed evaluates the generic CP²N² control plane across explicit
process boundaries. It does not require Cortical Labs software or hardware and
does not make biological or physical-hardware claims.

The versioned `local-four-process-v1` topology contains:

1. an agent-side campaign runner;
2. an HTTP gateway;
3. the lifecycle-, lease-, and admission-aware control plane;
4. an HTTP adapter service wrapping the edge synthetic runtime.

The gateway, control plane, and adapter service are launched as independent
operating-system processes. The campaign runner is a fourth process. Dynamic
localhost ports make unattended local runs collision-resistant. The same
service modules expose `--host` and `--port`, allowing them to be placed on
separate hosts by an external deployment system without changing the protocol.

## Versioned inputs

All campaign inputs are under `deployments/`:

- `local-four-process-v1.json` describes the service topology and dependencies;
- `campaigns/rq2-v1.json` fixes the client-count matrix, repetitions, profile
  order, timeout, and seed;
- `network-profiles/*.json` fix latency, jitter, loss, partition, and telemetry
  staleness conditions.

The committed RQ2 matrix uses 1, 2, 4, 8, 16, and 32 competing clients. Every
client has a distinct ownership and idempotency scope while all clients target
the same exclusive resource. Contention is therefore visible as typed
`RESOURCE_BUSY` outcomes rather than hidden retries.

Faults are injected at the application layer so the campaign is portable
without administrator privileges or platform-specific traffic-control tools.
Latency and jitter are seeded per trace. Loss is a seeded decision. The
partition profile deterministically rejects every eighth gateway-to-control
request after a configured delay. The stale-telemetry profile reports a
five-second age against a one-second task bound.

## Trace and metric model

The agent generates one stable trace identifier per matrix cell and request.
`X-CP2N2-Trace-ID` and `X-CP2N2-Parent-Span-ID` propagate it through the
gateway, control plane, timeout worker threads, and adapter HTTP calls. Each
process writes append-only JSONL spans containing:

- service, operation, trace ID, span ID, and parent span ID;
- UTC start and end timestamps and monotonic duration;
- profile identifier and fault decision;
- status, HTTP status, and sanitized error classification.

The raw request table records end-to-end latency, success, typed control error,
transport error, concurrency, and repetition. The derived table adds success
rate, mean and percentile latency, latency jitter, resource contention,
transport errors, and throughput.

## Running the campaign

From the repository root, with the project environment active:

```bash
python -m evaluation.evaluate_distributed_testbed
```

A fast installation check is available:

```bash
python -m evaluation.evaluate_distributed_testbed --quick
```

`--quick` runs only the baseline profile with one and two clients. It is a
smoke test, not an RQ2 result.

Use `--output-dir PATH` to choose an explicit archive location. Otherwise the
campaign is written below the current timestamped evaluation result directory.
The selected directory must be new or empty; the runner refuses to append to
an existing archive.

## Archived evidence

Each completed campaign directory contains:

- exact copies of the campaign, deployment, and network-profile inputs;
- per-service logs;
- per-service raw JSONL spans;
- `raw-requests.jsonl` and `raw-requests.csv`;
- `summary.csv`;
- the complete RQ2 figure set under `figures/`;
- `manifest.json` with the Git commit, explicit clean/dirty worktree state,
  Python/platform provenance, SHA-256 hashes of the A6 source files, and a
  checksum of every archived artifact.

The manifest is created last. A campaign archive is not complete unless every
listed digest verifies.

## Figure regeneration

The two RQ2 figures are generated only from `summary.csv`:

```bash
python -m evaluation.regenerate_rq2_figures \
  PATH/summary.csv --output-dir PATH/figures
```

This regenerates:

- `rq2-p95-latency.png`;
- `rq2-success-rate.png`.

The full campaign calls the same function automatically. Thus the plotted
values are traceable from figure to summary, raw request, service spans,
versioned configuration, source commit, and checksums.

## Interpretation boundary

The testbed measures orchestration and distributed-control behavior over
localhost process boundaries plus controlled, reproducible impairments. It is
not a measurement of a wide-area production network. Any paper figure must
state the deployment identifier, profile, client count, repetitions, and this
application-layer fault-injection method.
