# Overnight CP2N2 follow-up results

All paired values are treatment minus reference: indexed minus pinned for H; 2,000 ms minus 250 ms for M. Each pair is an independent run block. No equivalence claim is made from three noisy pairs.

## High-rate indexing: 50 requests/s

Pinned condition medians [range], n=3: cycle median 16.815 [16.534, 17.029] ms; cycle p95 27.562 [25.348, 27.834] ms; throughput 46.187 [46.179, 47.868] req/s.

Indexed condition medians [range], n=3: cycle median 9.230 [8.520, 9.609] ms; cycle p95 10.170 [10.162, 10.213] ms; throughput 49.920 [49.920, 49.920] req/s.

| Pair | Order | Indexed - pinned throughput req/s | Cycle median ms | Cycle p95 ms | Pinned/indexed misses | Pinned/indexed skips |
| --- | --- | ---: | ---: | ---: | --- | --- |
| H01 | pinned first | 3.733 | -8.509 | -17.672 | 5193 / 8 | 1143 / 24 |
| H02 | indexed first | 3.741 | -7.206 | -17.392 | 5092 / 9 | 1146 / 24 |
| H03 | pinned first | 2.052 | -7.305 | -15.135 | 4012 / 7 | 639 / 24 |

## Monitoring frequency: 10 requests/s

250 ms condition medians [range], n=3: cycle median 28.848 [26.034, 29.886] ms; cycle p95 47.295 [40.483, 47.431] ms; throughput 9.999 [9.999, 9.999] req/s.

2,000 ms condition medians [range], n=3: cycle median 28.630 [28.069, 29.155] ms; cycle p95 47.224 [47.044, 47.384] ms; throughput 9.999 [9.999, 9.999] req/s.

| Pair | Order | 2,000 - 250 throughput req/s | Cycle median ms | Cycle p95 ms | Observer CPU delta, % of one core | Fast/slow misses | Fast/slow skips |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| M01 | fast first | -0.000 | 0.307 | 0.089 | -10.521 | 5 / 5 | 1 / 1 |
| M02 | slow first | -0.000 | 2.596 | 6.741 | -10.558 | 5 / 5 | 1 / 1 |
| M03 | fast first | 0.000 | -1.817 | -0.387 | -10.512 | 5 / 5 | 1 / 1 |

See `per-run-results.csv` for medians, nearest-rank p95/p99/max, release-to-completion, exact accounting, failures, memory, CPU, temperature and coverage. `one-minute-trends.csv` and `high-rate-prefix.csv` provide the prescribed trend and matched-request analysis. `outliers.csv` describes >100 ms cycles and the previous 30-second-block excess criterion.

Interpret monitoring as a combined change to process-tree/tegrastats sampling, logging, serialization and guard cadence, not as disabling the monitoring stack. At 2,000 ms, short peaks can be missed. Do not claim isolated software power or general capacity from these runs.

This report uses the exact worktree/ref and package identities in `manifest.json` and `preflight.json`. Read `README.md` for reproduction, attempts, limitations, and source/configuration history.

## Supplementary post-analysis checks

Added after the frozen analyzer ran; values are recomputed from the sealed raw records and do not alter the generated tables above. Pilots (P01-A2, P02-P04) are excluded here as in the primary summaries.

### Exact outcome denominators (primary trials)

| Trial | Condition | Deadline misses / executed | Skipped / planned | Unreleased | Failed requests | Maintenance failures | Guard abort / timeout |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| H01-B | pinned 50/s | 5,193 / 13,857 (37.476%) | 1,143 / 15,000 (7.620%) | 0 | 0 | 0 | none / no |
| H01-I | indexed 50/s | 8 / 14,976 (0.053%) | 24 / 15,000 (0.160%) | 0 | 0 | 0 | none / no |
| H02-I | indexed 50/s | 9 / 14,976 (0.060%) | 24 / 15,000 (0.160%) | 0 | 0 | 0 | none / no |
| H02-B | pinned 50/s | 5,092 / 13,854 (36.755%) | 1,146 / 15,000 (7.640%) | 0 | 0 | 0 | none / no |
| H03-B | pinned 50/s | 4,012 / 14,361 (27.937%) | 639 / 15,000 (4.260%) | 0 | 0 | 0 | none / no |
| H03-I | indexed 50/s | 7 / 14,976 (0.047%) | 24 / 15,000 (0.160%) | 0 | 0 | 0 | none / no |
| M01-F | 250 ms sampling | 5 / 17,999 (0.028%) | 1 / 18,000 (0.006%) | 0 | 0 | 0 | none / no |
| M01-S | 2000 ms sampling | 5 / 17,999 (0.028%) | 1 / 18,000 (0.006%) | 0 | 0 | 0 | none / no |
| M02-S | 2000 ms sampling | 5 / 17,999 (0.028%) | 1 / 18,000 (0.006%) | 0 | 0 | 0 | none / no |
| M02-F | 250 ms sampling | 5 / 17,999 (0.028%) | 1 / 18,000 (0.006%) | 0 | 0 | 0 | none / no |
| M03-F | 250 ms sampling | 5 / 17,999 (0.028%) | 1 / 18,000 (0.006%) | 0 | 0 | 0 | none / no |
| M03-S | 2000 ms sampling | 5 / 17,999 (0.028%) | 1 / 18,000 (0.006%) | 0 | 0 | 0 | none / no |

