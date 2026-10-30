# Operating-limits campaign (embedded CP2N2 on Jetson)

Measures resource costs and operating limits of the pinned CP2N2 v5.1.1 release
(`36fbae002b0a9e066416ea9cafbb966a33fe53bb`, checkout `_temp/cp2n2-v5.1.1`) on this Jetson. It is not a
Jetson-versus-desktop comparison and it changes no manuscript files. The frozen matrix is
[plan.json](plan.json); the driver is [evaluation/operating_limits.py](../../../evaluation/operating_limits.py).

## Completed Campaign

The [October 29 campaign](../../../evaluation/results/operating-limits/campaign-20261029/results-summary.md)
completed all **64 planned trials** and passed offline archive verification.
Machine-readable results are in
[results-summary.json](../../../evaluation/results/operating-limits/campaign-20261029/results-summary.json);
each phase also contains an `analysis.json` and its unchanged per-trial archives.

All trials have terminal outcomes and there were no infrastructure failures.
Fifty trials have no performance flags; fourteen have deadline misses, skipped
releases, or other measured performance-limit flags. This is **not** a 78% request
success rate and does not mean fourteen experiments were incomplete.

- All three 30-minute runs completed. Each processed 17,999 requests successfully,
  with one skipped scheduled release and five completion-deadline misses.
- Lifecycle history grew from 3 to 107,997 entries in each long run, or six
  retained transitions per completed request. Idempotency entries remained zero.
  These counters identify retained state; they do not alone prove every source of
  allocated memory or justify removing audit/history semantics.
- In the 50 Hz sweep condition, median achieved throughput across three trials
  was approximately 45.37 requests/s. Deadline misses and skipped arrivals are
  reported independently, rather than hidden by the achieved rate.
- All 24 local-control scenarios met their specified assertions. Client-driven
  operation missed steps during the controlled client-link partition, while the
  local-supervisor mode continued the pre-authorized workload. This remains an
  experiment-layer supervisor with simulated resources, not a production
  availability or security guarantee.
- All five paired-power blocks completed. The median paired steady platform
  power difference was about 0.066 W. Read the baseline/coverage fields and
  dispersion in the JSON; this is not control-plane-only power or battery-life
  evidence.

The root `results-summary.md` table reports medians across per-trial statistics;
deadline/skip columns are totals across the condition's trials. Do not interpret
these summaries as pooled-request confidence intervals. Intrusive profiler
timings remain diagnostic-only. Raw captures and historical results are not
rewritten by analysis. Manuscript writing is deferred to the researcher.

## Interpreters

| Purpose | Interpreter |
| --- | --- |
| Measured runs (`run`), pinned CP2N2, `cl-sdk`, `mcp` | `_temp/cp2n2-python312/bin/python` (Python 3.12). `run` refuses older interpreters. |
| `validate`, `status`, `analyze`, unit tests | Either; the root `.venv` (Python 3.10) is enough. |

All commands below run from the repository root `/home/uit/Projects/phys-mcp`. Scratch files (intrusive
preflight, lock file) live under `_temp/`, never in `/tmp`.

## Commands

Validate the whole plan, print per-phase trial counts, runtime estimates and the source fingerprint
(no outputs written; add `--list-trials` to print every trial):

```bash
.venv/bin/python -m evaluation.operating_limits validate --phase all
```

Pilot (3 trials of 2 s, harness/direct/orchestrated, about 3 minutes including baselines and cooldowns):

```bash
_temp/cp2n2-python312/bin/python -m evaluation.operating_limits run --phase pilot \
  --output-dir evaluation/results/operating-limits/pilot-YYYYMMDD
```

Full sequential campaign (pilot, attribution, allocation-profiling, call-profiling, long-baseline, sweep,
local-control, paired-power; about 6.3 h):

```bash
_temp/cp2n2-python312/bin/python -m evaluation.operating_limits run --phase all \
  --output-dir evaluation/results/operating-limits/campaign-YYYYMMDD
```

Continue an interrupted campaign with the identical command plus `--resume` (see the partial-run rules below).
A single phase can be run on its own with `--phase <name>`; it gets its own folder and ledger.

Status (ledger only, no checksum verification; safe while a campaign runs):

```bash
.venv/bin/python -m evaluation.operating_limits status \
  --output-dir evaluation/results/operating-limits/campaign-YYYYMMDD
```

Analyze (verifies every sealed archive, then writes `analysis.json` per phase and `results-summary.json` /
`results-summary.md` at the given root; refuses while a campaign holds the measurement lock):

```bash
.venv/bin/python -m evaluation.operating_limits analyze \
  --output-dir evaluation/results/operating-limits/campaign-YYYYMMDD
```

`run` exits 0 only when every requested phase has all planned trials accounted for without infrastructure
failures; otherwise it prints the per-phase report and exits 2.

## Matrix and expected runtime

Estimates come from `validate`: each sustained trial is `2 x baseline + idle + duration + post_idle + 10 s`
start/teardown, each local-control trial is `duration + 15 s`, plus a 60 s cooldown between consecutive
trials (also across phase boundaries).

