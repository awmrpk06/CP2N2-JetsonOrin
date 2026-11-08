# Overnight CP2N2 Follow-up

## Questions and Scope

1. Does changing the existing process-tree/tegrastats sampling interval from 250 ms to 2,000 ms affect latency during sustained 10 requests/s execution?
2. Does the evaluation-layer full-history index improve the pinned v5.1.1 implementation at an offered 50 requests/s?

This compares the combined monitoring configuration, including sampling, serialization, logging and interval-periodic resource guards. It is not a monitoring-disabled comparison. Each row uses a fresh process/backend/history, orchestrated mode, simulated edge backend, periodic skip-expired-slots admission, default GC, full history, diagnostic counts, no intrusive profiler, no spike instrumentation, 3 s ready idle, no post-idle, 3 s platform baseline before/after, and a release-relative completion deadline equal to one period including maintenance (20 ms at 50/s, 100 ms at 10/s). Each adjacent trial is separated by at least 60 seconds.

## Environment

- Board: NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super; 8 GB nominal RAM, `MemTotal` 7789948 kB; architecture aarch64.
- Software: JetPack 6.2.2 / L4T 36.5.0 (`nvidia-l4t-core 36.5.0-20260115194252`); kernel 5.15.185-tegra; measurement Python 3.12.15.
- Power/frequency: NV Power Mode 25W (mode ID 1, `nvpmodel -q`); online CPUs 0-5; policy0: schedutil 729600-1344000 kHz; policy4: schedutil 729600-1344000 kHz. The configured 25 W mode is not a power measurement.
- Cooling/fan: Stock developer-kit fan and heatsink, open air (confirmed by user). `nvfancontrol` is active; preflight fan readings: rpm=2154, pwm1=92.
- Background: one-second system CPU 1.2%; load averages [0.94482421875, 0.5712890625, 0.24365234375]; 362 processes. Existing desktop/update services remained running and were not altered.
- Ambient: approximately 24.0 C by user estimate; not sensor-measured or continuous.
- Full Python package versions and raw board details are in `preflight.json`; trial-time temperatures and monitoring coverage are in each sealed resource archive.

## Schedule and Outcomes

The original 16 protocol identifiers are preserved in `manifest.json`. P01-A1 is a prior completed 60-second run retained under `prior-attempts/`; it passed all measurement checks but the first scheduler rejected it due to a faulty pinned-store class assertion. It is included as a prior attempt, not silently counted as a new scheduled row. P01-A2 repeats that pilot under a new ID after the assertion correction, before P02. The remaining rows retain the requested IDs and order.

The paired order alternates but is intentionally not perfectly balanced: H blocks B-I, I-B, B-I; M blocks F-S, S-F, F-S. Here B=pinned, I=indexed, F=250 ms, S=2,000 ms. The two comparisons are analyzed independently.

| # | ID | Role | Block | Store | Offered req/s | Active s | Sample ms | Planned | Status |
| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| 1 | P01-A2 | pilot | - | pinned | 10 | 60 | 250 | 600 | success |
| 2 | P02 | pilot | - | pinned | 10 | 60 | 2000 | 600 | success |
| 3 | P03 | pilot | - | pinned | 50 | 60 | 250 | 3000 | performance_failure |
| 4 | P04 | pilot | - | indexed | 50 | 60 | 250 | 3000 | performance_failure |
| 5 | H01-B | primary | H01 | pinned | 50 | 300 | 250 | 15000 | performance_failure |
| 6 | H01-I | primary | H01 | indexed | 50 | 300 | 250 | 15000 | performance_failure |
| 7 | H02-I | primary | H02 | indexed | 50 | 300 | 250 | 15000 | performance_failure |
| 8 | H02-B | primary | H02 | pinned | 50 | 300 | 250 | 15000 | performance_failure |
| 9 | H03-B | primary | H03 | pinned | 50 | 300 | 250 | 15000 | performance_failure |
| 10 | H03-I | primary | H03 | indexed | 50 | 300 | 250 | 15000 | performance_failure |
| 11 | M01-F | primary | M01 | pinned | 10 | 1800 | 250 | 18000 | performance_failure |
| 12 | M01-S | primary | M01 | pinned | 10 | 1800 | 2000 | 18000 | performance_failure |
| 13 | M02-S | primary | M02 | pinned | 10 | 1800 | 2000 | 18000 | performance_failure |
| 14 | M02-F | primary | M02 | pinned | 10 | 1800 | 250 | 18000 | performance_failure |
| 15 | M03-F | primary | M03 | pinned | 10 | 1800 | 250 | 18000 | performance_failure |
| 16 | M03-S | primary | M03 | pinned | 10 | 1800 | 2000 | 18000 | performance_failure |

4 scheduled pilots and 12 primary trials completed, plus 1 preserved prior pilot attempt(s); 0 infrastructure failures and 14 trials with measured performance flags. All paired differences and condition summaries are in `results-summary.md`, `summary.json`, `per-run-results.csv` and `paired-differences.csv`.

## Attempts and Source Changes

There were 7 recorded setup/suite attempts before the final run; 5 ended before a valid report. They are listed in `preflight-attempts.json`; no scientific trial was started by those attempts. The successful variant report is `variant-suite-check.json`: pinned, indexed and bounded/6000 each passed 146 tests; bounded/1 was the deliberate positive control and failed two history-dependent tests.

