# Phase 0 Acceptance Contract

## Status

This is a normative interface and evidence contract, not an implementation.
None of the commands below exists yet, and this document does not authorize
running Docker.

This contract implements `DEC-001` through `DEC-008`. `DEC-009` through
`DEC-012` are later-phase decisions and are not Phase 0 dependencies. Phase 0
non-goals are owned by [PROJECT_CHARTER.md](PROJECT_CHARTER.md).

Phase 0 passes only when one canonical acceptance run completes three
consecutive valid cycles and all environment, isolation, reproducibility, and
telemetry gates pass.

## Expected command interface

| Command | Network | Purpose | Primary output |
|---|---|---|---|
| `make phase0-bootstrap` | allowed | Initialize exact submodule, obtain locked images, and verify ARM64 digests | bootstrap report |
| `make phase0-preflight` | no external dependency required | Inspect the machine, Docker allocation, cached inputs, conflicts, and ownership | machine/environment manifests |
| `make phase0-up RUN_ID=<id>` | forbidden for pulls | Start only the frozen, namespaced environment | lifecycle event and resource manifest |
| `make phase0-health RUN_ID=<id>` | local only | Check service, load, Collector, and three-signal readiness | readiness report |
| `make phase0-inject RUN_ID=<id>` | local only | Apply the one allowlisted fault and write separated change/truth records | control acknowledgement |
| `make phase0-reset RUN_ID=<id>` | local only | Restore the frozen baseline without deleting evidence | reset acknowledgement |
| `make phase0-status RUN_ID=<id>` | local only | Report state without mutation | status report |
| `make phase0-accept` | local only | Orchestrate a canonical run and three cycles | acceptance report |
| `make phase0-stop RUN_ID=<id>` | local only | Stop proven project-owned resources | stop report |

`phase0-accept` generates `RUN_ID` unless one is supplied. IDs must be opaque,
non-semantic, collision-resistant, and safe as path components.

Canonical thresholds come from a future versioned repository configuration.
Diagnostic overrides are permitted only when explicitly marked
`NON_CANONICAL`; they cannot produce Phase 0 `SUCCESS`.

### Command contract

Every command must be:

- single-purpose and non-interactive;
- safe to invoke repeatedly without duplicating a mutation or overwriting a
  prior run;
- explicit about inputs, outputs, and the active `RUN_ID`;
- fail closed on unknown state, ownership, input, or dependency;
- implemented without manual flagd UI interaction;
- limited to project-owned resources, with no broad Docker cleanup.

Configuration and environment variables replace prompts or UI clicks. A repeat
of `phase0-inject` or `phase0-reset` for the same run must report the current
known state rather than apply a second uncontrolled mutation. A repeat of
`phase0-accept` creates a new run unless the caller explicitly resumes a
checkpointed, ownership-verified run; it never replaces prior evidence.

### Exit-code and outcome protocol

| Exit code | Outcome | Meaning |
|---|---|---|
| `0` | `SUCCESS` | The requested command completed; for `phase0-accept`, every Phase 0 gate passed |
| `20` | `BLOCKED_ENVIRONMENT` | Supported host, Docker allocation, port, or resource prerequisites are not satisfied |
| `21` | `BLOCKED_UPSTREAM` | Frozen source, ARM64 image, digest, Compose input, or query-fixture prerequisite is unavailable or mismatched |
| `30` | `FAILED_ACCEPTANCE` | The environment ran, but a statistical, telemetry, evidence, or cleanup acceptance condition failed |
| `40` | `UNSAFE` | The requested action violates ownership or a permanent safety boundary |
| `41` | `MANUAL_INTERVENTION_REQUIRED` | Safe state or cleanup cannot be established automatically |
| `64` | `INVALID_INVOCATION` | Required arguments or configuration are invalid; no acceptance outcome is produced |

Subcommand `SUCCESS` means that subcommand completed, not that Phase 0 passed.
Only a zero exit from `phase0-accept` with a finalized acceptance report means
Phase 0 `SUCCESS`.

