# Overnight CP2N2 follow-up

Questions: (1) effect of the existing resource-monitoring stack at 250 ms versus 2,000 ms; (2) full-history index effect at an offered rate of 50 requests/s. Comparisons use the same Jetson, simulated edge workload, pinned CP2N2 v5.1.1 and sequential scheduler. The treatment is the combined monitoring configuration; it is not an unmonitored control. The indexed arm retains full lifecycle history. No retention cap or GC policy change is used.

Prepared: 2026-11-07T12:09:48.300031+00:00. Reference evaluation commit: `5f4d05291a0e37d7f88b7a6874b8a7f57befd695`. Upstream: `36fbae002b0a9e066416ea9cafbb966a33fe53bb`.

Environment is recorded in `preflight.json`; the room temperature is only an approximate user estimate, not a sensor measurement. Each single-trial config, its hash and runner validation are preserved under `configs/` and `validation/`. The source hashes are in `source-fingerprint.json`.

Results pending measurement. The scheduler records every start, finish, cooldown and failure in `scheduler-ledger.jsonl`. It stops on infrastructure/data-quality failures, preserves performance flags, and never retries a started trial.
