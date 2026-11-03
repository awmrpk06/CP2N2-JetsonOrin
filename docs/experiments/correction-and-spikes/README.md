# Lifecycle-history correction and latency-spike attribution

Follow-up to the [operating-limits campaign](../operating-limits/README.md). The earlier campaign left two
things open: no correction was tested, and the latency spikes were not explained. This experiment
addresses both on the same Jetson, the same pinned CP2N2 v5.1.1 checkout (`36fbae0`) and the same
orchestrated 10 Hz edge workload. The pinned checkout is never edited. Every correction is an
evaluation-layer change applied in-process and labelled as an extension, not as an original-release
result.

## Hypotheses from the earlier archives (before any new run)

Offline reanalysis of the three unchanged 30-minute long-baseline runs
([prior-long-baseline-spikes.json](../../../evaluation/results/correction-and-spikes/prior-long-baseline-spikes.json)):

- **Growth.** Median orchestrated invocation rises steadily, by about 2.2-2.3 ms per 1,000 requests
  (about 10 ms to about 46 ms over 30 minutes). `CP2N2Orchestrator._history` calls
  `LifecycleStore.history(correlation_id=...)` for every request. That call scans the whole retained
  history, which grows by six transitions per request. Hypothesis H1: this linear scan causes the growth.
- **Spikes.** The four largest cycles (about 106, 150, 191 and 235 ms) fall at the same completed-request
  positions in all three repetitions (about 3,021, 6,838, 10,655 and 14,844), and they grow with run
  length. Earlier intrusive diagnostic runs logged one generation-2 garbage collection of about 90 ms near
  request 2,900. Hypothesis H2: these spikes are full (generation-2) CPython collections, whose cost
  grows with the retained objects. The relation between the uninstrumented 30-minute runs' skipped
  releases and individual spikes was not established; the instrumented correction runs test the latency
  and deadline relationship directly.

## Arms

| Variant | What changes | Semantics |
| --- | --- | --- |
| `pinned` | nothing | original v5.1.1 store |
| `indexed` | per-correlation index for `history(correlation_id=...)` | every transition is retained and every query result is identical |
| `bounded` | index plus in-memory retention of only the newest 6,000 transitions (about 1,000 requests) | **changed**: older transitions are evicted and can no longer be queried |

Implementation: [evaluation/lifecycle_variants.py](../../../evaluation/lifecycle_variants.py). The pinned
upstream suite was run once per variant through a pytest plugin, without editing the checkout
([variant-suite-check-20261031.json](../../../evaluation/results/correction-and-spikes/variant-suite-check-20261031.json)).
`pinned`, `indexed` and `bounded`/6000 each pass all 146 tests. A positive control with a retention
limit of 1 fails 2 history-dependent tests, which shows that the plugin really changes the store under
test. Passing the suite does not prove that bounded retention is acceptable for audits. That is a policy
decision.

## Spike instrumentation

`--spike-instrumentation` adds these columns to each `requests.csv` row, measured over a window that
wraps each request cycle: `gc_collections`, `gc_max_generation`, `gc_ms` (from `gc.callbacks`),
`process_cpu_ms`, `off_cpu_ms` (window minus process CPU time), voluntary/involuntary context switches,
and minor/major page faults. Every collection is also written to `workload/gc-events.jsonl`. The values
are process-wide because the pinned orchestrator runs adapter phases in short-lived worker threads.
Bookkeeping happens outside the timed cycle, and its cost is recorded in the worker summary
(`spike_recorder.row_overhead_*`). Compare the `pinned` arm with the uninstrumented long baselines before
treating instrumented timings as equivalent.

[evaluation/spike_analysis.py](../../../evaluation/spike_analysis.py) defines a spike as a cycle at least
10 ms above its 30-second block median (counts at 5 and 20 ms are also reported), plus every deadline
miss. Each spike's excess is split into GC, off-CPU and non-GC on-CPU components, each relative to the
block medians. The cause is the component that covers at least half of the excess. Otherwise the spike is
`mixed`, or `deadline_only` when the cycle was not long. Late dispatches and skipped releases are
attributed to an over-period predecessor cycle when one exists.

## Results: History Correction and Spike Attribution

