# EcomSRE-Agent Open Questions

## Classification

No item below is waiting for another user product decision before Phase 0 can
start after explicit authorization.

`OQ-001` through `OQ-004` are `phase0_closure_required`: the Phase 0 implementer
must resolve them from the frozen environment and preserve the listed evidence.
They do not block starting implementation, but any open item blocks canonical
Phase 0 `SUCCESS`.

`OQ-005` through `OQ-008` are `deferred` later-phase questions. They do not
expand or block Phase 0.

## Phase 0 closure-required items

### OQ-001 — Validated runtime fingerprint

- **State:** `phase0_closure_required`
- **Owner:** Phase 0 implementer
- **Resolution method:** Run the non-interactive preflight on the accepted
  Apple Silicon/Docker Desktop baseline and automatically detect the macOS,
  Docker client/server/Desktop/engine, Compose, native architecture, CPU,
  memory, disk, Docker allocation, ports, and existing related resources. Do
  not request manual version entry or modify the host.
- **Evidence required:** Machine manifest, environment manifest, raw preflight
  command log, detected values, ownership/collision report, and content hashes.
- **Blocking condition:** The supported native `linux/arm64` environment,
  required resources, or safe ownership boundary cannot be established.
- **Closed when:** One supported preflight result is preserved as the first
  validated runtime fingerprint and a reviewer can reproduce its determination
  from the evidence.

### OQ-002 — Frozen OTel 3.0.0 telemetry/query fixtures

- **State:** `phase0_closure_required`
- **Owner:** Phase 0 implementer
- **Resolution method:** Inspect only the frozen OTel Demo 3.0.0 commit and its
  emitted telemetry. Identify the exact `service.name`, `demo.*` metric names,
  labels, log fields, trace operations, and window-local query needed for the
  `GetAds` attempt denominator and errors. Do not restore legacy `app.*` names
  or patch upstream.
- **Evidence required:** Raw Prometheus, Jaeger, and OpenSearch queries and
  responses; emitted identity samples; query fixture version and hash; the
  mapping from raw counters/events to attempts and errors.
- **Blocking condition:** The denominator, error count, freshness window, or Ad
  service attribution cannot be derived without ambiguity.
- **Closed when:** The fixture set is frozen, hashed, reviewed against raw
  current-run data, and can produce the acceptance statistics without hidden
  truth.

### OQ-003 — Independent deterministic request probe

- **State:** `phase0_closure_required`
- **Owner:** Phase 0 implementer
- **Resolution method:** Select and document a local request path and response
  contract that exercises the relevant business path. The probe must be
  non-interactive, repeatable, and independent of flagd state, feature-flag
  identifiers, scenario truth, and evaluator-only artifacts.
- **Evidence required:** Probe contract, sanitized command log, raw requests
  and responses, timestamps, exit semantics, current-run attribution, and
  evidence that its inputs do not read the hidden-truth zone.
- **Blocking condition:** The probe requires UI clicks, evaluator data, direct
  flag-state inspection, an external service, or cannot distinguish a valid
  business-path response.
- **Closed when:** The frozen probe contract produces attributable current-run
  observations across baseline, fault, and recovery without becoming the
  Prometheus statistical denominator.

### OQ-004 — No undeclared external runtime dependency

- **State:** `phase0_closure_required`
- **Owner:** Phase 0 implementer
- **Resolution method:** Run acceptance with cached digest-locked images and
  `--pull never` or an equivalent policy; perform no package installation or
  code fetch; depend on no external registry, package index, or API; record
  Docker pull behavior, executed commands, and dependency operations.
- **Evidence required:** Image-cache and digest verification, resolved Compose
  input, command log, pull/dependency audit records, and a run report stating
  that no undeclared external runtime dependency access was initiated or
  observed.
- **Blocking condition:** A registry pull, package install, code fetch,
  undeclared external API dependency, or incomplete dependency audit occurs
  during the canonical run.
- **Closed when:** A canonical run completes using only frozen repository
  inputs and local cached images, and the recorded command/pull/dependency
  evidence contains no initiated or observed undeclared external runtime
  dependency.

This item does not require a cryptographic proof that macOS emitted no network
packet. It requires a bounded, auditable execution contract and evidence about
the project commands and dependencies under test.

## Later-phase deferred items

| ID | State | Owner phase | Question | Blocks Phase 0? |
|---|---|---|---|---|
| OQ-005 | deferred | Phase 1 | Which model snapshot, provider, tokenizer accounting method, and concrete token/tool budgets implement `DEC-010`? | No |
| OQ-006 | deferred | Phase 1 | What exact versioned Evidence Contract schema and migration policy implement the later read-only tool boundary? | No |
| OQ-007 | deferred | Phase 5 | What final 12+ scenario templates, hidden split, paired seeds, and preregistered analysis version implement `DEC-011`? | No |
| OQ-008 | deferred | Phase 3 | What action schemas, allowlist entries, preconditions, rollback contracts, and human-approval interface implement `DEC-012`? | No |

## Resolution rules

- Resolve `OQ-001` through `OQ-004` from actual frozen evidence, not by
  weakening `DEC-001` through `DEC-008`.
- A reproducible incompatibility with `DEC-001` or `DEC-002` requires a new
  Decision Record and `BLOCKED_ENVIRONMENT` or `BLOCKED_UPSTREAM`.
- Closing an item updates this file and every affected fixture or contract
  version.
- Deferred items cannot become hidden dependencies of Phase 0.
- No item may be closed from a UI screenshot or prose assertion alone.
