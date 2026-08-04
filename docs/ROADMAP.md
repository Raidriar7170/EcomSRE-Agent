# EcomSRE-Agent Roadmap

No calendar dates are committed. A phase starts only after its entry gate is
satisfied and explicitly authorized.

## Planning and decision freeze

**State:** accepted and complete.

Outputs:

- `DEC-001` through `DEC-012`;
- project charter, architecture, roadmap, safety, acceptance, and open-question
  documents;
- the accepted planning packet.

That planning-only boundary has ended through explicit bounded Phase 0
authorization; it does not authorize work beyond the current repair prompt.

## Phase 0 — Local deterministic control and statistical fault loop

**State:** bounded repair ended `UNSAFE`; `REVIEW_REQUIRED`.

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

The single authorized `NON_CANONICAL` smoke was consumed and terminated
`UNSAFE` before readiness or measurement. Its later authenticated environment
stop does not satisfy this phase exit gate, close `OQ-002` through `OQ-004`, or
authorize Phase 1. The real preflight evidence independently closes `OQ-001`.
A second smoke requires a new explicit bounded task.

## Phase 1 — Read-only tools and Single-Agent baseline

**State:** frozen local replay baseline implemented and verified.

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

**State:** local offline implementation and 7 x 3 comparison complete; fresh
Phase 2, frozen Phase 1, and repository-wide tests pass. The final read-only
review's three Must Fix findings are repaired and focused verified locally;
fresh repository-wide `FINAL_CLOSURE` evidence is verified. This is not a
reviewer re-pass, release, or a
superiority claim.

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

**State:** complete for the accepted replay-only MVP (`DEC-025`); live
integration has not been entered.

Completion marker:

`PHASE3_RESTRICTED_REMEDIATION_REPLAY_MVP_READY`

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

This exit applies only to deterministic offline replay. It does not authorize
Docker, provider calls, live telemetry, live mutation, Phase 4, or publication.

## Phase 4 — Search and recommendation domain replay extension

**State:** `PHASE4_OFFLINE_ECOMMERCE_DOMAIN_REPLAY_MVP_READY` under accepted
`DEC-026`. The deterministic offline implementation, ten-run evaluation, demo,
regressions, and Draft PR CI pass. The real-provider gate is not configured and
is recorded as `SKIPPED_NOT_CONFIGURED`; Phase 5A does not alter this result.

Entry:

- PR #3 merged to `main`;
- Phase 1–3 contracts and replay semantics remain frozen;
- `DEC-026` accepted.

Scope:

- five new visible Search/Recommendation replay templates;
- Feature and Ranking root-service evidence;
- feature freshness lag, model-feature schema mismatch, and ranking
  configuration failure;
- Fixed Specialist and Dynamic Multi-Agent workflows over the existing Phase 2
  runtime;
- deterministic ten-run offline evaluation, one-command domain demo, and an
  optional bounded four-run real-provider gate;
- no new Agent, live service, remediation action, or superiority claim.

Exit:

- the five cases run through both variants and retain all ten traces;
- Domain RCA, evidence, DAG, isolation, and budget checks verify;
- new mechanisms yield only `NO_ACTION` or `NO_SUPPORTED_REMEDIATION` at the
  Phase 3 boundary;
- Phase 1 fingerprints and Phase 2 comparison semantics remain unchanged.

These components do not retroactively expand Phase 0 claims.

## Phase 5A — Multi-Agent diagnosis quality repair

**State:** `PHASE5A_MULTI_AGENT_QUALITY_REPAIR_READY` under accepted `DEC-027`.
This is a visible development evaluation. Phase 5B has not been entered.

Entry:

- Phase 4 merge commit `8d9bb8a1e6e173aa795ac5f2ff541c29302ae691` is the baseline;
- Phase 1/2/3/4 contracts, reports, truth, and safety boundaries remain frozen;
- `DEC-027` accepted.

Scope:

- mechanism-level typed candidates and Specialist findings;
- typed empty, unavailable, and query-failed source behavior;
- capability-parity Single, Fixed, and Dynamic v2 workflows;
- 12 public templates × 3 variants with all 36 runs retained;
- metamorphic anti-hardcoding tests and an optional bounded 3 × 3 provider pilot;
- no new Agent, remediation action, Docker, live telemetry, or live mutation.

Exit:

- 36/36 runs return typed terminal results with zero empty-evidence failures;
- Fixed and Dynamic v2 each exceed the frozen v1 2/7 original-case result;
- Dynamic average tool calls do not exceed Fixed;
- report verification and Phase 1–4 regressions pass;
- claims remain limited to the visible development templates.

## Phase 5B — Frozen hidden evaluation and demonstration

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
