# CP²N² v5.1.0 release checklist

This checklist records the provider-facing Cortical Labs SDK Simulator
integration release. It supplements, rather than replaces, the frozen v5.0
software and paper evidence baseline.

## Release identity

The following identifiers must refer to one immutable revision:

- Git commit on `main`;
- annotated Git tag `v5.1.0`;
- GitHub release and source archive;
- `cp2n2-v5.1.0-source.zip` and `SHA256SUMS.txt`;
- any later Zenodo deposit and manuscript software citation.

Do not build the candidate from uncommitted files, a local-only commit, or a
branch that has not been pushed.

## Included scope

v5.1.0 adds:

- a documented CL API execution port for BioPattern Gate;
- atomic stimulation plans and validation of observed stimulation
  acknowledgements;
- a stimulation-responsive CL SDK Simulator source;
- native complete and aborted HDF5 recordings;
- independent online/offline feature and decision verification;
- HDF5-safe visualization streams plus native spike and stimulation streams;
- a versioned technical execution guide and Cloud onboarding question set;
- refreshed MCP source binding, audit trace, replay fixture, package manifest,
  and distributable CL application.

The release does not claim physical CL1 execution, biological performance,
provider-approved stimulation parameters, or an implemented Cortical Cloud
authentication and lifecycle contract. Those remain separately gated E5 work.

## Freeze gate

- [x] `main` contains the intended implementation and documentation.
- [x] No credentials, local `.env` files, or provider secrets are tracked.
- [x] The complete headless test suite passes with 146 tests.
- [x] The CL application package passes the official validator and packager.
- [x] The local CL application runner completes the frozen 14-trial E3
  configuration with exact online/offline HDF5 agreement.
- [x] Complete and cooperatively aborted native HDF5 recordings are tested.
- [x] The refreshed MCP audit chain and replay/demo bundle verify.
- [x] `CHANGELOG.md`, `README.md`, and the project master plan preserve the E3
  evidence boundary.

## Build the candidate

From a clean, pushed `main` worktree:

```powershell
.\scripts\build_release_candidate.ps1 -Version v5.1.0
```

Record the immutable publication values after the release is created:

| Field | Frozen value |
|---|---|
| Commit | `fbbe726d0ce86c0b5d967b3b3568b861c006156d` |
| Test result | 146 passed |
| BioPattern E3 fixture verification | passed in the complete release suite |
| Native CL HDF5 verification | complete and cooperative-abort paths passed |
| Source ZIP SHA-256 | `6ce4a9da9e8fcf4f904228fa1b5d7508f6cf03a8d2adc47417008a030d165b88` |
| GitHub release URL | `https://github.com/fischesn/cp2n2/releases/tag/v5.1.0` |
| Zenodo DOI | |

## Publish and bind

- [x] Create an annotated `v5.1.0` tag at the recorded commit.
- [x] Push the tag and publish the GitHub release with the generated archive,
  checksum file, and release manifest.
- [x] Record the immutable commit, archive hash, and release URL above on
  post-release `main`.
- [ ] If this version is cited by the paper, archive the exact release on
  Zenodo and bind its DOI and commit to the non-anonymous manuscript.

If any published identifier or artifact differs, stop and issue a corrected
release rather than silently replacing files.