The first follow-up campaign completed all 9/9 trials (3 short pilots, 6 x 20-minute corrections),
with no infrastructure failures. Four performance flags were deadline misses in the pinned and indexed
arms; all planned trials completed. Verified archives and raw per-trial attribution are in
[campaign-20261031](../../../evaluation/results/correction-and-spikes/campaign-20261031/results-summary.md)
and its [machine-readable summary](../../../evaluation/results/correction-and-spikes/campaign-20261031/results-summary.json).

| Variant | Invocation slope, ms / 1,000 requests | Last-window invocation median, ms | Gen-2 collections per 20 min | Largest gen-2 pause, ms | Deadline misses (2 runs) | Retained history | Peak process-tree RSS, MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Pinned | 2.19 / 2.30 | 33.70 / 35.23 | 3 / 3 | 148.6 / 155.3 | 4 / 4 | 72,003 | 171.5 / 168.0 |
| Indexed | 0.073 / 0.044 | 10.37 / 9.09 | 3 / 3 | 161.0 / 154.6 | 3 / 3 | 72,003 | 185.3 / 181.5 |
| Bounded (6,000) | 0.043 / 0.016 | 10.52 / 8.96 | 0 / 0 | none | 0 / 0 | 6,000 | 93.5 / 91.4 |

Indexing removes the history-scan growth while preserving every transition and query result. It does not
remove full collections: all three gen-2 collections in each indexed run were aligned with large request
spikes. Bounded retention also removes the growth and gen-2 collections, but older transition records
are no longer queryable. Each 20-minute run in this campaign had zero skipped releases.

Spike attribution threshold is 10 ms over the request's 30-second block median, plus every missed
deadline. On the pinned and indexed arms, every gen-2 collection occurred inside a large request and
its collection duration accounted for essentially all of that request's excess latency. The 3 full
collections per 20-minute run grew from roughly 90 ms to 149-161 ms. This confirms the GC hypothesis
for the largest latency spikes. Remaining threshold spikes were classified as off-CPU time, not GC;
most had elevated voluntary context switches, while some coincided with high sampled platform CPU.
Those measures identify process non-execution time (blocking or scheduling) as the time component,
but do not identify the exact kernel wait site or the process responsible for platform load. They are
not a complete scheduler trace.

## Results: Scheduled GC Freeze

To test suppression of the full-collection spikes without dropping history, the second campaign ran
the pinned and indexed stores with Python's cyclic GC policy changed in the evaluation process. Every
100 completed requests, after logging that request and outside the next timed cycle, it calls
`gc.collect(1)` and then `gc.freeze()`. Full history remains queryable; cyclic objects that survive a
young-generation collection become permanently frozen and cannot later be reclaimed. The policy and
all counters are recorded per trial. The plan is [plan-gc-freeze.json](plan-gc-freeze.json); verified
results are in [campaign-20261101-gc-freeze](../../../evaluation/results/correction-and-spikes/campaign-20261101-gc-freeze/results-summary.md)
and its [machine-readable summary](../../../evaluation/results/correction-and-spikes/campaign-20261101-gc-freeze/results-summary.json).

All 4/4 20-minute trials completed with zero deadline misses, skipped releases or infrastructure
failures. Each run performed 121 freeze operations; the largest took 0.85-0.92 ms, and their cumulative
measured time was 85.6-86.8 ms over 20 minutes, outside request-cycle timing. There were zero gen-2
collections; the largest observed gen-1 collection was below 0.90 ms. No request had a gen-2 collection.

| Store | Invocation slope, ms / 1,000 requests | Last-window invocation median, ms | Gen-2 collections | >10 ms spikes, all off-CPU | Deadline misses | Frozen objects at end | Peak process-tree RSS, MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Pinned + freeze | 2.22 / 2.32 | 35.88 / 35.60 | 0 / 0 | 4 / 6 | 0 / 0 | 208,041 / 208,041 | 167.7 / 167.8 |
| Indexed + freeze | -0.055 / -0.084 | 10.01 / 9.33 | 0 / 0 | 7 / 1 | 0 / 0 | 220,064 / 220,063 | 177.3 / 177.6 |

