# CP²N² v5.0 release checklist

This checklist records the software and evidence release that accompanies the
journal manuscript. The software/evidence scope may be frozen and released
before the manuscript's final editorial and journal-template pass; the final
paper must cite this immutable release or a later explicitly versioned one.

## Release identity

The following identifiers must refer to one immutable revision:

- Git commit on `main`;
- annotated Git tag `v5.0`;
- GitHub release and source archive;
- `cp2n2-v5.0-source.zip` and `SHA256SUMS.txt`;
- Zenodo deposit and DOI;
- non-anonymous manuscript software/data citation;
- paper metrics and cited evaluation artifacts.

Do not build the candidate from uncommitted files, a local-only commit, or a
branch that has not been merged and pushed.

## Included scope

v5.0 contains the merged post-v4.0 work:

- the CP²N² naming migration and compatibility aliases;
- the BioPattern Gate E3 control-plane integration;
- the audited replay/web demo and user guide;
- platform-independent canonical audit-fixture hashing;
- the Agent-to-PNN v1.2 campaign package;
- the frozen University of Lübeck AI-Lab campaign results.

The release does not claim physical CL1 execution, biological performance, or
provider-approved Cortical Cloud deployment. Those require separately attested
E5 evidence.

## Freeze gate

- [x] The authoritative manuscript and software wording use the same evidence
  boundary; the release identity is recorded below for later non-anonymous
  paper binding.
- [ ] The paper revision is frozen for journal submission.
- [x] `main` contained all intended v5.0 changes and was synchronized with
  `origin/main`.
- [x] `git status --short` was empty when the release archive was built.
- [x] No secrets, local `.env` files, provider credentials, or unredacted
  prompts are tracked.
- [x] The complete automated test suite passes in the release environment.
- [x] The E3 BioPattern fixture and audit chain verify.
- [x] The frozen Agent-to-PNN campaign manifest, trial count, metrics, and
  audit verification match the manuscript.
- [x] `CHANGELOG.md`, `README.md`, and the project master plan reflect the
  frozen state.

## Build the candidate

From a clean `main` worktree:

```powershell
.\scripts\build_release_candidate.ps1 -Version v5.0
```

The script refuses a dirty or non-`main` worktree and creates an untracked
release directory containing the source ZIP, SHA-256 manifest, and
machine-readable release manifest. Review those files before tagging.

Record the final values here only after the gate passes:

| Field | Frozen value |
|---|---|
| Commit | `df8a322b79a505779c5c97323a57a15a58f7df66` |
| Test result | 142 passed |
| BioPattern E3 fixture verification | passed in the complete release suite |
| Agent campaign manifest | `evaluation/results/agent-to-pnn-ai-lab-20260730T100138Z/manifest.json` |
| Source ZIP SHA-256 | `8cc70dc0544661f44503d0b6c0144bf0ff73ea009ccb98f164446be99274e5c8` |
| GitHub release URL | `https://github.com/fischesn/cp2n2/releases/tag/v5.0` |
| Zenodo DOI | |

## Publish and bind

- [x] Create an annotated `v5.0` tag at the recorded commit.
- [x] Push the tag and publish the GitHub release with the generated archive
  and checksum files.
- [ ] Archive that exact release on Zenodo and verify its files and checksum.
- [ ] Add the Zenodo DOI and Git commit to the non-anonymous manuscript.
- [ ] Rebuild the final PDF and verify that the cited release, code, data,
  metrics, and evidence statements all resolve to the frozen revision.

If any published identifier or artifact differs, stop and issue a corrected
release rather than silently replacing files.
