# CP²N² selector reference run for v5.1.1

This directory freezes the selector artifacts generated on 2026-08-11 from
implementation commit `da262b751b2a5bbfc5866897bd38b9ab77372f3b`. The v5.1.1
release changes evidence packaging and documentation only; the selector
implementation is unchanged from that commit.

## Reproduction

From the repository root with the project virtual environment active:

```powershell
$env:MPLBACKEND = 'Agg'
$env:CP2N2_RESULTS_DIR = (Join-Path (Get-Location) 'evaluation\results\selector-reference-v5.1.1-20260811')
python -m evaluation.evaluate_matching
python -m evaluation.evaluate_matching_baselines
python -m evaluation.evaluate_selection_robustness
```

`Agg` makes chart generation headless and does not affect selector decisions.
The runs use deterministic task fixtures; `random_admissible` is deterministic
for these fixtures because its seed is derived from the task identifier.

## Frozen results

- Principal CP²N² lexicographic policy: 7/7 conformance cases.
- Weighted-comparison evaluation policy: 7/7.
- Static-priority baseline: 6/7.
- Constraint-based baseline: 6/7.
- Random-admissible baseline: 6/7.
- Policy holdout suite: 4/4.
- Seeded weighted-heuristic perturbations: 32; the principal lexicographic
  policy selected `safe-local` in every run.

These are curated functional conformance cases, not an estimate of accuracy on
an open task distribution. `SHA256SUMS.txt` covers every generated result in
this directory except itself.
