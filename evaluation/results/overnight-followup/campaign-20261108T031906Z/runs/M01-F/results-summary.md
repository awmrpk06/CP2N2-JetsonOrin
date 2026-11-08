# Operating-limits results summary

- Generated offline from verified trial archives; never run concurrently with measurements.
- Per-trial nearest-rank statistics; across-trial values summarize trials, not pooled requests.
- Deadlines are experimental release-relative deadlines of one interarrival period unless stated otherwise.
- Intrusive allocation/calls trials are diagnostics only; their timings are not primary evidence.
- Power is platform VDD_IN; idle-adjusted values subtract the same worker's ready/post idle windows.

## M01-F

Planned trials: 1. Status counts: {'performance_failure': 1}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| orchestrated@10Hz/pinned | primary | 1 | 28.848 | 47.295 | 10.00 | 5 | 1 | 4.928 | 0.091 |