This intervention suppresses the large GC pauses while retaining the full 72,003-transition history.
It does **not** fix the pinned store's O(N) history-scan slowdown: its slope remains about 2.2-2.3 ms
per 1,000 requests. Use the indexed variant to address that separate cost. The indexed negative slopes
are small relative to the per-window values and should be read as no measurable growth in this pair of
trials, not as a performance improvement over time.

The 18 remaining threshold spikes in the freeze campaign were all dominated by off-CPU time; 17 were
classified as voluntary-wait-associated by context-switch counts and one was not further classified.
No freeze-campaign spike was attributed to high platform CPU. “Voluntary wait” is a measured proxy,
not proof of a specific thread join or blocking syscall. The instrumentation does not identify the
exact source of that wait, so that residual attribution remains limited. The completed campaign
supports the narrow conclusion that periodic collect-and-freeze eliminated the repeated long full-GC
spikes and their deadline misses under this workload; it is not a general production GC recommendation.

Frozen-object counts were about 208k (pinned) and 220k (indexed) at the end of the runs. Peak RSS
was not higher than the corresponding default-GC arm in this small matrix, but frozen-object count
is not a byte count and freezing can retain otherwise collectible cycles indefinitely. Longer runs
and representative cyclic workloads are needed before treating the memory cost as bounded.

## Matrix

[plan.json](plan.json): orchestrated, 10 Hz, retained-state counters and spike instrumentation in every
trial.

| Phase | Trials | Condition |
| --- | ---: | --- |
| pilot | 3 | pinned/indexed/bounded, 20 s |
| correction | 6 | pinned/indexed/bounded x 2 rotated repetitions, 1,200 s each |

The estimated wall time is about 2.2 h, including 60 s cooldowns. Guards, sealing, ledgers and the
infrastructure-versus-performance classification are the same as in the operating-limits campaign.

[plan-gc-freeze.json](plan-gc-freeze.json): pinned/indexed x two 20-minute repetitions with
`gc_policy: freeze`, plus two short pilots. The four measured trials took about 1.5 h including
cooldowns, using the same sequential runner and guards.

## Hardware and Software Identity

The recorded board model is `NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super`.
That SKU has **8 GB nominal installed LPDDR5** ([NVIDIA product specification](https://developer.nvidia.com/embedded/jetson-orin-nano-super-developer-kit)); this is board capacity, not memory available to applications. Trial provenance records `MemTotal: 7,789,948 kB` and `psutil` total memory `7,976,906,752` bytes (about 7.43 GiB reported by the OS).

The exact archived Jetson Linux/L4T package is `nvidia-l4t-core 36.5.0-20260115194252` (`R36`, revision `5.0`). The `nvidia-jetpack` meta-package was not installed, and its package query returned no version. Consequently, these archives establish the exact L4T build but **do not establish a separately named JetPack SDK release**. Do not infer one from the L4T package identifier alone. Future campaigns should record the flashed image's JetPack release manifest or other authoritative SDK installation metadata before starting.

## Commands

```bash
# Upstream suite once per variant (pinned checkout unchanged)
.venv/bin/python -m evaluation.variant_suite_check --output evaluation/results/correction-and-spikes/variant-suite-check-YYYYMMDD.json

# Uninstrumented spike report for earlier sealed sustained trials
.venv/bin/python -m evaluation.spike_analysis <trial-folder>... --output <new-file>.json

# Campaign (Python 3.12 pinned interpreter), status, analysis
_temp/cp2n2-python312/bin/python -m evaluation.operating_limits run --phase all \
  --config docs/experiments/correction-and-spikes/plan.json \
  --output-dir evaluation/results/correction-and-spikes/campaign-YYYYMMDD
.venv/bin/python -m evaluation.operating_limits status --output-dir evaluation/results/correction-and-spikes/campaign-YYYYMMDD
.venv/bin/python -m evaluation.operating_limits analyze --output-dir evaluation/results/correction-and-spikes/campaign-YYYYMMDD
```

## Reproduction

```bash
# Verified history-correction campaign
.venv/bin/python -m evaluation.operating_limits analyze \
  --output-dir evaluation/results/correction-and-spikes/campaign-20261031

# Verified scheduled-GC-freeze campaign
.venv/bin/python -m evaluation.operating_limits analyze \
  --output-dir evaluation/results/correction-and-spikes/campaign-20261101-gc-freeze
```