| Phase | Trials | Condition | Measured active | Est. wall |
| --- | ---: | --- | ---: | ---: |
| pilot | 3 | harness, direct, orchestrated; 10 Hz, 2 s; retained counters | 6 s | 3 min |
| attribution | 9 | harness/direct/orchestrated x 3 rotated blocks; 10 Hz, 300 s; retained counters | 45 min | 56 min |
| allocation-profiling | 2 | direct, orchestrated; 10 Hz, 300 s; tracemalloc (diagnostic only) | 10 min | 12 min |
| call-profiling | 1 | orchestrated; 10 Hz, 300 s; cProfile early/late windows (diagnostic only) | 5 min | 5 min |
| long-baseline | 3 | orchestrated; 10 Hz, 1800 s; retained counters | 90 min | 93 min |
| sweep | 12 | orchestrated; 1/10/25/50 Hz x 3, rotated order; 300 s; retained counters | 60 min | 75 min |
| local-control | 24 | local/client x 4 scenarios x 3, seed 20261029; 90 s at 10 Hz | 36 min | 65 min |
| paired-power | 10 | 5 AB/BA pairs of direct/orchestrated; 60 s ready idle, 120 s active, 60 s post idle | 20 min active | 61 min |
| **all** | **64** | | **4 h 26 min** | **about 6.3 h** |

Sweep order rotates the rate list cyclically per repetition (frozen, not randomized at run time). Every
sustained deadline is one interarrival period (1000/100/40/20 ms), an experimental release-relative deadline,
not an application or safety contract.

### Paired-power baseline grouping

This is the only intended deviation from a uniform per-trial layout. In each pair the **first** trial uses
`platform_baseline_s` (60 s) for its platform-only window before the worker starts (and, because the
profiler records the same length afterwards, a 60 s platform window after it). The **second** trial uses
the campaign `platform_idle_s` (3 s) before and after. Each worker still has 60 s ready idle, 120 s active
and 60 s post idle, so the planned measured windows per block (60 s platform idle + 2 x 240 s workers,
45 min for five blocks) are unchanged; only the extra 60 s platform-after window and the two 3 s windows
are added. Power is platform VDD_IN, not control-plane-only power.

## Scopes and what is not measured

- Sustained phases run `evaluation/upstream_sustained.py` against the pinned checkout, with an external
  process/platform sampler at `interval_ms` (250 ms). Harness mode loads no CP2N2 modules; direct and
  orchestrated load the pinned release.
- Retained-state counters read `len()` of pinned lifecycle/lease/idempotency collections under their locks.
  Allocation and call profiling are intrusive; their timings are labelled `diagnostic_not_primary` and never
  pooled with primary timings.
- Local-control trials compare scheduling placement through an evaluation-layer supervisor and loopback
  proxy around pinned CP2N2. This is not an upstream feature. No external process or platform sampler runs
  during them; only the supervisor's self-reported peak RSS is retained.
- Ambient temperature is recorded as unmeasured (`null`); cooling is the developer-kit fan and heatsink in
  open air. No desktop comparison, hosted model, physical substrate, power-mode change or network change.

## Guards and partial-run rules

- **Host lock**: one measurement or analysis process at a time (`_temp/operating-limits.lock`).
- **Host preflight** before outputs: Python 3.12, `tegrastats` available, at least 4 GiB free disk. The
  worker profiler additionally applies the thermal, available-memory, workload-RSS, disk and telemetry
  freshness guards; a guard stop is recorded as an infrastructure failure.
- **Intrusive preflight**: before any measured trial, `run --phase all` runs the real worker for 0.5 s in
  harness mode with each intrusive profile in a scratch folder under `_temp/`. If either fails, the campaign
  stops before the pilot and the ledger records the reason.
- **Source guard**: `campaign.json` / `frozen.json` freeze the configuration, the interpreter, the upstream
  path and SHA-256 hashes of every harness, monitor, worker, diagnostics, local-control and analysis module.
  The fingerprint is rechecked before every trial; any change stops the campaign. The pinned upstream's
  revision and source hashes are checked before and after every trial.
- **Immutable ledger**: `ledger.jsonl` per phase and `campaign-ledger.jsonl` at the root are append-only and
  fsynced. Each trial folder is sealed with `reproduction-manifest.json` checksums.
- **Infrastructure versus measured failures**: guard stops, timeouts, worker crashes or interruptions,
  missing summaries, missing or erroneous diagnostics, leftover owned processes and inconsistent exit
  statuses stop the campaign at that trial. Failed requests, maintenance failures, unexpected backends,
  deadline misses, skipped releases and failed local-control assertions are results; the campaign continues.
- **Resume**: `--resume` requires byte-identical configuration, sources, interpreter and upstream path. It
  re-verifies every finished trial, then starts only trials that never started. A trial that started without
  a finish record, or that stopped the campaign, is never redone in that campaign; investigate, then start a
  new, separately named campaign folder and link it in your notes. Incomplete folders stay as evidence.
- Do not run tests, builds, installs, analysis or other CPU work on the Jetson while a campaign is measuring.

## Output layout

```
evaluation/results/operating-limits/<campaign-id>/
  campaign.json  campaign-ledger.jsonl  results-summary.json  results-summary.md
  <phase>/frozen.json  ledger.jsonl  analysis.json
  <phase>/<trial-id>/provenance.json  result.json  reproduction-manifest.json  stdout.log  stderr.log
                     workload/... (sustained)  controller/... (local-control)  resource-summary.json
```
