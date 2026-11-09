# CP²N² on Jetson Orin Nano: reproducibility artifact

This is the complete artifact for the case study **Characterizing Embedded Control-Plane Costs for Physical Neural Resources**. Use the single snapshot linked in the paper. It includes the evaluated CP²N² baseline, the measured evaluation scripts, experimental changes, frozen configurations and recorded results.

## Start here

1. Download or clone this artifact snapshot and enter its root directory.
2. Verify the included material:

   ```bash
   python3 scripts/verify_study_artifact.py
   ```

3. Follow the [baseline setup guide](third_party/README.md) to reconstruct the included CP²N² v5.1.1 Git checkout. The portable bundle supplies the baseline locally, without another repository download.
4. Use Python 3.12 and the Jetson/Linux environment documented in the protocols below. Requirements files and each campaign's recorded package versions describe the dependencies. The bundled baseline and evaluation scripts are already the versions needed for this study; no other evaluation-code revision needs to be selected.

## Use the included runner

After reconstructing the baseline and preparing the documented Python environment, validate a recorded configuration from this artifact's root:

```bash
REPO="$(pwd)"
PY312="$REPO/_temp/cp2n2-python312/bin/python"  # the prepared Python 3.12 environment
UPSTREAM="$REPO/_temp/cp2n2-v5.1.1"
CAMPAIGN="$REPO/evaluation/results/overnight-followup/campaign-20261108T031906Z"
"$PY312" -m evaluation.operating_limits validate \
  --config "$CAMPAIGN/configs/H01-B.json" --phase all --list-trials
```

To run that configuration on the documented Jetson/Linux setup, use a new output directory:

```bash
"$PY312" -m evaluation.operating_limits run \
  --config "$CAMPAIGN/configs/H01-B.json" --phase all \
  --upstream "$UPSTREAM" --output-dir "$REPO/_temp/new-H01-B"
```

Do not reuse an existing output directory. For multiple trials, follow the supplied schedule, use fresh workers, preserve the guards and allow at least 60 seconds between adjacent runs. Use this included runner rather than the historical scheduler's host-specific preparation/run commands; no older evaluation checkout is needed.

Perform offline reanalysis on a copy of the relevant campaign under `_temp`, not inside the archived records. The original commands and manifests are retained as provenance; adapt their paths to the current artifact root.

## What is included

| Material | Location |
| --- | --- |
| Complete unchanged CP²N² baseline source | [`third_party/cp2n2-v5.1.1/`](third_party/cp2n2-v5.1.1/) |
| Portable baseline Git bundle, license and integrity information | [`third_party/`](third_party/) |
| Measured evaluation runner and analysis tools | [`evaluation/`](evaluation/) |
| Experimental history-store changes | [`evaluation/lifecycle_variants.py`](evaluation/lifecycle_variants.py) |
| Artifact verification and detailed provenance | [`artifact-manifest.json`](artifact-manifest.json) |

The historical prototype modules at the repository root are retained as supporting material. The reported baseline is the complete source bundled under `third_party/`; use the setup guide and the runner's `--upstream` option.

## Reproduce or inspect each experiment

| Evaluation | Protocol and commands | Recorded measurements |
| --- | --- | --- |
| Resource growth, load sweep, local supervision and platform power | [Protocol](docs/experiments/operating-limits/README.md) | [Results](evaluation/results/operating-limits/campaign-20261029/results-summary.md) |
| History stores and garbage-collection interventions | [Protocol](docs/experiments/correction-and-spikes/README.md) | [Storage](evaluation/results/correction-and-spikes/campaign-20261031/results-summary.md), [collection policy](evaluation/results/correction-and-spikes/campaign-20261101-gc-freeze/results-summary.md) |
| Paired high-rate indexing and monitoring-frequency comparisons | [Protocol, environment and commands](evaluation/results/overnight-followup/campaign-20261108T031906Z/README.md) | [Results](evaluation/results/overnight-followup/campaign-20261108T031906Z/results-summary.md) |
| Short direct/orchestrated timing measurements | [Protocol](docs/embedded-feasibility/original-protocol.md) | [Recorded comparison](evaluation/results/original-paper-20261024/comparison.md) |

Existing recorded absolute paths describe the measurement host. The portable command example above uses this artifact directly. New measurements and reanalysis must use new output directories; do not overwrite or resume an existing scientific trial.

The public records include simulated-resource workloads on one Jetson. Baseline and modified implementations are identified in each configuration. Read the timing, sampling, outcome-accounting and power boundaries in the protocols before comparing results. The paired experiments do not establish general capacity or absence of monitoring interference.

## Provenance and integrity

The single cited artifact snapshot fixes all reader-facing material. Detailed upstream identities and file checksums remain in manifests and campaign provenance for verification. These records do not require readers to choose between software revisions. The original measurements, unsuccessful outcomes and archived attempts are preserved.

The verification command checks the complete baseline source and bundle, all measured evaluation-module hashes, and every file listed by the included scientific trials' reproduction manifests. It does not run a new experiment.

Copyright and license notices for the included upstream source remain unchanged. See its [MIT license](third_party/cp2n2-v5.1.1/LICENSE).
