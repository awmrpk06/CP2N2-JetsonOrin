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
| harness@10Hz | primary | 1 | 0.010 | 0.014 | 10.00 | 0 | 0 | 4.756 | -0.012 |
| direct@10Hz | primary | 1 | 0.433 | 0.520 | 10.00 | 0 | 0 | 4.885 | -0.529 |
| orchestrated@10Hz | primary | 1 | 8.550 | 9.339 | 10.00 | 0 | 0 | 4.925 | -0.516 |

## attribution

Planned trials: 9. Status counts: {'success': 7, 'performance_failure': 2}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| harness@10Hz | primary | 3 | 0.009 | 0.012 | 10.00 | 0 | 0 | 4.706 | -0.016 |
| direct@10Hz | primary | 3 | 0.453 | 0.540 | 10.00 | 0 | 0 | 4.704 | -0.106 |
| orchestrated@10Hz | primary | 3 | 12.344 | 14.490 | 10.00 | 2 | 0 | 4.779 | -0.037 |

## allocation-profiling

Planned trials: 2. Status counts: {'success': 1, 'performance_failure': 1}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| direct@10Hz/allocation | diagnostic_not_primary | 1 | 1.051 | 1.181 | 10.00 | 0 | 0 | 4.705 | -0.104 |
| orchestrated@10Hz/allocation | diagnostic_not_primary | 1 | 32.697 | 36.672 | 9.97 | 2 | 9 | 4.881 | 0.076 |

## call-profiling

Planned trials: 1. Status counts: {'performance_failure': 1}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| orchestrated@10Hz/calls | diagnostic_not_primary | 1 | 12.102 | 16.494 | 10.00 | 1 | 0 | 4.789 | -0.031 |

## long-baseline

Planned trials: 3. Status counts: {'performance_failure': 3}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| orchestrated@10Hz | primary | 3 | 28.888 | 47.352 | 10.00 | 15 | 3 | 4.894 | 0.067 |

## sweep

Planned trials: 12. Status counts: {'success': 5, 'performance_failure': 7}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| orchestrated@1Hz | primary | 3 | 10.174 | 11.187 | 1.00 | 0 | 0 | 4.705 | -0.112 |
| orchestrated@10Hz | primary | 3 | 12.338 | 16.157 | 10.00 | 1 | 0 | 4.798 | -0.010 |
| orchestrated@25Hz | primary | 3 | 15.540 | 23.683 | 24.99 | 9 | 9 | 4.948 | 0.146 |
| orchestrated@50Hz | primary | 3 | 16.961 | 28.958 | 45.37 | 16269 | 4160 | 5.377 | 0.575 |

## local-control

Planned trials: 24. Status counts: {'success': 24}.

| Condition | Trials | Status counts | Failed assertions | Missed steps (median) |
| --- | ---: | --- | --- | ---: |
| local/connected-nofault | 3 | {'success': 3} | - | 0 |
| client/partition-nofault | 3 | {'success': 3} | - | 301 |
| local/partition-fault | 3 | {'success': 3} | - | 0 |
| client/partition-fault | 3 | {'success': 3} | - | 301 |
| client/connected-fault | 3 | {'success': 3} | - | 0 |
| local/partition-nofault | 3 | {'success': 3} | - | 0 |
| client/connected-nofault | 3 | {'success': 3} | - | 0 |
| local/connected-fault | 3 | {'success': 3} | - | 0 |

## paired-power

Planned trials: 10. Status counts: {'success': 10}.

| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| direct@10Hz | primary | 5 | 0.496 | 0.570 | 10.00 | 0 | 0 | 4.721 | 0.007 |
| orchestrated@10Hz | primary | 5 | 10.382 | 12.406 | 10.00 | 0 | 0 | 4.787 | 0.076 |

Paired orchestrated_minus_direct: steady W median 0.066 (n=5, stdev 0.003); idle-adjusted W median 0.066 (n=5). Excluded pairs: none.
