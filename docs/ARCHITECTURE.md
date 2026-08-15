# EcomSRE-Agent Architecture

## Status and purpose

This document describes durable cross-phase system boundaries. Phase 0
canonical acceptance remains incomplete, while later replay, evaluation, and
the separately bounded LOCAL_DEMO successor have their own accepted decisions
and evidence. A green later-phase result does not rewrite Phase 0.

Decision references: `DEC-002`, `DEC-003`, `DEC-007`, `DEC-008`, `DEC-010`,
`DEC-011`, `DEC-031`, and `DEC-032` govern the historical paths described below.
`DEC-033` through `DEC-035` govern the separately versioned Diagnosis-to-Action
v2 contracts, admission, authorization, and bounded read-only runtime.

## Logical planes

| Plane | Responsibility | Authority |
|---|---|---|
| Environment | Frozen e-commerce services and observability backends | No agent authority |
| Scenario control | Inject, reset, and record controlled faults | Evaluator-only truth |
| Observation | Metrics, logs, traces, service state, and sanitized changes | Read-only |
| Diagnosis | Single-Agent or dynamic Multi-Agent investigation | Read-only |
| Remediation | Plan typed, bounded actions | No direct execution |
| Execution | Enforce policy and execute an allowlisted mutation | Restricted write |
| Verification | Check infrastructure and business SLO independently | Read-only plus rollback request |
| Evaluation | Score all experimental arms against hidden truth | External to the tested systems |

The authority boundary is normative; see
[SAFETY_BOUNDARIES.md](SAFETY_BOUNDARIES.md).

## End-to-end flow

```mermaid
flowchart LR
  A["Alert or SLO anomaly"] --> C["Incident Commander"]
  C --> D["Dynamic investigation DAG"]
  D --> M["Metrics Agent"]
  D --> L["Logs Agent"]
  D --> T["Traces Agent"]
  D --> H["Changes Agent"]
  M --> E["Versioned Evidence Store"]
  L --> E
  T --> E
  H --> E
  E --> J["RCA Judge"]
  J --> P["Remediation Planner"]
  P --> G["Deterministic Policy Gate"]
  G --> X["Restricted Executor"]
  X --> V["Independent Verifier"]
  V -->|pass| Z["Mitigation accepted"]
  V -->|fail| R["Compensating rollback"]
```

The diagram above is the historical Dynamic Multi-Agent path, not a claim that
every repository workflow uses it. Phase 1 contains a read-only Single-Agent
tool loop; Phase 2 contains Fixed and Dynamic Multi-Agent replay; Phase 3
contains replay-only restricted remediation; and the LOCAL_DEMO successor used
one Strong Single diagnosis plus one exact allowlisted local configuration
restoration. Its strict R3 diagnosis remained negative because of a fault-class
mismatch. Exact evidence and claim limits live in the result documents.

Diagnosis-to-Action v2 does not replace those paths. It adds the namespaced
offline contracts described in
[diagnosis-to-action-v2.md](design/diagnosis-to-action-v2.md). PR-B adds
deterministic admission, exact authorization records, and fake-only
Executor/Verifier transactions. PR-C adds five strict read adapters and a
separate full-run Evidence Store: fixed-query loopback Prometheus, OpenSearch,
and Jaeger adapters plus GET-only, exact-owned local Unix Docker runtime and
resource inspection. Production reads require an owned-lifecycle authority
capability issued only after fresh local-daemon re-authentication and bound to
the frozen configuration, a fresh resolve equal to the admitted Sandbox, exact
endpoints, and ownership labels. The full revalidated authority context and
canonical request resolver envelopes persist in the run-scoped store separately
from the diagnosis-cited view.
The observation plane never exposes raw container, trace, or span identities.
No v2 Agent, real Executor, Docker mutation,
Provider call, held-out result, or live acceptance is established by these
slices. Fresh authorized no-fault Smoke
`f8532f3a6ab5242ab5bba2f8ae1a6caf` closed the PR-C read-only gate
`PASS / CLEAN` with all five tools successful and all prohibited-action
counters zero; this does not establish live remediation or Live acceptance.

## Phase 0 environment boundary

The environment uses the read-only upstream submodule specified by `DEC-002`.
The preferred topology is the upstream `compose.yaml` plus
`compose.observability.yaml`. `compose.full.yaml`, agentic, profiling, extras,
Kubernetes, and private topology rewrites are excluded by `DEC-003`.

Project-owned wrappers may provide:

- resource namespacing and ownership labels;
- frozen environment overrides and image digest selection;
- lifecycle and evidence orchestration;
- programmatic probes and query fixtures.

They must not modify upstream source or change upstream behavior to conceal an
unsupported environment.

## Stable interface between Phase 0 and later phases

Phase 0 must produce a versioned run bundle rather than expose backend-specific
responses as the project API. The stable envelope contains:

- opaque run and scenario-instance references;
- source, service, time window, and scenario phase;
- observation versus inference type;
- query or probe identity and version;
- raw artifact reference and content hash;
- completeness, limitations, and freshness metadata.

Raw Prometheus, Jaeger, and OpenSearch responses remain immutable attachments.
Phase 1 tools normalize them into the Evidence Contract. Query fixtures are
frozen to OTel Demo 3.0.0 `demo.*` telemetry.

## Trust zones

Observer-visible artifacts and evaluator-only artifacts are separate roots.
The observer zone may contain sanitized change records, telemetry, probes, and
environment manifests. It must not contain feature-flag keys or values,
scenario names, expected answers, or semantic paths that reveal ground truth.

The evaluator zone maps opaque references to exact injections and structured
answers. The external evaluator scores every architecture, including the
internal RCA Judge output.

## Dynamic Multi-Agent criteria

Phase 2 is dynamic only if the Commander can create, revise, stop, and
checkpoint investigation tasks based on evidence and budgets. Specialist
agents receive scoped tool contexts, not a shared dump of raw telemetry.
Hypotheses are adjudicated using supporting, contradicting, and missing
evidence; voting alone is insufficient.

Multi-agent value is an empirical result, not an architectural assumption.
`DEC-010` and `DEC-011` govern the comparison.

## Persistence

- SQLite stores events, state transitions, checkpoints, and compact structured
  state.
- JSON/JSONL stores large evidence and immutable run artifacts.
- Artifacts carry schema versions and hashes.
- Replay consumes captured tool responses and state transitions without
  exposing evaluator-only truth.

The original Phase 1 schemas and the independent `dta-v2.*` schemas are
versioned separately. Remaining observation-owned decisions are tracked in
[OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).
