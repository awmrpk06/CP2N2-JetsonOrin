# Operating-limits results summary

- Generated offline from verified trial archives; never run concurrently with measurements.
- Per-trial nearest-rank statistics; across-trial values summarize trials, not pooled requests.
- Deadlines are experimental release-relative deadlines of one interarrival period unless stated otherwise.
- Intrusive allocation/calls trials are diagnostics only; their timings are not primary evidence.
- Power is platform VDD_IN; idle-adjusted values subtract the same worker's ready/post idle windows.

## pilot

Planned trials: 3. Status counts: {'success': 3}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| orchestrated@10Hz/pinned | primary | 1 | 10.366 | 11.596 | 10.00 | 0 | 0 | 4.881 | -0.022 |
| orchestrated@10Hz/indexed | primary | 1 | 9.036 | 9.929 | 10.00 | 0 | 0 | 4.810 | -0.029 |
| orchestrated@10Hz/bounded | primary | 1 | 9.191 | 10.316 | 10.00 | 0 | 0 | 4.799 | -0.034 |

Spike attribution (per trial; threshold 10 ms over the 30-s block median, plus every deadline miss):

| Trial | Lifecycle | GC policy | Invocation slope ms/1k req | Last-window median ms | Spikes | Causes | Off-CPU sub-causes | Explained | Gen-2 collections | Max gen-2 ms | Freeze max ms | Frozen objects | History entries | RSS max MiB |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pilot-r01-orchestrated-pinned-10hz | pinned | default | - | - | 0 | - | - | - | 0 | - | - | - | 1203 | 92.7 |
| pilot-r01-orchestrated-indexed-10hz | indexed | default | - | - | 0 | - | - | - | 0 | - | - | - | 1203 | 87.0 |
| pilot-r01-orchestrated-bounded-10hz | bounded | default | - | - | 0 | - | - | - | 0 | - | - | - | 1203 | 88.8 |

## correction

Planned trials: 6. Status counts: {'performance_failure': 4, 'success': 2}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| orchestrated@10Hz/pinned | primary | 2 | 21.924 | 34.380 | 10.00 | 8 | 0 | 4.875 | 0.044 |
| orchestrated@10Hz/indexed | primary | 2 | 9.800 | 11.218 | 10.00 | 6 | 0 | 4.815 | -0.028 |
| orchestrated@10Hz/bounded | primary | 2 | 9.730 | 11.280 | 10.00 | 0 | 0 | 4.813 | -0.039 |

Spike attribution (per trial; threshold 10 ms over the 30-s block median, plus every deadline miss):

| Trial | Lifecycle | GC policy | Invocation slope ms/1k req | Last-window median ms | Spikes | Causes | Off-CPU sub-causes | Explained | Gen-2 collections | Max gen-2 ms | Freeze max ms | Frozen objects | History entries | RSS max MiB |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| correction-r01-orchestrated-pinned-10hz | pinned | default | 2.192 | 33.70 | 15 | {'off_cpu': 11, 'gc': 3, 'deadline_only': 1} | {'unclassified': 3, 'voluntary_wait': 8} | 0.933 | 3 | 148.6 | - | - | 72003 | 171.5 |
| correction-r02-orchestrated-pinned-10hz | pinned | default | 2.302 | 35.23 | 6 | {'gc': 3, 'off_cpu': 2, 'deadline_only': 1} | {'voluntary_wait': 2} | 0.833 | 3 | 155.3 | - | - | 72003 | 168.0 |
| correction-r01-orchestrated-indexed-10hz | indexed | default | 0.073 | 10.37 | 21 | {'gc': 3, 'off_cpu': 18} | {'voluntary_wait': 16, 'cpu_contention': 2} | 1.000 | 3 | 161.0 | - | - | 72003 | 185.3 |
| correction-r02-orchestrated-indexed-10hz | indexed | default | 0.044 | 9.09 | 17 | {'gc': 3, 'off_cpu': 14} | {'voluntary_wait': 14} | 1.000 | 3 | 154.6 | - | - | 72003 | 181.5 |
| correction-r01-orchestrated-bounded-10hz | bounded | default | 0.043 | 10.52 | 11 | {'off_cpu': 11} | {'cpu_contention': 6, 'unclassified': 5} | 1.000 | 0 | - | - | - | 6000 | 93.5 |
| correction-r02-orchestrated-bounded-10hz | bounded | default | 0.016 | 8.96 | 0 | - | - | - | 0 | - | - | - | 6000 | 91.4 |
