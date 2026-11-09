# Cortical Cloud Onboarding Questions for the CP2N2 Pilot

## Experiment summary

CP2N2 is a control plane for heterogeneous physical neural network resources.
Its BioPattern Gate case study asks an agent to request a high-level,
server-owned assay preset. CP2N2 performs admission, approval, exclusive
leasing, lifecycle control, provenance capture, result validation, and
sanitization. The installed CL application presents two charge-matched
spatiotemporal input patterns to a CL1, records the response after an explicit
blanking interval, applies a frozen linear readout, and returns a compact gate
decision.

The local package already passes the official CL SDK Simulator application
runner and packager. The requested pilot would first perform a no-stimulation
installation smoke test and then the smallest provider-approved technical run.

## Access and execution

1. Is Cortical Cloud application access browser-only, SDK-mediated,
   CLI-mediated, available through a documented HTTP API, or some combination
   of these?
2. Is there an official Python or CLI client for application upload,
   installation, configuration selection, launch, status, and removal?
3. Can an external control plane start and monitor an installed application,
   or must every run be initiated interactively in the Cloud UI?
4. May a project run a service such as an MCP server inside its Cloud
   environment, and if so, what inbound and outbound networking is supported?

## Authentication and authorization

5. Which supported mechanism is intended for unattended research automation:
   API key, OAuth client, service account, short-lived workload identity, CLI
   login, or another method?
6. How are credentials scoped, rotated, revoked, and attributed in audit
   records?
7. Are project or service identities available, or must each operation be
   attributed to an interactive user?
8. Which permissions separately govern package management, reservation,
   execution, abort, recording access, and artifact export?

## Resource lifecycle

9. How are available CL1 resources, projects, chips, cultures, or cell batches
   identified and selected?
10. Is resource access exclusive for an application run, and what provider
    identifier represents the allocation or reservation?
11. What queue, reservation, start, status, terminal-state, and cancellation
    interfaces are available?
12. What is the authoritative safe-abort operation, and what state follows a
    timeout, client disconnect, application exception, or lost response?
13. Is there a reconciliation interface for determining whether an uncertain
    start or abort actually took effect?

## Application deployment

14. How is a `cl.app.pack` ZIP uploaded, installed, versioned, verified,
    selected, and removed?
15. Are `requirements.txt` dependencies permitted, and how does the on-device
    `cl-api` package consolidate with a declared `cl-sdk` requirement?
16. Are application configuration presets managed inside the ZIP, in the
    Cloud UI, through an API, or in all three places?
17. Does the Cloud runtime expose a stable provider run ID and the exact
    package/configuration hashes used for a run?

## Safety and experimental protocol

18. Which stimulation designs, channel groups, mapping procedures, blanking
    intervals, session durations, trial counts, cooldowns, and abort rules are
    approved for the initial pilot?
19. Which health, calibration, culture-age, activity, contention, or
    recording-readiness fields are available before admission?
20. Must channel mapping be repeated for every culture or session, and which
    minimum activity or quality criteria does Cortical Labs recommend?
21. Are sham trials and the proposed order-reversal pattern
    `I0 -> I1` versus `I1 -> I0` acceptable?
22. Who provides the final technical and biological approval for the exact
    preset, and how should that approval be referenced in the run record?

## Recording, provenance, and export

23. May the pilot record spikes, stimulation events, application data streams,
    and raw samples? Is raw-sample recording disabled by default for bundled
    applications?
24. Can the native HDF5, `result.json`, `summary.md`, and additional output
    files be exported programmatically?
25. What are the artifact retention period, size limits, download mechanism,
    and deletion policy?
26. Which metadata fields reliably identify the system, project, chip, cell
    batch, package, configuration, allocation, and provider run?
27. Does Cortical Labs provide signed or otherwise verifiable runtime
    attestation, or should `cl.is_simulator()`, `cl.get_system_attributes()`,
    recording attributes, and provider run metadata be treated only as
    provenance?

## Publication and support

28. Are the planned recording export, open-source application package,
    methods description, screenshots, aggregate results, and publication
    permitted under the grant terms?
29. What attribution, review, acknowledgement, or pre-publication notice is
    required?
30. Which technical support channel should be used during the scheduled
    access slot, and can a short no-stimulation setup session occur before the
    biological pilot?
