# EcomSRE-Agent Roadmap

No calendar dates are committed. A phase starts only after its entry gate is
satisfied and explicitly authorized.

## Planning and decision freeze

**State:** accepted and complete.

Outputs:

- `DEC-001` through `DEC-012`;
- project charter, architecture, roadmap, safety, acceptance, and open-question
  documents;
- no production implementation.

Stop after documentation. Do not infer authorization for Phase 0.

## Phase 0 — Local deterministic control and statistical fault loop

**State:** not started.

Entry:

- explicit user authorization;
- no unresolved item classified as blocking Phase 0 implementation.

Scope:

- frozen OTel Demo 3.0.0 environment;
- bootstrap, preflight, lifecycle, health, inject, reset, and evidence commands;
- `adServiceFailure` control with a statistical Ad `GetAds` proxy SLI;
- Prometheus incident oracle and three-signal telemetry readiness;
- no LLM or write remediation.

Phase 0 is governed by `DEC-001` through `DEC-008`. Its complete non-goals are
owned by [PROJECT_CHARTER.md](PROJECT_CHARTER.md); later-phase decisions do not
expand this scope.

Exit:

- every condition in [PHASE_0_ACCEPTANCE.md](PHASE_0_ACCEPTANCE.md) passes for
  one canonical run containing three consecutive cycles;
- evidence is preserved and independently reviewable.

## Phase 1 — Read-only tools and Single-Agent baseline

**State:** deferred.

Entry:

- Phase 0 accepted;
- Evidence Contract and structured RCA schema from `DEC-009` frozen;
- model/provider snapshot and read boundaries frozen.

Scope:

- Metrics, Logs, Traces, Service State, and sanitized Changes tools;
- observer/evaluator separation;
- Single-Agent RCA only;
- no remediation writes.

Exit:

- tools have deterministic fixtures and provenance;
- Single-Agent produces structured RCA under a recorded budget;
- leakage and replay tests pass.

## Phase 2 — Dynamic Multi-Agent diagnosis

**State:** deferred.

Entry:

- Phase 1 baseline frozen;
- equal-budget protocol from `DEC-010` operational.

Scope:

- Commander and dynamic investigation DAG;
- specialist agents, Evidence Store, and RCA Judge;
- timeout, retry, budget, termination, checkpoint, and replay;
- Single-Agent and Fixed Workflow comparison.

Exit:

- dynamic behavior and authority separation are demonstrated;
- paired results and required diagnosis ablations are available;
- no superiority claim is made before Phase 5.

## Phase 3 — Restricted remediation

**State:** deferred.

Entry:

- diagnosis contracts stable;
- exact allowlist and human-approval interface frozen;
- safety tests for `DEC-012` pass.

Scope:

- Remediation Planner;
- deterministic Policy Gate;
- Restricted Executor;
- independent Verifier and compensating rollback.

Exit:

- unsafe and uncertain states fail closed;
- rollback remains available after the one-forward-mutation limit;
- no action can escape project-owned local resources.

## Phase 4 — Search, ads, and recommendation extensions

**State:** deferred.

Scope:

- Feature Service and Ranking Service;
- feature freshness lag;
- model-feature schema mismatch;
- bad ranking configuration;
- Search, Ads, and Recommendation SLOs.

These components do not retroactively expand Phase 0 claims.

## Phase 5 — Frozen evaluation and demonstration

**State:** deferred.

Entry:

- at least 12 scenario templates are frozen as required by `DEC-011`;
- hidden templates remain unexposed;
- all experimental arms and budgets are versioned.

Scope:

- paired Single-Agent, Fixed Workflow, and Multi-Agent runs;
- RCA, mitigation, unsafe-action, MTTD, MTTR, token, compute, and wall-clock
  metrics;
- required ablations and bootstrap confidence intervals;
- incident timeline and reproducible demo.

Exit:

- unblinded results are immutable;
- claims are limited to the frozen suite;
- any later retuning creates a new evaluation version.