Totals: pinned 50/s deadline_misses 14,297, completed 42,072, skipped 2,928, planned 45,000; indexed 50/s deadline_misses 24, completed 44,928, skipped 72, planned 45,000; 250 ms deadline_misses 15, completed 53,997, skipped 3, planned 54,000; 2,000 ms deadline_misses 15, completed 53,997, skipped 3, planned 54,000.

No request, backend or maintenance failures and no guard-triggered aborts or timeouts occurred; every runner exited 0 after its full active duration. All skips and misses are measured performance outcomes.

### Matched first 10,000 completed requests (50/s)

All six primary H runs reached 10,000 completed requests; the prefix was fixed before inspection. Detail by 1,000-request block is in `high-rate-prefix.csv` (which also lists partial P03/P04 pilot blocks; ignore those for primary inference). Paired values are in `high-rate-prefix-pairs.csv`.

| Pair | Pinned / indexed cycle median, ms | Indexed - pinned median, ms | Pinned / indexed cycle p95, ms | Indexed - pinned p95, ms | Pinned / indexed misses in prefix |
| --- | ---: | ---: | ---: | ---: | ---: |
| H01 | 15.814 / 8.688 | -7.126 | 20.824 / 10.200 | -10.624 | 1336 / 5 |
| H02 | 15.975 / 9.002 | -6.973 | 20.878 / 10.161 | -10.718 | 1238 / 4 |
| H03 | 15.657 / 8.752 | -6.905 | 18.592 / 10.173 | -8.419 | 142 / 4 |

In the first 1,000-request block both stores had similar medians (8.4-10.0 ms). Pinned medians then rose with completed requests and retained history to 18.4-20.8 ms by block 10, while indexed medians stayed between 8.3 and 9.9 ms. Retained-history values in `high-rate-prefix.csv` are the nearest retained-state sample at or before each block endpoint (`retained_sample_completed`), not exact endpoint counts. A matched request count does not control elapsed time or thermal history.

### Per-run resource and trend observations

CPU is percent of one logical core, averaged over process-tree samples during active execution; worker = root+child processes, observer = monitoring processes. RSS and temperature are maxima of sampled values and can miss short peaks, especially at 2,000 ms.

| Trial | Worker CPU | Observer CPU | Worker RSS max, MiB | Observer RSS max, MiB | Max temp, C | First-minute / last-minute cycle median, ms | Final retained history |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| H01-B | 78.3 | 11.9 | 181.2 | 29.1 | 57.6 | 11.41 / 27.07 | 83,145 |
| H01-I | 38.7 | 12.5 | 200.6 | 31.1 | 57.1 | 9.77 / 8.44 | 89,859 |
| H02-I | 40.5 | 12.3 | 200.6 | 29.1 | 56.5 | 8.39 / 9.88 | 89,859 |
| H02-B | 77.4 | 11.8 | 181.0 | 31.1 | 57.6 | 10.36 / 26.90 | 83,127 |
| H03-B | 76.2 | 12.1 | 184.7 | 29.1 | 57.8 | 11.08 / 24.33 | 86,169 |
| H03-I | 40.2 | 12.6 | 200.7 | 29.1 | 56.8 | 9.88 / 9.92 | 89,859 |
| M01-F | 27.6 | 12.6 | 210.5 | 31.1 | 57.3 | 9.62 / 48.97 | 107,997 |
| M01-S | 27.5 | 2.1 | 210.7 | 30.7 | 56.7 | 9.71 / 48.74 | 107,997 |
| M02-S | 27.6 | 2.2 | 212.3 | 31.1 | 56.7 | 9.87 / 48.44 | 107,997 |
| M02-F | 24.6 | 12.8 | 210.6 | 31.1 | 57.1 | 9.76 / 40.87 | 107,997 |
| M03-F | 28.0 | 12.8 | 210.4 | 29.1 | 57.1 | 9.50 / 48.82 | 107,997 |
| M03-S | 27.2 | 2.3 | 209.9 | 31.1 | 57.7 | 9.24 / 48.54 | 107,997 |

