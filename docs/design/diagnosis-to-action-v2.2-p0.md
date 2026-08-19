# Diagnosis-to-Action v2.2 P0

Status: `PR_A_PROTOCOL / REVIEW_COMPLETE / LOCAL_EXACT_HEAD_PASS / GITHUB_CI_PENDING`

Goal: `dta-v22-p0-master-v1`

Starting main: `9da92d54a4fb470c5452cee36a731e81529d05a5`

Primary model continuity target: `gpt-5.4-mini-2026-03-17`

Live Agent write authority: `0`

## Purpose

DTA v2.2 tests whether runtime-managed planning improves protocol reliability,
No-Incident calibration, evidence efficiency, and end-to-end exact success over
a reactive Flat baseline on planning-required replay incidents. It is an
algorithm and evaluation successor, not another live remediation campaign.

DTA v2 and v2.1 are immutable historical portfolios. Their reports, identities,
seals, terminals, and failed evidence remain authoritative for those versions.
The PR-A verifier must pass before any v2.2 evidence is accepted.

## P0 scope

Allowed by the active Goal, only at the named stage:

- offline source, config, test, CI, and documentation work;
- bounded Provider protocol/development/held-out calls after their gates;
- evaluator-controlled project-owned local Docker capture under the exact
  mutation allowlist;
- baseline restoration and project-owned cleanup;
- sequential PR integration and Master Progress maintenance.

Prohibited:

- Agent-driven live remediation or live Runbook execution;
- model-visible generic shell, commands, paths, Docker identities, or write API;
- production, cloud, Kubernetes, remote Docker, or non-owned mutation;
- v2/v2.1 held-out reruns or report rewrites;
- retry-until-pass or post-unblinding tuning;
- SFT, DPO, RLHF, new fault mechanisms, GUI, or dashboard work.

Evaluator-controlled capture is dataset generation. During capture, Agent,
Provider, and Runbook calls are all zero.

## Namespaces

- Python: `src/ecomsre/dta_v2/v22/`
- configuration: `config/dta-v22/`
- tests: `tests/dta_v22/`
- public results: `docs/results/dta-v22-*`
- private evidence: a non-repository root owned by the active Goal

The successor does not modify `src/ecomsre/dta_v2/v21`.

## Model continuity

The primary comparison uses exactly `gpt-5.4-mini-2026-03-17` to preserve
model continuity with v2.1. If that model is unavailable, execution stops at
`BLOCKED_DTA_V22_MODEL_CONTINUITY`; no silent model swap is allowed. A new
model variant requires a new Decision Record and must be labeled
`architecture + model joint successor`, so it cannot be represented as an
architecture-only repair of v2.1.

## Architecture

1. A deterministic common bootstrap produces runtime state, core metrics,
   request support, baseline-relative features, candidate subgraph, and source
   availability for every primary arm.
2. A source-local deterministic predicate extractor turns observations into
   typed evidence predicates.
3. Salient Evidence Memory retains all refs/predicates, Top-K typed facts, and a
   loss ledger; Full Memory is a development reference mode.
4. Runtime creates a closed `HypothesisCatalogV22` from candidate services and
   the versioned ontology.
5. Runtime owns `BeliefLedgerV22`, budgets, turn ordinal, request digests,
   coverage, action masking, and correction usage.
6. A canonical `ActionCatalogV22` exposes stable action IDs, never model-chosen
   query parameters.
7. The model emits the shared `ControllerDecisionV22`.
8. Admission either resolves one canonical read, constructs a Diagnosis from an
   accepted support clause, accepts No-Incident coverage, records Abstain, or
   returns one bounded no-tool correction.
9. A deterministic predicate-aware Candidate Filter produces candidate-bound
   Action Selection input.
10. P0 action evaluation is replay-only; Agent live write authority stays zero.

## Common bootstrap