## Frozen canonical inputs

| Input | Value |
|---|---|
| Upstream tag | `3.0.0` |
| Upstream commit | `1755859a9de82c2e5e225be68abc401a5ebf2b4f` |
| Platform | native `linux/arm64` |
| Compose layers | `compose.yaml`, `compose.observability.yaml` |
| Stabilization | 30 seconds |
| Minimum attempts per measured window | 200 observed `GetAds` attempts |
| Maximum measured-window wait | 180 seconds |
| Baseline error rate | ≤1% |
| Fault error rate | 5%–20% |
| Recovery error rate | ≤1% |
| Consecutive cycles | 3 |
| Control acknowledgement timeout | 30 seconds |

Stabilization is configurable through versioned, recorded input. Thirty seconds
is the canonical default. A different value marks the run `NON_CANONICAL`
unless a later Decision Record changes the canonical value.

The command acknowledgement proves only that the controller accepted an
inject/reset request. Incident effect and recovery are proved only by the
subsequent probe and Prometheus window.

## Canonical single-run sequence

The orchestrator performs the following non-interactive sequence:

1. preflight;
2. environment startup;
3. service and initial telemetry readiness;
4. stabilization;
5. baseline measurement;
6. fault injection;
7. stabilization;
8. fault measurement;
9. reset;
10. stabilization;
11. recovery measurement;
12. final three-signal telemetry readiness;
13. evidence finalization and content hashing;
14. environment shutdown, or predeclared failure-scene preservation.

Steps 4–11 form one cycle and repeat three times. Before each repetition, the
orchestrator revalidates readiness without reusing historical samples. Each
cycle is independently evaluated; a later successful cycle cannot repair or
hide an earlier failed cycle.

The default is safe environment shutdown after evidence finalization. Scene
preservation is allowed only when configured before the run, only for a failed
run, and only while ownership remains proven. It never changes the failure
outcome and leaves an ownership manifest and manual shutdown command in the
report. A successful canonical run must shut down its project-owned
environment.

## Step contract

### 1. Bootstrap

Inputs:

- exact submodule URL, tag, and commit;
- committed image digest lock;
- supported platform.

Outputs:

- checked-out commit verification;
- image index and resolved `linux/arm64` digest verification;
- pull and cache events;
- bootstrap status.

Failure includes a missing/mismatched digest, unavailable ARM64 image, upstream
commit mismatch, floating image reference, or undeclared source.

### 2. Preflight

Inputs:

- cached frozen inputs;
- stable project namespace;
- required ports and minimum host envelope.

Outputs:

- macOS version/build and architecture;
- chip architecture and available CPU;
- total and available memory;
- available disk;
- Docker client/server, Desktop/engine, and Compose versions;
- Docker CPU, memory, and disk allocation;
- existing relevant containers, networks, volumes, ports, locks, and process
  metadata;
- supported/unsupported result with actionable diagnostics.

Preflight performs no installation, upgrade, host mutation, resource takeover,
or cleanup.

### 3. Start and readiness

Inputs:

- resolved frozen Compose configuration;
- `--pull never` or equivalent;
- ownership labels and run metadata.

Outputs:

- resolved Compose content hash;
- exact resource ownership manifest;
- declaration of whether any pull or external dependency access occurred;
- service and dependency health;
- load-generator readiness;
- Collector pipeline readiness;
- fresh Prometheus, Jaeger, and OpenSearch checks.

Container `Running` or `healthy` alone is insufficient.

### 4. Execute each cycle

Each cycle is:

1. readiness;
2. 30-second stabilization;
3. baseline window;
4. inject and acknowledge within 30 seconds;
5. 30-second stabilization;
6. fault window;
7. reset and acknowledge within 30 seconds;
8. 30-second stabilization;
9. recovery window.