Condition medians [range] across n=3 runs: pinned 50/s: worker CPU 77.44 [76.21, 78.28], observer CPU 11.90 [11.77, 12.11], worker RSS max 181.17 [181.04, 184.68] MiB; indexed 50/s: worker CPU 40.23 [38.68, 40.55], observer CPU 12.51 [12.31, 12.65], worker RSS max 200.62 [200.59, 200.69] MiB; 250 ms: worker CPU 27.57 [24.62, 27.97], observer CPU 12.76 [12.64, 12.77], worker RSS max 210.46 [210.43, 210.58] MiB; 2,000 ms: worker CPU 27.53 [27.16, 27.57], observer CPU 2.21 [2.12, 2.25], worker RSS max 210.73 [209.92, 212.28] MiB.

### Long cycles and the prior local-block criterion

`prior-criterion-spikes-high-rate.json` and `prior-criterion-spikes-monitoring.json` are produced by the unchanged reference `evaluation/spike_analysis.py` (cycle at least 10 ms above its 300-request block median, plus every deadline miss; 5 and 20 ms counts also reported). At 50/s a 300-request block spans 6 s rather than 30 s. `outliers.csv` from the frozen helper uses 30-second slot blocks and a strict >10 ms excess, without adding deadline misses, so its counts differ. No new run has GC instrumentation, so every event is unattributed and none is attributed to garbage collection.

| Trial | Cycles > 100 ms | Prior criterion, 10 ms (incl. misses) | 5 ms | 20 ms |
| --- | ---: | ---: | ---: | ---: |
| H01-B | 2 | 5,193 | 5,193 | 5,193 |
| H01-I | 3 | 12 | 71 | 8 |
| H02-I | 3 | 9 | 79 | 9 |
| H02-B | 2 | 5,092 | 5,098 | 5,092 |
| H03-B | 2 | 4,012 | 4,015 | 4,012 |
| H03-I | 3 | 8 | 91 | 7 |
| M01-F | 4 | 15 | 91 | 5 |
| M01-S | 4 | 5 | 41 | 5 |
| M02-S | 4 | 7 | 54 | 5 |
| M02-F | 4 | 18 | 100 | 5 |
| M03-F | 4 | 8 | 92 | 5 |
| M03-S | 4 | 21 | 91 | 8 |

Cycles above 100 ms recur at nearly the same completed-request positions within each condition, with cycle time rising at each recurrence: in all six monitoring runs at request indices 2990, 6807, 10624 and 14813 (about 103-106, 141-149, 184-194 and 230-237 ms), independent of the 250 or 2,000 ms interval; in pinned 50/s runs at indices 6776-6807 and 10593-10624 (130-177 ms); and in indexed 50/s runs at indices 6451-6482, 10083-10113 and 13716-13747 (135-203 ms). Every primary-run request with a cycle above 100 ms or a deadline miss is listed in `long-cycle-slots.csv`. This alignment is descriptive; it is consistent with workload/history-driven events rather than sampler timing, but the cause is not measured here.

### Supported interpretation

- **Indexing at 50 requests/s:** improved execution in all three independent pairs: throughput +2.05 to +3.74 requests/s, cycle median -7.21 to -8.51 ms, cycle p95 -15.14 to -17.67 ms, deadline misses 4,012-5,193 (pinned) versus 7-9 (indexed) and skips 639-1,146 versus 24 per 15,000 planned releases. Worker CPU fell from about 77% to about 40% of one core. The indexed store still retains full history; its sampled worker RSS was higher (about 200 versus 181-185 MiB), plausibly reflecting more retained history and the index structure, which were not separately measured. Indexed runs were not miss-free.
- **Monitoring frequency at 10 requests/s:** no consistent latency effect was detected. The 2,000 - 250 ms cycle-median differences were +0.31, +2.60 and -1.82 ms (p95 +0.09, +6.74, -0.39 ms), with no sign consistency; the M02 difference arises mainly from a 250 ms run (M02-F) whose one-minute medians grew unusually slowly (last-minute median 40.9 ms versus 48.4-49.0 ms in the other five runs). Throughput was identical (9.999 requests/s) and misses/skips were identical (5/17,999 and 1/18,000 in every run). Observer CPU fell consistently by about 10.5 percentage points of one core (about 12.7% to 2.2%). The evidence is inconclusive about small latency effects and does not establish equivalence or non-interference.
