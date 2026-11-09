# Evaluated CP2N2 baseline

This directory includes the complete tracked source for the **unchanged CP2N2 v5.1.1 baseline** used in the reported Jetson experiments. Its exact identity and checksums are preserved in the source manifest. The baseline and measured evaluation scripts are supplied together in the one artifact snapshot cited by the paper.

## Contents

- [`cp2n2-v5.1.1/`](cp2n2-v5.1.1/): browsable, unmodified source snapshot, including all 470 tracked upstream files, tests, documentation and license notices. External dependencies and untracked local files are not included.
- [`cp2n2-v5.1.1.bundle`](cp2n2-v5.1.1.bundle): self-contained Git bundle containing the original v5.1.1 tag and its history. This reconstructs a checkout with the exact upstream Git identity required by the evaluation source guards.
- [`cp2n2-v5.1.1.manifest.json`](cp2n2-v5.1.1.manifest.json): full upstream commit, tag identity, file list, original Git blob IDs, SHA-256 checksums and bundle checksum.
- [`verify_baseline.py`](verify_baseline.py): standard-library integrity check for the included snapshot and bundle.

The MIT copyright and license notices in the upstream snapshot are retained unchanged. The source is redistributed under that license. Nothing in this source snapshot was optimized, patched or reformatted.

## Verify and reconstruct the tested checkout

Run these commands from the evaluation repository root:

```bash
python3 third_party/verify_baseline.py
mkdir -p _temp
git clone --branch v5.1.1 third_party/cp2n2-v5.1.1.bundle _temp/cp2n2-v5.1.1
git -C _temp/cp2n2-v5.1.1 rev-parse HEAD
git -C _temp/cp2n2-v5.1.1 status --porcelain --untracked-files=no
```

The reconstructed checkout must match the source manifest and its tracked-file status must be empty. If `_temp/cp2n2-v5.1.1` already exists, verify it instead of overwriting it. Do not copy an existing `.git` directory into the browsable snapshot.

Use the reconstructed Git checkout as `--upstream`. The browsable copy intentionally has no `.git` directory, so passing it directly to the existing runner would not satisfy the upstream-commit guard. Python 3.12, the packages recorded in each campaign's provenance and the documented Jetson/Linux facilities are still required for running measurements. Including source does not imply that measurements can be rerun on an arbitrary host.

## Experimental changes and recorded results

The experimental stores are implemented separately in [`evaluation/lifecycle_variants.py`](../evaluation/lifecycle_variants.py), and the collection policy is applied by the evaluation harness. They are not changes to the baseline source above.

- Resource/load, disconnection and power protocol: [`docs/experiments/operating-limits/README.md`](../docs/experiments/operating-limits/README.md).
- Storage and garbage-collection protocol: [`docs/experiments/correction-and-spikes/README.md`](../docs/experiments/correction-and-spikes/README.md).
- Paired high-rate and monitoring-frequency evidence: [`evaluation/results/overnight-followup/campaign-20261108T031906Z/README.md`](../evaluation/results/overnight-followup/campaign-20261108T031906Z/README.md).

Use the evaluation scripts included at this artifact's root with each campaign's recorded configurations. They match the measured runner, so a separate evaluation-code checkout is unnecessary. The raw measurements and sealed trial archives are unchanged.