`TriageSnapshotV22` contains candidate runtime state, error rate, latency p95,
request support, baseline-relative anomaly features, candidate subgraph, and
source availability. It contains no evaluator truth, injected fault flag,
expected mechanism, or expected action. It is byte-equivalent across primary
arms and its cost is counted.

Bootstrap may support direct `NO_INCIDENT` only when all candidates are covered,
runtime is healthy, metrics have sufficient support, and there is no strong
anomaly predicate.

## Hypothesis catalog and belief ledger

For at most four candidates, runtime creates each candidate x five mechanisms:
`CONFIGURATION_ERROR`, `SERVICE_UNAVAILABLE`, `MEMORY_LEAK`,
`CPU_SATURATION`, and `DEPENDENCY_LATENCY`, plus `NO_INCIDENT` and
`UNRESOLVED`. IDs are stable, such as `h:email:memory_leak`.

The model cannot create a service, domain, mechanism, ID, status, digest, or
budget. Runtime derives `UNTESTED`, `PARTIALLY_SUPPORTED`, `SUPPORTED`, and
`CONTRADICTED` from selected hypothesis history and resolved predicates.

## Canonical action catalog

Each `EvidenceActionV22` binds:

- action ID;
- source and exact targets;
- canonical versioned request;
- coverage key and dominance relation;
- weighted cost and request digest.

Catalog inputs are alert context, candidates, static topology, tool capability
registry, executed coverage, and remaining budget. Evaluator truth, fixtures,
expected sources/mechanisms, and fault controllers are forbidden inputs.

Executed, dominated, unavailable, and over-budget actions are removed before
the next model turn. Exact duplicate dispatch is therefore structurally
impossible.

## Shared controller schema

`ControllerDecisionV22` has only required fields:

- `decision`: `READ`, `COMMIT`, `NO_INCIDENT`, or `ABSTAIN`;
- `working_hypothesis_id`;
- `action_id`;
- `supporting_evidence_refs`;
- `contradicting_evidence_refs`.

Sentinels are fixed: `action_id=NONE` outside `READ`,
`h:none:no_incident` for `NO_INCIDENT`, and `h:none:unresolved` for `ABSTAIN`.
Runtime injects identity, run ID, turn ordinal, hashes, and budgets.

`PLANNER_LITE` receives the persistent ledger view and binds each read to a
working hypothesis. `FLAT_CANONICAL` receives the same bootstrap, memory,
catalog, and budget but no persistent ledger. No other primary-arm contract
differs.

## One bounded correction

One correction is permitted for an invalid/stale action ID, decision-shape
error, out-of-memory ref, or action removed by the current mask. It supplies
only a safe error code, current valid action IDs, and remaining budget. It
consumes one Provider turn, dispatches no tool, grants no write authority, and
cannot be repeated. Execution, CandidateSet, Runbook, authorization, and safety
fail closed without correction-based authority expansion.

## Query semantics

Runtime owns all result limits, metric bundles, sampling windows, sample counts,
and service tuples. Source outcomes distinguish `SUCCESS_NONEMPTY`,
`SUCCESS_EMPTY`, `FAILURE_UNAVAILABLE`, `FAILURE_TIMEOUT`, and
`FAILURE_SCHEMA`.

Metric support is explicit; zero samples produce `UNSUPPORTED`. Trace queries
return a bounded connected neighborhood around the requested service and retain
operation, parent, service path, first-error location, and duration without
re-anchoring the complete fixture.

The read-only `CHANGES` source contains an opaque change ID, service, timestamp,
category, rollout state, and revision digest. It contains no fault flag,
injected variant, expected mechanism, or Runbook. All families may have decoy
changes, and Configuration requires corroboration.

## Memory

`SALIENT_MEMORY` stores predicates, refs, Top-K typed facts, and
`MemoryLossLedgerV22`; it does not resend old full observations.
`FULL_MEMORY` stores full typed observations plus a minimal ref/status index; it
does not duplicate a fact-rich Evidence Index.