Measurements used a detached clean worktree at `5f4d05291a0e37d7f88b7a6874b8a7f57befd695` and unchanged CP2N2 v5.1.1 `36fbae002b0a9e066416ea9cafbb966a33fe53bb`. The live checkout was at `943e26a6c252e6c24414ced7d19b12c8fde688fe`; its two later commits only document JetPack and capture `jetson_release` output. No measured evaluation-source changes were made. See `source-change-log.json`, `source-fingerprint.json`, per-trial provenance and `manifest.json` for hashes. The scheduler/verifier helper (`tools/overnight.py`, not part of CP2N2 or the measured runner) was corrected after P01-A1 and before this campaign was frozen; that change and every post-measurement analysis supplement are listed in `post-measurement-change-log.json`. Existing manuscript changes were not used or modified.

## Reproduction and Analysis

The frozen single-row configs are under `configs/`; `schedule.csv` records the exact order. From the repository root, validate/run an individual row with the pinned Python:

```bash
PY312=/home/uit/Projects/phys-mcp/_temp/cp2n2-python312/bin/python
REPO=/home/uit/Projects/phys-mcp/_temp/overnight-followup-ref-5f4d
UPSTREAM=/home/uit/Projects/phys-mcp/_temp/cp2n2-v5.1.1
BUNDLE=/home/uit/Projects/phys-mcp/evaluation/results/overnight-followup/campaign-20261108T031906Z
ID=P01-A2  # configs/ holds P01-A2, P02-P04, H01-B ... M03-S; P01-A1's config is prior-attempts/P01-A1/P01.json
"$PY312" -m evaluation.operating_limits validate --config "$BUNDLE/configs/$ID.json" --phase all --list-trials
"$PY312" -m evaluation.operating_limits run --config "$BUNDLE/configs/$ID.json" --phase all --upstream "$UPSTREAM" --output-dir "$BUNDLE/runs/$ID"
```

The frozen scheduler is `tools/overnight.py`; full analysis is performed only after the measurement schedule ends:

```bash
"$PY312" "$BUNDLE/tools/overnight.py" analyze --bundle "$BUNDLE" --repo "$REPO"
```

The schedule ledger is `scheduler-ledger.jsonl`. Each run preserves the runner's campaign/phase/trial folder names and sealed raw CSV, resource telemetry, summary, provenance, logs and reproduction manifest. Never overwrite or resume a started scientific trial.

The primary high-rate matched-prefix comparison uses exactly the first 10,000 completed request rows from each H run; paired median/p95 results are in `high-rate-prefix-pairs.csv`, while `high-rate-prefix.csv` retains the 1,000-request block detail. The `Supplementary post-analysis checks` section of `results-summary.md` adds exact miss/skip denominators, resource observations, long-cycle positions and the supported interpretation. The prior local-block criterion was computed with the unchanged reference tool:

```bash
cd "$REPO"
"$PY312" -m evaluation.spike_analysis "$BUNDLE"/runs/H0*/H0*/H0*-r01-* --rate-hz 50 --output "$BUNDLE/prior-criterion-spikes-high-rate.json"
"$PY312" -m evaluation.spike_analysis "$BUNDLE"/runs/M0*/M0*/M0*-r01-* --rate-hz 10 --output "$BUNDLE/prior-criterion-spikes-monitoring.json"
```

Additional validation and immutability checks are recorded in `analysis-review.json`.

## Archive and Verification

The compressed archive `../campaign-20261108T031906Z.tar.zst` was created after all measurements and analysis. `../campaign-20261108T031906Z.archive-sha256.json` lists the size and SHA-256 of every archived file and of the archive; `../campaign-20261108T031906Z.tar.zst.sha256` is a conventional checksum sidecar; `../campaign-20261108T031906Z.archive-verification.json` records the successful verification. These package files sit beside this folder so that the archive does not contain itself; this README therefore cannot quote the archive checksum. To verify independently from `evaluation/results/overnight-followup`:

```bash
sha256sum -c campaign-20261108T031906Z.tar.zst.sha256
zstd -t campaign-20261108T031906Z.tar.zst
mkdir -p /tmp/verify && tar -I 'zstd -d' -xf campaign-20261108T031906Z.tar.zst -C /tmp/verify
python3 - <<'PY'
import hashlib, json, pathlib
m = json.load(open("campaign-20261108T031906Z.archive-sha256.json"))
bad = [f["path"] for f in m["files"] if hashlib.sha256(pathlib.Path("/tmp/verify", f["path"]).read_bytes()).hexdigest() != f["sha256"]]
print("files:", len(m["files"]), "mismatches:", bad or "none")
PY
```

## Limitations

Three independent pairs per comparison are exploratory, noisy run-level replication; requests are not independent replicates. No statistical equivalence or absence-of-interference conclusion follows from small differences. The high-rate result is limited to this simulated workload and duration; the index retains all history and does not bound memory. The monitoring arm measures the whole observer configuration. Two-second sampling can miss short-lived peaks, and sampled CPU/RSS/temperature do not isolate control-plane power or actual resource maxima. The configured 25 W profile is not measured consumption. A matched request count does not control elapsed time or thermal history. Cycles above 100 ms and the prior 30-second local-block excess criterion are descriptive only; no new run used GC-event instrumentation, so individual new spikes are not attributed to GC.