Each measured window ends when 200 valid observed `GetAds` attempts are
available, or fails at 180 seconds. The denominator is not homepage requests,
k6 iterations, or total HTTP traffic.

For every window, save:

- UTC start/end and monotonic duration;
- attempt and error counts;
- error rate;
- 95% Wilson confidence interval;
- exact query fixture version;
- raw query/probe response and hash.

Use only window-local delta or rate data. Samples from preceding windows must
not be included.

Prometheus is the primary source for the `GetAds` attempt denominator, error
count, and threshold calculation. A deterministic local request probe supplies
an independent business-path observation. The probe must not read flagd state,
feature-flag identifiers, scenario truth, or evaluator-only artifacts.

Every baseline, fault, and recovery window is judged independently. A failed
window fails its cycle; a failed cycle fails the run. All failed-cycle inputs,
queries, responses, statistics, and command logs are retained. Reporting only
successful reruns is prohibited.

### 5. Telemetry readiness

Prometheus must have current-window Ad call/error data. Jaeger must contain a
new current-run trace with an Ad-related span. OpenSearch must contain a new
current-run Ad log, not a previous-run residue.

Correlation requires service identity, run time window, and scenario phase.
Use trace/request correlation when upstream provides it; sampling differences
do not require all three backends to contain the same request.

All three backends must prove that the selected records were generated during
the active run. Historical Prometheus samples, old Jaeger traces, and retained
OpenSearch logs cannot satisfy readiness or become current-run evidence.

The human name “Ad Service” does not override upstream telemetry. Exact emitted
`service.name` and metric names must be captured from the frozen 3.0.0
baseline, placed in query fixtures, and then treated as immutable.

### 6. Evaluate and stop

The external deterministic evaluator checks thresholds, freshness, isolation,
input hashes, prohibited network activity, and evidence completeness. The run
then stops only proven project-owned resources.

Failed runs remain intact. A rerun creates a new `RUN_ID`; it does not replace
the failed record.

## Pass conditions

All are required:

- supported preflight;
- exact upstream commit and locked ARM64 image digests;
- no ownership or port conflict;
- no pull, install, update, upstream fetch, or undeclared external dependency
  during acceptance;
- resolved Compose hash recorded;
- three-signal telemetry readiness in the current run;
- three consecutive cycles, each meeting the sample limits and all three error
  thresholds;
- inject and reset acknowledgements within their limits;
- complete observer/evaluator separation and evidence hashes;
- `OQ-001` through `OQ-004` closed with their required evidence;
- safe project-scoped stop.

The sole passing outcome is `SUCCESS`. Terms such as “mostly passed,”
“basically passed,” or “passed except for” are invalid.

## Failure conditions

Examples include:

- `ENVIRONMENT_UNSUPPORTED`;
- `PREFLIGHT_BLOCKED`;
- `RESOURCE_CONFLICT`;
- `RESOURCE_OWNERSHIP_UNKNOWN`;
- `INPUT_NOT_FROZEN`;
- `ARM64_DIGEST_MISMATCH`;
- `EXTERNAL_DEPENDENCY_DETECTED`;
- `TELEMETRY_NOT_READY`;
- `WINDOW_SAMPLE_TIMEOUT`;
- `BASELINE_THRESHOLD_FAILED`;
- `FAULT_THRESHOLD_FAILED`;
- `RECOVERY_THRESHOLD_FAILED`;
- `INJECT_TIMEOUT`;
- `RESET_TIMEOUT`;
- `EVIDENCE_INCOMPLETE`;
- `CLEANUP_INCOMPLETE`.

Every failure preserves evidence and maps to one of the terminal outcomes
below. Threshold relaxation and selective run deletion are forbidden.

## Stop conditions

