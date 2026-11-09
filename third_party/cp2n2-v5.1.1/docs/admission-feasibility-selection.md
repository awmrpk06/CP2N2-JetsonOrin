# Admission, feasibility, and selection

## Scope

A3 replaces the v3.0 single-score matcher with three decisions that must not
be conflated:

1. **Admission** determines whether a resource is compatible with the task and
   its policy and safety constraints.
2. **Feasibility** determines whether an admitted resource can be used now
   within dynamic availability, freshness, reservation, latency, and cost
   bounds.
3. **Ranking** expresses a preference only among admitted and feasible
   resources.

Changing a ranking policy or a ranking weight can never admit a resource that
failed either hard stage.

## Admission constraints

The machine-readable admission record names every evaluated constraint:

- directed backend target;
- supported task type;
- input-modality compatibility;
- repeated-invocation capability;
- declared required telemetry;
- age-of-information observability;
- human-supervision policy;
- continuous health-monitoring capability;
- confidence observability; and
- resource-contract safety and runtime attestation.

Unknown safety-relevant information remains conservative. In particular, an
unknown runtime kind, missing safety contract, operation that is not explicitly
permitted, or an incomplete physical-hardware safety declaration fails
admission.

## Feasibility constraints

Feasibility evaluates:

- runtime health and control-plane lifecycle;
- current reservability for the requesting client;
- current availability of every required runtime telemetry field;
- the hard latency budget;
- the hard drift threshold (`drift_score < 0.95`);
- the requested age-of-information bound; and
- an optional per-invocation cost ceiling in a specified currency.

When a cost ceiling is present, unknown cost or a currency mismatch is
infeasible rather than free. A task without a cost ceiling may still rank
known-cost resources ahead of unknown-cost resources under
`locality_cost_first`.

## Principal and profile policies

All tuples use ascending order and end with `backend_id` as a stable tie
breaker. Health, drift, freshness, variability, locality distance, and cost
are normalized into explicit fields in `ranking.criteria`.

- `lexicographic` is the principal policy:
  `(health, health sensitivity, drift, freshness, variability mismatch,
  reset mismatch, latency ratio, locality distance, cost-known flag, cost)`.
- `latency_first`:
  `(latency ratio, health, health sensitivity, drift, freshness, locality
  distance, cost-known flag, cost, variability mismatch, reset mismatch)`.
- `safety_freshness_first`:
  `(health, health sensitivity, drift, freshness, latency ratio, locality
  distance, cost-known flag, cost, variability mismatch, reset mismatch)`.
- `locality_cost_first`:
  `(locality distance, cost-known flag, cost, health, health sensitivity,
  drift, freshness, latency ratio, variability mismatch, reset mismatch)`.

The principal policy is intentionally non-compensatory: a later advantage
cannot cancel an earlier safety/freshness disadvantage. It also does not
prefer physical hardware merely because its evidence level is higher.

## Explicit weighted comparison

`weighted_comparison` preserves the previous additive heuristic as a
transparent comparison after hard checks. It is not the principal CP²N²
policy. The implementation exposes an isolated copy through
`BackendMatcher.weighted_heuristic`, and every candidate report contains its
individual `weighted_components`.

| Term | Weight |
|---|---:|
| base accepted | +50 |
| task supported | +20 |
| modality overlap | +15 |
| latency within budget | +20 |
| low-variability match | +6 |
| low-variability mismatch | -8 |
| stochastic backend tolerated | +2 |
| continuous health telemetry | +8 |
| continuous drift telemetry | +4 |
| each required telemetry field | +2 |
| age-of-information support | +6 |
| reset-free match | +4 |
| reset-free mismatch | -8 |
| locality exact match | +6 |
| health-sensitive resource | -5 |
| ready health | +4 |
| degraded health | -20 |
| low drift (`<= 0.25`) | +6 |
| medium drift (`<= 0.75`) | `-12 * drift_score` |
| comfortable freshness (`<= 0.5` of bound) | +4 |
| higher freshness consumption | `-6 * freshness_ratio` |

Locality mismatch penalties are also explicit: adjacent edge/fog/local
placements cost 3, cloud-to-fog/local and lab-to-local cost 2, and other
mismatches cost 6.

The old latency-overshoot score is intentionally absent: latency is now a hard
feasibility constraint and therefore cannot be compensated by unrelated
positive weights.

## Baselines

The same admitted-and-feasible set feeds three reproducible baselines:

- `static_priority`: configured backend order, then backend identifier;
- `constraint_based`: backend identifier after hard checks, with no preference
  model; and
- `random_admissible`: SHA-256 ordering over the task ID, backend ID, and the
  task's explicit `selection_seed`.

The random baseline is deterministic across process runs and descriptor input
order. None of the baselines bypasses admission or feasibility.

## Decision report

`MatchReport` records the task ID, selected policy, and every candidate.
Each `MatchCandidate` contains:

- `admission.passed`, satisfied constraints, and violations;
- `feasibility.passed`, satisfied constraints, and violations;
- `ranking.policy`, final rank, rank key, normalized criteria, weighted
  components, and weighted comparison score; and
- compatibility fields `accepted`, `score`, `reasons`, and
  `rejection_reasons` for existing clients.

The report serializes directly to strict JSON. Excluded resources have no rank
and every exclusion identifies the failed constraint.

## Evaluation

Run:

```text
python -m evaluation.evaluate_matching
python -m evaluation.evaluate_matching_baselines
python -m evaluation.evaluate_selection_robustness
```

The robustness evaluation uses holdout task variants and 32 seeded
perturbations of all comparison weights. It reports the lexicographic and
weighted selections separately so weight sensitivity cannot be mistaken for
principal-policy instability.