Metrics preserve support, value, baseline ratio, delta/z-score, and strength.
Logs preserve normalized template, severity, typed category/downstream, and
count. Traces preserve operation/path/edge/status/first-error/duration. Runtime
and bounded resource series retain their typed state. Changes retain category,
relative time, rollout state, and digest.

The loss ledger records original/retained/omitted counts, omitted field
categories, truncation, and artifact hash. All evidence refs remain resolvable.

## Semantic predicates and support clauses

Predicates are source-local, generic, deterministic, versioned, and frozen from
visible development thresholds. They do not read evaluator truth.

Representative support clauses:

- Configuration: recent target rollout AND (configuration log OR strong target
  error-rate predicate).
- Service unavailable: runtime not running OR (runtime unhealthy AND (strong
  error metric OR first-error trace)).
- CPU saturation: strong target CPU AND runtime healthy; business metrics are a
  non-regression guardrail.
- Memory leak: strong memory growth AND (memory metric OR restart pressure OR
  memory-pressure log).
- Dependency latency: dependency-latency edge AND strong latency on the affected
  parent/path.
- No-Incident: broad bootstrap coverage, all runtime healthy, sufficient metric
  support, and no strong anomaly.
- Abstain: no clause satisfied and budget exhausted or evidence unavailable or
  conflicting.

Runtime uses clauses only for admission; it does not choose the model's
hypothesis. Both the raw semantic proposal and admitted Diagnosis are retained.

## Diagnosis and action safety

Diagnosis terminals are `DIAGNOSED`, `NO_INCIDENT`, `ABSTAIN`, and `FAILED`.
Runtime derives root/domain/mechanism/entity from the selected closed hypothesis.
A completed UNKNOWN or partial fault Diagnosis is impossible.

Candidate filtering requires the admitted Diagnosis, resolved predicates, one
acceptable clause, trusted registry, and exact target. Action Selection receives
only the candidate-bound semantic view. P0 Runbook behavior is `REPLAY_ONLY`.

## Evaluation protocol

The normative metric definitions and gates are in
[`dta-v22-evaluation-metrics.md`](dta-v22-evaluation-metrics.md). The key
boundaries are:

- at least 40 protocol-only synthetic transitions before capture/freeze;
- protocol gate: first-pass >= 95%, post-correction >= 98%, invalid dispatches 0;
- 24 visible-development and 24 private held-out cases;
- visible 2x2 controller x memory factorial plus Router and oracle anchors;
- one 96-entry held-out execution, one unblinding, no retry;
- exact Planner and memory terminals regardless of positive or negative result.

Engineering completion is `DTA_V22_P0_ENGINEERING_COMPLETE`. It does not require
an advantage claim and does not authorize a future live campaign.

## Provenance

An execution report binds the exact pre-merge candidate code head. Later
post-merge metadata uses one protocol-predeclared administrative successor
attestation. Frozen reports are not rewritten to follow later repository heads.
Every successor attestation names the exact changed paths and raw hashes and
records zero Provider/Docker/held-out/fault/Runbook execution unless that stage
explicitly authorized those actions.

## True blockers

The only Goal blockers are:

- `BLOCKED_DTA_V22_BASELINE_HISTORY_DRIFT`
- `BLOCKED_DTA_V22_MODEL_CONTINUITY`
- `BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE`
- `BLOCKED_DTA_V22_QUERY_SEMANTICS`
- `BLOCKED_DTA_V22_TRUTH_ISOLATION`
- `BLOCKED_DTA_V22_CAPTURE_CALIBRATION`
- `BLOCKED_DTA_V22_DEVELOPMENT_GATE`
- `BLOCKED_DTA_V22_HELD_OUT_PROTOCOL`
- `BLOCKED_DTA_V22_SAFETY`
- `BLOCKED_DTA_V22_EXACT_HEAD_ACCEPTANCE`

A negative preregistered advantage result is not a blocker.
