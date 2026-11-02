# Operating-limits results summary

- Generated offline from verified trial archives; never run concurrently with measurements.
- Per-trial nearest-rank statistics; across-trial values summarize trials, not pooled requests.
- Deadlines are experimental release-relative deadlines of one interarrival period unless stated otherwise.
- Intrusive allocation/calls trials are diagnostics only; their timings are not primary evidence.
- Power is platform VDD_IN; idle-adjusted values subtract the same worker's ready/post idle windows.

## pilot

Planned trials: 2. Status counts: {'success': 2}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| orchestrated@10Hz/pinned/gc-freeze | primary | 1 | 9.520 | 10.709 | 10.00 | 0 | 0 | 4.925 | 0.051 |
| orchestrated@10Hz/indexed/gc-freeze | primary | 1 | 10.346 | 11.345 | 10.00 | 0 | 0 | 4.858 | -0.040 |

Spike attribution (per trial; threshold 10 ms over the 30-s block median, plus every deadline miss):

| Trial | Lifecycle | GC policy | Invocation slope ms/1k req | Last-window median ms | Spikes | Causes | Off-CPU sub-causes | Explained | Gen-2 collections | Max gen-2 ms | Freeze max ms | Frozen objects | History entries | RSS max MiB |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pilot-r01-orchestrated-pinned-gcfreeze-10hz | pinned | freeze | - | - | 0 | - | - | - | 0 | - | 0.751 | 66441 | 1203 | 84.7 |
| pilot-r01-orchestrated-indexed-gcfreeze-10hz | indexed | freeze | - | - | 0 | - | - | - | 0 | - | 0.932 | 66663 | 1203 | 84.9 |

## gc-freeze

Planned trials: 4. Status counts: {'success': 4}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| orchestrated@10Hz/pinned/gc-freeze | primary | 2 | 21.904 | 35.305 | 10.00 | 0 | 0 | 4.873 | 0.011 |
| orchestrated@10Hz/indexed/gc-freeze | primary | 2 | 9.600 | 10.907 | 10.00 | 0 | 0 | 4.785 | -0.042 |

Spike attribution (per trial; threshold 10 ms over the 30-s block median, plus every deadline miss):

| Trial | Lifecycle | GC policy | Invocation slope ms/1k req | Last-window median ms | Spikes | Causes | Off-CPU sub-causes | Explained | Gen-2 collections | Max gen-2 ms | Freeze max ms | Frozen objects | History entries | RSS max MiB |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gc-freeze-r01-orchestrated-pinned-gcfreeze-10hz | pinned | freeze | 2.224 | 35.88 | 4 | {'off_cpu': 4} | {'unclassified': 1, 'voluntary_wait': 3} | 1.000 | 0 | - | 0.916 | 208041 | 72003 | 167.7 |
| gc-freeze-r02-orchestrated-pinned-gcfreeze-10hz | pinned | freeze | 2.324 | 35.60 | 6 | {'off_cpu': 6} | {'voluntary_wait': 6} | 1.000 | 0 | - | 0.853 | 208041 | 72003 | 167.8 |
| gc-freeze-r01-orchestrated-indexed-gcfreeze-10hz | indexed | freeze | -0.055 | 10.01 | 7 | {'off_cpu': 7} | {'voluntary_wait': 7} | 1.000 | 0 | - | 0.922 | 220064 | 72003 | 177.3 |
| gc-freeze-r02-orchestrated-indexed-gcfreeze-10hz | indexed | freeze | -0.084 | 9.33 | 1 | {'off_cpu': 1} | {'voluntary_wait': 1} | 1.000 | 0 | - | 0.917 | 220063 | 72003 | 177.6 |