| Outcome | Stop condition |
|---|---|
| `SUCCESS` | Every pass condition is met, evidence is finalized, and the owned environment is shut down |
| `BLOCKED_ENVIRONMENT` | The supported host/Docker envelope, resource allocation, port availability, or safe project namespace is unavailable before valid measurement |
| `BLOCKED_UPSTREAM` | The frozen commit, ARM64 image/digest, Compose input, or required frozen query fixture cannot be verified |
| `FAILED_ACCEPTANCE` | The environment ran but a sample, threshold, telemetry freshness, evidence, or required shutdown condition failed |
| `UNSAFE` | Ownership cannot be proven, a prohibited action is requested/observed, or continuing would cross a permanent safety boundary |
| `MANUAL_INTERVENTION_REQUIRED` | Safe state, rollback/reset, scene preservation, or cleanup cannot be established automatically |

Stop the active run without another fault mutation when:

- resource ownership becomes uncertain;
- an unknown resource or port conflict appears;
- a frozen input or digest changes;
- an undeclared pull, install, update, or external dependency is detected;
- any telemetry backend cannot provide fresh attributable data;
- a controller action times out or state becomes unknown;
- a measured window times out;
- evidence cannot be written or hashed safely;
- the user requests stop.

Stopping may reset the one allowlisted fault and stop proven project resources.
It may not delete evidence or broaden cleanup. Unsafe cleanup ends with
`MANUAL_INTERVENTION_REQUIRED`.

## Required evidence contents

| Evidence | Required contents |
|---|---|
| Run identity | `run_id`, opaque scenario-instance reference, canonical/non-canonical state, start/end times, final outcome |
| Machine manifest | Detected macOS/architecture, CPU, memory, disk, Docker client/server/Desktop/engine, Compose, and Docker resource allocation |
| Environment manifest | Owned resources and ports, startup/readiness state, external-runtime-dependency observations, and shutdown/preservation state |
| Frozen inputs | Upstream tag and commit, committed image digest lock plus resolved ARM64 digests, and resolved Compose configuration hash |
| Phase record | Scenario phase, cycle number, UTC and monotonic time window, probe/query fixture versions, and freshness boundaries |
| Query evidence | Raw query text or request, raw response, backend, timestamps, exit/status code, and content hash |
| Statistical evidence | Valid `GetAds` attempts, errors, rate, 95% Wilson interval, threshold decision, and sample timeout state |
| Command log | Command, sanitized arguments, working directory, start/end time, exit code, outcome, and referenced output artifacts |
| Final report | Per-cycle decisions, telemetry gate decisions, overall acceptance decision, failure reason codes, and environment disposition |
| Integrity | Content hash for every immutable evidence object plus the final checksum manifest |

Command logs must not contain secrets. Raw queries and responses are retained
even when parsing or aggregation fails.

## Evidence layout

```text
artifacts/phase0/
├── bootstrap/<bootstrap_id>/
│   ├── bootstrap-report.json
│   └── image-verification.json
├── observer-visible/<run_id>/
│   ├── run-manifest.json
│   ├── machine-manifest.json
│   ├── environment-manifest.json
│   ├── resource-ownership.json
│   ├── inputs/
│   │   ├── upstream.json
│   │   ├── image-digests.json
│   │   ├── compose.sha256
│   │   └── query-fixtures.json
│   ├── changes/changes.jsonl
│   ├── dependency-audit/
│   │   ├── pulls.jsonl
│   │   └── external-access.jsonl
│   ├── commands/commands.jsonl
│   ├── lifecycle/events.jsonl
│   ├── cycles/01..03/
│   │   ├── baseline/
│   │   ├── fault/
│   │   └── recovery/
│   └── telemetry/
│       ├── prometheus/
│       ├── jaeger/
│       └── opensearch/
├── evaluator-only/<run_id>/
│   ├── scenario-ground-truth.json
│   ├── control-events.jsonl
│   └── expected-outcome.json
└── reports/<run_id>/
    ├── acceptance-report.json
    ├── failure-report.json
    └── checksums.sha256
```

Observer-visible manifests use only opaque scenario/change references. The
evaluator-only tree must not be reachable by future agent tools.
