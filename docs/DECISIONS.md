# EcomSRE-Agent Decision Register

## Status vocabulary

- `accepted`: binding until replaced by a new Decision Record.
- `deferred`: deliberately postponed to a named phase.
- `non-goal`: outside the stated project or phase.
- `unsupported`: no compatibility claim or silent fallback.
- `unresolved`: a genuine decision gap without an accepted boundary.
- `phase0_closure_required`: an accepted Phase 0 evidence obligation whose
  result does not exist yet; it is not a pending user product decision.

Deferred and closure-required items are tracked in
[OPEN_QUESTIONS.md](OPEN_QUESTIONS.md). There is currently no unresolved user
decision blocking Phase 0 implementation.

All decisions below are accepted. An accepted decision may have a later
effective phase. That does not make the boundary optional, but it also does not
authorize or expand Phase 0. Concrete choices that remain deferred are tracked
separately in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

## Decision consistency matrix

This register is the authority for Decision status, scope, and effective phase.
The referenced documents provide derived operational detail. If derived text
conflicts with this register, this register wins.

| ID | Short name | Status | Effective phase | Authority document | Referenced by | Blocks Phase 0? |
|---|---|---|---|---|---|---|
| DEC-001 | Supported host | accepted | Phase 0 | `DECISIONS.md` | AGENTS, Charter, Acceptance, Open Questions | Yes — unsupported environment blocks preflight |
| DEC-002 | Frozen upstream | accepted | Phase 0 | `DECISIONS.md` | AGENTS, Architecture, Acceptance, Open Questions | Yes — source or digest drift blocks bootstrap/acceptance |
| DEC-003 | Compose scope | accepted | Phase 0 | `DECISIONS.md` | AGENTS, Charter, Architecture, Roadmap, Acceptance | Yes — topology or scope drift blocks acceptance |
| DEC-004 | Resource isolation | accepted | Phase 0 | `DECISIONS.md` | AGENTS, Safety, Acceptance | Yes — unknown ownership blocks environment actions |
| DEC-005 | Ad proxy SLI | accepted | Phase 0 | `DECISIONS.md` | Charter, Roadmap, Acceptance | Yes — missing or failed statistical loop blocks acceptance |
| DEC-006 | Offline acceptance | accepted | Phase 0 | `DECISIONS.md` | AGENTS, Acceptance, Open Questions | Yes — unfrozen runtime dependency blocks acceptance |
| DEC-007 | Hidden truth split | accepted | Phase 0 onward | `DECISIONS.md` | AGENTS, Architecture, Safety, Acceptance | Yes — evidence leakage or missing separation blocks acceptance |
| DEC-008 | Telemetry readiness | accepted | Phase 0 | `DECISIONS.md` | Architecture, Roadmap, Acceptance, Open Questions | Yes — any unready signal blocks acceptance |
| DEC-009 | Structured RCA | accepted | Phase 1 onward only | `DECISIONS.md` | Charter, Roadmap, Open Questions | No — no Phase 0 model or RCA dependency |
| DEC-010 | Equal-budget comparison | accepted | Phase 1 onward only | `DECISIONS.md` | Charter, Architecture, Roadmap, Open Questions | No — no Phase 0 model or budget dependency |
| DEC-011 | Frozen evaluation | accepted | Phase 5 only | `DECISIONS.md` | Charter, Architecture, Roadmap, Open Questions | No — no Phase 0 scenario-suite dependency |
| DEC-012 | Restricted writes | accepted | Phase 3 onward only | `DECISIONS.md` | AGENTS, Charter, Roadmap, Safety, Open Questions | No — Phase 0 has no remediation executor |
| DEC-025 | Phase 3 agile restricted-remediation replay MVP | accepted | Phase 3 v1 only | `DECISIONS.md` | Roadmap, Safety, Open Questions, Phase 3 implementation and tests | No — replay-only Phase 3 boundary |
| DEC-026 | Phase 4 e-commerce domain replay extension MVP | accepted | Phase 4 only | `DECISIONS.md` | Roadmap, Open Questions, Phase 4 implementation and tests | No — replay-only Phase 4 boundary |
| DEC-027 | Multi-Agent diagnosis quality repair | accepted | Phase 5A only | `DECISIONS.md` | Roadmap, Open Questions, Phase 5A implementation and tests | No — visible development evaluation only |
| DEC-028 | Frozen hidden paired evaluation protocol | accepted | Phase 5B v1 | `DECISIONS.md` | Roadmap, Open Questions, Phase 5B protocol and tests | No — protocol freeze does not enter execution |
| DEC-029 | Hidden-pack seal control plane | accepted | Phase 5B-1 only | `DECISIONS.md` | Roadmap, Open Questions, seal tooling and evidence | No — out-of-band build and verification only |
| DEC-033 | DTA v2 namespaced offline architecture | accepted | DTA v2 PR-0/PR-A offline only | `DECISIONS.md` | Architecture, Safety, DTA v2 design and tests | No — no Live authority or Phase 0 dependency |
| DEC-034 | DTA v2 bounded multi-step policy | accepted | DTA v2 design only; Live requires a later Goal | `DECISIONS.md` | Safety, DTA v2 design and contracts | No — design does not authorize execution |
| DEC-035 | DTA v2 Master Authorization delegation | accepted | DTA v2 PR-B onward under `dta-v2-master-v1` | `DECISIONS.md` | Safety, DTA v2 design, admission and authorization tests | No — the record alone creates no Docker or Provider action |
| DEC-036 | DTA v2 provisional Agent identity and Provider development gate | accepted | DTA v2 PR-D under `dta-v2-master-v1` | `DECISIONS.md` | Architecture, Safety, DTA v2 design and Agent tests | No — development Provider evidence creates no write authority |
| DEC-037 | DTA v2 PR-E replay evaluation protocol and result | accepted | DTA v2 PR-E under `dta-v2-master-v1` | `DECISIONS.md` | Architecture, Safety, DTA v2 design and evaluation tests | No — evaluation creates no remediation or later-stage authority |
| DEC-038 | DTA v2 PR-F known-scenario local live Demo result | accepted | DTA v2 PR-F under `dta-v2-master-v1` | `DECISIONS.md` | Architecture, Safety, DTA v2 design and live reports | No — consumed local Demo authority creates no production or continuing write authority |
| DEC-039 | DTA v2.1 versioned successor and immutable v2 bindings | accepted | DTA v2.1 P0 under `dta-v21-p0-master-v1` | `DECISIONS.md` | Architecture, Safety, DTA v2.1 design and historical verifier | Yes — any historical binding drift blocks the successor |
| DEC-040 | DTA v2.1 crossed service and mechanism matrix | accepted | DTA v2.1 P0 | `DECISIONS.md` | DTA v2.1 design, scenario registry, evaluator | Yes — shortcut-prone or incomplete matrices block evaluation |
| DEC-041 | DTA v2.1 evidence-guided planner and compact state | accepted | DTA v2.1 P0 | `DECISIONS.md` | Architecture, Safety, planner and projection contracts | Yes — truth routing, duplicate reads, or invalid evidence blocks the run |
| DEC-042 | DTA v2.1 frozen three-arm evaluation and honest claim gate | accepted | DTA v2.1 PR-D/PR-E | `DECISIONS.md` | DTA v2.1 design, freeze, scorer, reports | Yes — protocol drift or a failed threshold forbids an advantage claim |
| DEC-043 | DTA v2.1 bounded local portfolio and zero model write authority | accepted | DTA v2.1 PR-F under `dta-v21-p0-master-v1` | `DECISIONS.md` | Architecture, Safety, live contracts and reports | Yes — authority, ownership, recovery, or cleanup mismatch blocks live continuation |
| DEC-044 | DTA v2.1 Ad CPU resource-only recovery protocol | accepted | DTA v2.1 PR-F amendment | `DECISIONS.md` | Safety, live protocol, capability report | Yes — frozen resource/recovery semantics cannot drift |
| DEC-045 | DTA v2.1 closed-world Compose identity and retry admission | accepted | DTA v2.1 PR-F amendment | `DECISIONS.md` | Safety, live admission, reconciliation | Yes — identity or retry mismatch blocks continuation |
| DEC-046 | DTA v2.1 No-Fault capability-miss preservation | accepted | DTA v2.1 PR-F amendment | `DECISIONS.md` | Capability closeout and reports | Yes — the miss cannot be relabeled or rerun |
| DEC-047 | DTA v2.1 frozen-Agent capability-limitations closeout | accepted | DTA v2.1 PR-F closeout | `DECISIONS.md` | Master Progress and capability report | Yes — no further v2.1 Provider or Docker execution |
| DEC-048 | DTA v2.1 administrative successor attestation | accepted | DTA v2.1 PR-F post-merge metadata | `DECISIONS.md` | Historical binding and administrative attestation | Yes — future changes require a new record |
| DEC-049 | DTA v2.2 versioned successor and v2.1 immutability | accepted | DTA v2.2 P0 under `dta-v22-p0-master-v1` | `DECISIONS.md` | DTA v2.2 design, historical verifier, Master Progress | Yes — history drift blocks v2.2 |
| DEC-050 | DTA v2.2 runtime-owned state and shared controller schema | accepted | DTA v2.2 P0 | `DECISIONS.md` | Planner-Lite, Flat Canonical, controller contracts | Yes — arm/schema asymmetry invalidates the primary comparison |
| DEC-051 | DTA v2.2 canonical action catalog and query semantics | accepted | DTA v2.2 PR-B onward | `DECISIONS.md` | Action catalog, replay, read contracts | Yes — truth-dependent catalogs or ambiguous query semantics block evaluation |
| DEC-052 | DTA v2.2 semantic evidence predicates and alternative clauses | accepted | DTA v2.2 PR-C onward | `DECISIONS.md` | Memory, Diagnosis, Candidate Filter, scorer | Yes — truth-dependent predicates or unsupported admission block evaluation |
| DEC-053 | DTA v2.2 factorial development and paired held-out evaluation | accepted | DTA v2.2 PR-D through PR-F | `DECISIONS.md` | Protocol gate, preregistration, scorer, reports | Yes — gate, seal, truth-isolation, or scorer drift blocks held-out |
| DEC-054 | DTA v2.2 P0 zero live Agent write authority | accepted | DTA v2.2 P0 | `DECISIONS.md` | Safety, capture, replay, reports | Yes — any Agent write or non-owned mutation is a safety blocker |
| DEC-055 | DTA v2.2 execution report and administrative successor provenance | accepted | DTA v2.2 PR-A through PR-F | `DECISIONS.md` | Frozen reports, exact-head acceptance, successor attestations | Yes — provenance mismatch blocks closure |

## DEC-001 — Supported host baseline

The initial supported host is a MacBook Pro with Apple Silicon M5 Pro, 48 GB
unified memory, 2 TB SSD, at least 25 GB free before a run, Docker Desktop,
Docker Compose v2, and native `linux/arm64`.

Preflight must automatically record the OS, architecture, CPU, memory, disk,
Docker client/server and Desktop/engine information, Compose version, Docker
resource allocation, resource collisions, and port ownership. It must not
install or upgrade Docker. Other engines, hosts, and amd64 emulation are
unsupported and fail closed.

## DEC-002 — OTel Demo source and image freeze

The sole Phase 0 upstream is OpenTelemetry Demo tag `3.0.0`, commit
`1755859a9de82c2e5e225be68abc401a5ebf2b4f`, mounted as a read-only submodule at
`third_party/opentelemetry-demo`.

No `main`, floating tag, `latest`, silent fallback, amd64 emulation, or upstream
patch is allowed. Every executed image is locked by both image-index digest and
resolved `linux/arm64` digest. Runtime evidence records those digests, the
submodule commit, the resolved Compose hash, and frozen `demo.*` query fixtures.
Failure of this baseline requires a new Decision Record.

## DEC-003 — Compose topology and Phase 0 service scope

Use frozen upstream `compose.yaml` and `compose.observability.yaml` as the
preferred topology. Do not use `compose.full.yaml`, agentic services, profiling,
extras, Kubernetes, AIOpsLab, Feature Service, or Ranking Service.

Required capabilities are Frontend/Proxy, Ad, flagd/UI, k6, Collector,
Prometheus, Jaeger, OpenSearch, and official dependencies. Incidental services
required by the official topology are allowed but are not Phase 0 acceptance
objects or capability claims. Do not maintain a private Compose fork merely to
make the stack smaller.

## DEC-004 — Resource isolation

All project resources use a stable `ecomsre-phase0` namespace plus per-run
metadata. Ownership must be proven by matching labels and manifests. Unknown
containers, networks, volumes, ports, files, locks, or processes cause a
fail-closed conflict.

The project never adopts, stops, kills, deletes, or reconfigures unknown
resources and never performs global Docker cleanup.

## DEC-005 — Phase 0 incident oracle

Phase 0 measures an Ad availability proxy, not a full-site business SLO. The
denominator is observed Ad Service `GetAds` call attempts.

Each of three consecutive cycles runs readiness, stabilization, baseline,
inject, stabilization, fault, reset, stabilization, and recovery. Stabilization
defaults to 30 seconds. Each measurement window requires at least 200 attempts
within 180 seconds. Thresholds are baseline ≤1%, fault 5%–20%, and recovery
≤1%. Window-local counts and rates are authoritative; 95% Wilson intervals are
evidence only. Failed cycles are retained and never hidden by selective reruns.

## DEC-006 — Bootstrap versus acceptance

Bootstrap may initialize the exact submodule, pull locked images, verify
digests, create project directories, inspect conflicts, and record the machine.

A canonical acceptance run uses cached inputs, an equivalent of `--pull never`,
no online install or upstream fetch, and no undeclared external dependency.
Any runtime pull, install, update, or undeclared external access fails the run.

## DEC-007 — Change visibility and ground truth

The main evaluation track exposes only sanitized change records: opaque ID,
time, target service, change category/source, rollout state, and a
non-semantic artifact reference.

Exact feature-flag key/value, scenario identity, expected answer, evaluator
labels, and semantic paths remain evaluator-only. Observer and evaluator
artifacts are separated. Full change visibility is allowed only as a separately
reported ablation.

## DEC-008 — Telemetry readiness

Prometheus plus a deterministic request probe determine incident impact and
recovery. Jaeger and OpenSearch are readiness gates, not independent 5%–20%
oracles.

Every run must prove fresh, attributable data for Ad in Prometheus, Jaeger, and
OpenSearch within the current run window. Service identity, time window, and
scenario phase are mandatory correlation dimensions; trace/request correlation
is used when available. A missing channel prevents Phase 0 pass.

## DEC-009 — Structured RCA target

RCA uses frozen labels for `root_service`, `fault_mechanism`,
`causal_chain`, and `affected_sli`. Causal chains are ordered typed edges.

Evaluation reports component accuracy, causal-edge precision/recall/F1,
evidence grounding, contradiction handling, missing-evidence identification,
and calibrated abstention. Exact free-text matching is not the RCA score.

## DEC-010 — Equal-budget comparison

The main comparison fixes model snapshot, provider, temperature, output
parameters, tools and schemas, scenario and telemetry snapshot, input/output
token caps, and total tool-call cap.

Commander, specialists, Judge, summarizers, retries, and replans all count.
Parallel token and tool usage is not free. Wall-clock and aggregate compute are
reported separately. Architecture-native cost curves may supplement but never
replace the equal-budget result.

## DEC-011 — Evaluation credibility

Before Phase 5, freeze at least 12 templates spanning single-root-cause,
cascading failure, unrelated or decoy changes, confounded changes, missing
telemetry, delayed telemetry, partial tool failure, no-incident negatives,
anomalies requiring no write, safe-remediation cases, and required-abstention
cases. At least 30%, and preferably at least four templates, remain hidden.
Each template uses at least five paired seeds across all architectures.

Report every run, paired differences, and bootstrap confidence intervals.
Required ablations are no Commander, no RCA Judge, parallel-to-sequential,
shared context, no contradiction check, no independent verifier, and full
change visibility. Unblinding ends prompt tuning for that evaluation version.

## DEC-012 — Restricted writes

The Restricted Executor permanently targets only local, isolated,
project-owned Demo resources. Arbitrary shell, host mutation, global Docker,
cloud or enterprise systems, real credentials, public writes, and unlabeled
resources are forbidden.

One remediation attempt permits at most one forward mutation. Read checks,
verification, and necessary compensating rollback do not consume that limit.
Human approval is the default; auto-approval exists only in an explicitly
marked local test mode. Unsafe, failed, or uncertain state terminates without a
second forward mutation.

This is the default restricted-write limit. A later Decision Record may replace
it only by naming an exact versioned Runbook and preserving every other safety
term. `DEC-034` does so only for the exact versioned DTA v2 Email transaction; it does not
change Phase 3, LOCAL_DEMO, or any other Runbook.

## DEC-025 — Phase 3 Agile Restricted Remediation Replay MVP

**Status: `accepted`. On 2026-08-03 the user rejected the earlier heavyweight
proposal and explicitly accepted this lean replay-only replacement. The binding
completion marker is `PHASE3_RESTRICTED_REMEDIATION_REPLAY_MVP_READY`.**

Phase 3 v1 closes only the local, offline, replay-backed Planner → deterministic
Policy Gate → Human Approval Gate → Restricted Executor → independent Verifier
→ compensating rollback path. It must not run Docker, the OTel Demo, a real
feature flag, a host write, a cloud API, or any other live mutation.

The exact v1 allowlist contains only
`RESTORE_FROZEN_SERVICE_CONFIGURATION`. Its target service is `ad`, its required
RCA mechanism is `runtime_configuration_failure`, its backend is a replay
resource, its blast radius is exactly one replay-owned configuration field, and
each attempt permits at most one forward mutation. The Executor never accepts
shell, argv, a script, URL, arbitrary path, Docker command, arbitrary key/value
map, or free-form action.

The deterministic Planner emits the action only when the Phase 2 decision is
`RCA_CONFIRMED`, the root service is `ad`, the mechanism is
`runtime_configuration_failure`, at least one supporting evidence reference
resolves in the current-run Evidence Store, `missing_evidence` is empty, the
replay resource belongs to the current run, and its pre-state matches the
fault state. Otherwise it returns typed `NO_ACTION`. Phase 3 adds no two-source
requirement and no LLM Planner requirement; it does not weaken the frozen Phase
1 or Phase 2 RCA contracts.

The pure deterministic Policy Gate checks run, incident, attempt, action and
plan identity; the exact action allowlist; RCA/action compatibility;
current-run evidence scope; replay resource ownership; expected pre-state;
state version; zero prior forward mutations; valid approval; available rollback
pre-state; and replay-only targeting. It returns only typed `ALLOW` or `DENY`
with a stable reason code.

Human approval is required by default and binds `run_id`, `incident_id`,
`attempt_id`, `action_id`, the plan digest, and the decision. An explicitly
marked `LOCAL_TEST_AUTO_APPROVAL` is allowed only in tests and must appear in
the report. Phase 3 v1 does not require approval expiry, a cryptographic nonce,
an authenticated ownership digest, or a separate anti-replay authority.
Typed identity validation still rejects a forged, duplicate, wrong-run,
wrong-attempt, wrong-action, or wrong-plan approval.

The attempt state is in memory or temporary replay state. It uses
compare-before-mutate state-version checks, allows at most one forward mutation,
rejects a second invocation, closes on every terminal outcome, and produces a
deterministically replayable report. A compact ordered event list and one
semantic report hash are allowed. A durable append-only hash-chained ledger,
exclusive filesystem locking, crash-recovery journal, CAS storage engine,
previous-event hash chain, and per-event provenance hierarchy are explicitly
not required and must not be introduced as Phase 3 v1 evidence machinery.

The Restricted Executor accepts only the exact typed action, an `ALLOW`
PolicyDecision, an approved ApprovalDecision, and matching run/resource/state
version. It may change only the one frozen field in the replay resource and
returns `NOT_APPLIED`, `APPLIED`, or `FAILED`. Phase 3 v1 does not build an
`UNKNOWN` crash-recovery protocol. Exceptions fail closed and preserve the
current replay state.

The independent Verifier is read-only over replay post-state and a deterministic
replay-health fixture. It proves configuration recovery, unchanged ownership,
the correct state version, exactly one allowed field change, a forward mutation
count of one, and recovered replay health. It does not depend on a 200-attempt
or 180-second window, live Prometheus/Jaeger/OpenSearch freshness, Phase 0
readiness, or a live SLO window; those belong to a later live-integration phase.

When verification is `FAILED` or `INCONCLUSIVE`, the attempt forbids another
forward mutation and may perform one compensating rollback using only the exact
before-state in the ExecutionReceipt. Rollback restores only this change and is
then read back against the before-state. Terminal outcomes include at least
`REMEDIATION_VERIFIED`, `NO_ACTION`, `APPROVAL_DENIED`, `POLICY_REJECTED`,
`PRECONDITION_FAILED`, `VERIFICATION_FAILED_ROLLED_BACK`, `ROLLBACK_FAILED`, and
`UNSAFE`.

Minimum evaluation covers six replay cases: safe remediation success; human
approval denied; RCA abstain/no action; pre-state or state-version drift;
cross-run or unowned resource rejection; and verification failure followed by
rollback. Separate tests reject a forged approval, a second forward mutation,
and an arbitrary executable payload.

The only new tracked review summary allowed is
`docs/review-evidence/phase3-restricted-remediation/current-disposition.json`.
Phase 3 v1 must not add a layered review packet, command-by-command evidence
tree, durable hash ledger, new hash contract, long closure HTML, or independent
review workflow framework.

This decision authorizes implementation on
`phase3/restricted-remediation-replay`, closing `OQ-008`, and adding
`src/ecomsre/phase3` plus `tests/phase3`. It does not authorize changes to Phase
0 or frozen Phase 1/2 semantics, a live mutation, Phase 4, or Phase 5. The
accepted Phase 3 goal remains controlling unless it conflicts with this record;
where it conflicts, this DEC-025 wins.

## DEC-026 — Phase 4 E-commerce Domain Replay Extension MVP

**Status: `accepted`. The binding offline completion marker is
`PHASE4_OFFLINE_ECOMMERCE_DOMAIN_REPLAY_MVP_READY`; it may be upgraded to
`PHASE4_ECOMMERCE_DOMAIN_REPLAY_MVP_READY` only after the separate four-run
real-provider gate passes.**

Phase 4 extends only replay-backed Search and Recommendation diagnosis through
the existing Commander, Metrics/Logs/Trace/Change Specialists, DAG admission,
Evidence and Finding Stores, and central Budget Ledger. It adds no Agent and no
general orchestration framework. A narrow pre-Judge Specialist execution
boundary permits an independent `phase4.domain-rca-result.v1` Judge without
changing the frozen Phase 1 RCA v1 schema or the Phase 2 default Judge.

The exact new mechanism allowlist is `feature_freshness_lag`,
`model_feature_schema_mismatch`, and `ranking_configuration_failure`. Confirmed
roots are limited to `feature` or `ranking`; Search and Recommendation are the
bounded business SLI surfaces. Mechanism classification is evidence-native and
uses only typed current-run source, service, observation, and attributes.
Evaluator labels, case identity, fixture paths, and expected answers are not
runtime inputs.

The visible Phase 4 suite contains exactly five new templates: three confirmed,
one need-more-evidence, and one abstention, including one frontend decoy. Each
template runs once through `FIXED_SPECIALIST_WORKFLOW` and once through
`DYNAMIC_MULTI_AGENT`, for ten retained offline runs. The report is a
deterministic domain-correctness evaluation, not a Fixed-versus-Dynamic
superiority claim and not a Phase 5 statistical evaluation.

Phase 3 remains unchanged. A confirmed Phase 4 domain mechanism receives
`NO_SUPPORTED_REMEDIATION`; an unconfirmed result receives `NO_ACTION`.
Neither path can produce `RESTORE_FROZEN_SERVICE_CONFIGURATION`, a Docker or
provider fallback, a live mutation, or a new remediation action.

The bounded provider smoke reuses the frozen Agent Mainline model snapshot and
OpenAI-compatible transport for exactly four positive/negative Fixed/Dynamic
runs with temperature zero, one exact typed Domain tool call, no retry, no
scripted fallback, and no evaluator truth. An unconfigured provider yields
`SKIPPED_NOT_CONFIGURED` and preserves the offline completion marker.

This decision closes `OQ-009`. It does not authorize live Feature or Ranking
services, Docker or OTel Demo work, Phase 5 hidden splits or paired seeds,
bootstrap confidence intervals, remediation expansion, release, deployment,
or a Multi-Agent superiority claim.

## DEC-027 — Multi-Agent Diagnosis Quality Repair and Capability-Parity Evaluation

**Status: `accepted`. The binding completion marker is
`PHASE5A_MULTI_AGENT_QUALITY_REPAIR_READY`. This is a visible development-set
quality repair, not Phase 5B or a Multi-Agent superiority evaluation.**

Phase 5A adds the independent `phase5a.diagnosis-quality-v2` surface without
changing the frozen Phase 1 RCA v1, Phase 2 comparison v1, Phase 3 action
allowlist, or Phase 4 Domain RCA v1 contracts. Its exact unified mechanism set
is `runtime_configuration_failure`, `request_processing_failure`,
`cache_backend_timeout`, `feature_freshness_lag`,
`model_feature_schema_mismatch`, and `ranking_configuration_failure`.

Specialists now hand off typed mechanism candidates, supporting and
contradicting current-run evidence, concrete missing evidence, and confidence.
`AVAILABLE`, `EMPTY`, `SOURCE_UNAVAILABLE`, and `QUERY_FAILED` are distinct
observation states. A dispatched read-only tool error remains a charged,
auditable typed attempt and does not automatically collapse the whole workflow.
The Phase 2 v1 success-only dispatch behavior remains available and unchanged.

Single v2, Fixed Specialist v2, and Dynamic Multi-Agent v2 share the same
evidence-native semantics and closed `phase5a.diagnosis-result.v2` contract.
Dynamic v2 uses Metrics first, evidence-driven Logs/Traces expansion, and at
most one targeted refinement. Every model/tool/token charge remains owned by
the central Phase 2 budget ledger; no orchestration work is treated as free.

The visible development evaluation runs all seven Phase 1 cases and all five
Phase 4 cases through all three v2 variants, retaining 36/36 typed terminal
results. At acceptance, original-seven decision accuracy is 7/7 for Single,
Fixed, and Dynamic v2; empty-evidence workflow failures are zero; Dynamic uses
2.5 average tool calls versus Fixed's 4.0. The report labels itself
`VISIBLE DEVELOPMENT EVALUATION` and `NOT A SUPERIORITY CLAIM`. The frozen
Phase 2 v1 baseline remains Single 7/7, Fixed 2/7, Dynamic 2/7 with semantic
SHA-256 `3734e5814a5a0bbe139f7e7ca346e06f0d139ec4f9947b4a97cb6a34c7af14b4`.

The optional real-provider pilot is exactly three visible cases by three
variants, with one frozen model snapshot, temperature zero, equal completion
limit, no retry, no scripted fallback, complete usage, and all failures
retained. An absent provider configuration returns `SKIPPED_NOT_CONFIGURED`
and does not block the offline marker.

Phase 5A adds no Agent, remediation action, Docker command, live telemetry,
live mutation, hidden template, paired seed, bootstrap interval, release, or
deployment. Phase 5B remains separately gated and unentered. This decision
closes `OQ-010`; it does not close or weaken the hidden-evaluation requirements
in `OQ-007` and `DEC-011`.

## DEC-028 — Phase 5B Frozen Hidden Paired Evaluation Protocol

**Status: `accepted`. The protocol-only completion marker is
`PHASE5B_PROTOCOL_FREEZE_READY`; it is not a hidden-pack, execution, unblinding,
or superiority result.**

Phase 5B v1 freezes exactly 12 templates: six immutable public anchors and six
opaque hidden coverage slots, with five paired scenario-instance seeds and the
three arms `SINGLE_AGENT_V2`, `FIXED_SPECIALIST_V2`, and
`DYNAMIC_MULTI_AGENT_V2`. The main schedule contains 60 pairing units and 180
scored runs. The seven preregistered ablations contain 38 additional,
primary-ineligible runs. Every arm uses the same OpenAI-compatible provider,
`gpt-5.4-mini-2026-03-17`, temperature zero, model/tool caps of 8, a 32,000
token cap, a 2,048 completion-token cap, and two-second inter-call pacing.
Hidden retry and scripted fallback are forbidden.

The primary endpoint is hidden-only Dynamic-versus-Single Decision Accuracy.
The preregistered 10,000-replicate hierarchical paired bootstrap first samples
hidden templates with replacement and then paired seeds within each selected
template. Superiority may be claimed only when the percentile 95% confidence
interval lower bound for Dynamic minus Single correctness is greater than zero.
The separate cost-quality rule requires an accuracy lower bound of at least
-0.05, mean tool-call reduction of at least 20%, and a positive tool-reduction
interval lower bound. Failures stay in the denominator with correctness zero.

The real hidden pack remains outside the repository. Workers receive only an
opaque instance identity and the agent-visible replay case; evaluator truth is
unavailable until all 180 raw execution records are frozen. Unblinding is a
create-once irreversible record bound to the protocol commit, freeze manifest,
schedule, hidden-pack hashes, and execution report. Any post-freeze retuning or
runtime change requires `phase5b.v2`; evaluation commits may not be squashed or
rewritten after execution begins.

Phase 5B-0 validates only a 2-template × 2-seed × 3-arm synthetic mock dry run,
labelled `MOCK_PROTOCOL_DRY_RUN` and `NOT_MODEL_EVIDENCE`. It creates no real
hidden case, reads no hidden truth, calls no Provider, enters no scored
execution, and establishes no Multi-Agent superiority. At this boundary:
protocol frozen `YES`; hidden pack sealed `NO`; execution entered `NO`;
unblinded `NO`.

This decision partially resolves `OQ-007` by freezing suite, seed, schedule,
statistics, isolation, and unblinding contracts. Final `OQ-007` closure still
requires a separately authorized sealed hidden-pack manifest and execution
freeze. It does not authorize hidden-pack construction, Provider execution,
unblinding, release, deployment, or Phase 5A prompt/runtime modification.

## DEC-029 — Phase 5B-1 Hidden-Pack Seal Control Plane

**Status: `accepted`. Phase 5B-1 seal tooling is out-of-band control-plane
tooling. The first sealed pack is `SUPERSEDED / NOT_EXECUTION_ELIGIBLE`; only a
fresh post-relocation pack may become the authoritative sealed pack.**

The evaluator-only truth contract, safe aggregate seal record, structural seal
verifier, and offline seal CLI are repository control-plane utilities, not part
of the frozen `phase5b.v1` execution runtime. Their public modules live under
`scripts/phase5b_hidden_pack/`, and their answer-free aggregate binding lives
under `config/phase5b-seal/`. They must remain outside the recursive discovery
roots owned by the existing v1 freeze manifest. Neither the manifest nor the
frozen runtime may be updated, bypassed, or reinterpreted to admit them.

The first external pack built before this path boundary was accepted remains
preserved read-only and blinded. It must not be deleted, overwritten, executed,
or represented as the authoritative pack. Its exact disposition is
`SUPERSEDED / NOT_EXECUTION_ELIGIBLE`.

After relocation, Phase 5B-1 must pass the unchanged v1 preflight with an exact
frozen path set, then create and validate a fresh external pack at a new
create-once location. The public answer-free seal records may bind only that
fresh authoritative pack and its relocated builder and validator sources. This
decision authorizes construction, offline validation, and read-only sealing of
that replacement pack only. Agent runs, Provider calls, scored execution, and
unblinding remain forbidden and at zero; no superiority claim is created.

## DEC-030 — RCAEval Root-only Metrics Arbitration M3

**Status: `accepted`. The development result remains bounded by the exact live
marker recorded in `docs/results/rcaeval-metrics-arbitration-v1-development.json`;
it is not external validation or a production-generalization claim.**

RCAEval Metrics Arbitration v1 is an independent evaluation/runtime derived
from the PR #20 `METRICS_ARBITRATION` decision at commit `59ace4d`. It is not a
sixth Adaptive candidate and does not alter Candidate-3, Candidate-4,
Candidate-5, PR #19, or PR #20. Each case queries the same bounded Metrics,
Logs, and Traces tools, makes exactly one Strong Single `ArchitectureContext ->
Diagnosis` model call, then applies deterministic Root-only M3. It constructs
no specialist Provider and performs no model Fusion.

M3 overrides the Initial Root with Metrics Top-1 only when the Initial service
is absent from the Metrics ranking or has rank greater than two, and
`(top1_score - top2_score) / max(abs(top1_score), 1e-12) >= 0.25`. A
single-service ranking has margin `1.0`. If Metrics Top-1 already equals the
Initial Root, the action is `KEEP_INITIAL`. The exact Initial indicator is
always preserved. KEEP retains the exact Initial Diagnosis; OVERRIDE cites only
legal run-visible Metrics evidence, uses deterministic provenance and
explanation, and assigns no model confidence to the changed Root.

The frozen development lifecycle is zero-Provider fixture replay, one
synthetic non-case Provider preflight, a 12-case Smoke reused inside one
60-case consumed TUNE, then—only if the TUNE gate passes—one 120-case consumed
Regression. Concurrency is one, pacing is five seconds, Retry-After is
respected, and at most one allowlisted byte-identical transport retry is
permitted. Terminal, retry, token, and pacing evidence reuse the dev3 transport
sidecars under a new Metrics Arbitration namespace.

Primary damage/rescue conclusions compare the Strong Single Initial and M3
Final from the same call/run. Historical Strong Single TUNE `51/60` Root and
`29/60` Pair, and Regression `99/120` Root and `55/120` Pair, are
`CROSS_RUN_CONTEXTUAL_BASELINE` only. All case-level material remains outside
Git. Public results exclude case/run identifiers, concrete services, evidence
references, raw Provider output, private paths, and credentials.

This decision authorizes the bounded development implementation and the exact
one-shot evaluation lifecycle above. It does not authorize RE2-TT access, new
external data, a second TUNE or Regression, post-Regression retuning, release,
deployment, merge, or an external-validity claim. A fresh external holdout is
plan-only and may be written only after a passing Regression; acquiring or
running it requires separate authorization.

## DEC-031 — One Human-approved Live Local Sandbox Successor

**Status: `accepted`. Invocation A must stop at
`SANDBOX_REMEDIATION_HUMAN_APPROVAL_REQUIRED`; only an exact, unexpired human
record may admit Invocation B.**

This decision creates an independent live-local successor without modifying
historical Phase 0 acceptance or Phase 3 replay contracts. It uses the clean
OpenTelemetry Demo 3.0.0 submodule at commit
`1755859a9de82c2e5e225be68abc401a5ebf2b4f`, a local Unix-socket Docker daemon,
`linux/arm64`, one Compose project, fixed loopback endpoints, and exact dual
ownership labels. The historical Phase 0 image lock remains immutable. A
private successor lock may reverify the same cached image identities against a
new resolved Compose hash with reason `COMPOSE_OVERRIDE_CHANGED`.

Exactly one built-in scenario is registered: `paymentFailure.defaultVariant`
changes from baseline `off` to fault `100%`, and the only action is
`RESTORE_FROZEN_SERVICE_CONFIGURATION`. Baseline and fault are hash-bound whole
documents that differ at only that field. The forward remediation mutation
limit is one, with at most one exact compensating rollback. There is no
arbitrary argv, shell, second fault, second candidate, retry run, upstream
patch, image rebuild, remote Docker, Kubernetes, production, release, or
deployment authority.

Real target-service Metrics, Logs, and Traces are required. The existing A0
Strong Single Prompt, typed output schema, one-call runtime, and deterministic
keep-initial hierarchical decision remain unchanged. Scenario identity,
expected answer, control key, and remediation action are not model inputs. An
exact root, exact `APPLICATION` class, valid Metrics evidence, and at least one
valid Logs or Traces reference are mandatory before planning.

Invocation A is no-fault only. It must prove health, stabilization, baseline
control, real telemetry, exact cleanup, and zero owned resources before
creating the private scenario lock and human approval request. Invocation B is
admitted only by an exact create-once human record and starts with one typed
Provider preflight. It then permits one positive live run, independent two-
window SLI verification, baseline restoration, and project-owned cleanup.

Public claims are limited to `LIVE_LOCAL_SANDBOX_DEMO`,
`CONTROLLED_FAULT_INJECTION`, `HUMAN_APPROVED_REMEDIATION`, `NOT_PRODUCTION`,
`NOT_EXTERNAL_BENCHMARK`, and `NOT_SECURITY_VULNERABILITY_DETECTION`. Raw
telemetry, configuration documents, Provider material, approvals, receipts,
and run evidence remain in private `0700` directories with `0600` files.

## DEC-032 — LOCAL_DEMO Root-and-Evidence Admission with Standing Authorization

**Status: `accepted` for the one-session local successor. Until a sealed live
terminal proves otherwise, its public disposition remains `PRE_LIVE /
REVIEW_REQUIRED`.**

The `feature/local-e2e-demo-v1` successor starts at the exact PR #40 result
head `f939824c9b33eca69939aab5d6aa6a5097123e7e` and preserves the V6_REPRO_3
legal negative result byte-for-byte. It is a `POST_FAILURE_REGRESSION_DEMO` on
the consumed payment scenario, not a new evaluation generation, held-out RCA
result, or model-quality comparison.

The existing strict Diagnosis Gate remains unchanged and authoritative for
fault-class audit quality. LOCAL_DEMO adds a separate injected admission Gate:
the diagnosis must complete in one Strong Single semantic call with no
specialists or fusion, select the visible `payment` root, cite unique and
resolver-backed Metrics plus Logs or Traces evidence with exact source
accounting, bind to the exact Provider live-input context hash, and expose no
control truth. An `APPLICATION` class mismatch remains visible as
`FAULT_CLASS_MISMATCH_WARNING` but does not by itself deny the frozen local
restoration action.

The user's Goal is the standing human authorization for this successor. Its
private create-once typed record is bound to the local environment, sandbox,
scenario, payment service, `paymentFailure.defaultVariant`, exact frozen
baseline, action `RESTORE_FROZEN_SERVICE_CONFIGURATION`, one forward mutation,
and at most one compensating rollback. It records `Minghong Sun` as approver,
`CODEX_DELEGATED_EXECUTION`, and `codex_autonomous_self_approval = false`.
Ordinary implementation, test, Prompt, projection, transport, CI, or reporting
repairs do not create a new authorization ceremony or a new version.

Each attempt may use one Provider synthetic preflight and one live A0 semantic
call. There is no global attempt count, but an identical failed attempt is
forbidden: a retry requires a real committed implementation or runtime-config
change and the prior attempt must have restored baseline and completed owned
cleanup. Every attempt retains private Provider response/tool-call/A0/ontology/
final-diagnosis and dual-Gate lineage. Public result files are created only
from a successful sealed terminal.

The only permitted mutation is the deterministic, typed restoration of the
frozen baseline on the project-owned local Unix Docker sandbox. Remote Docker,
unknown or non-owned resources, arbitrary model-generated actions or shell,
Kubernetes, production, merge, release, tag, and deployment remain outside
this decision. A positive result may claim only the bounded local regression
demo and its observed fault-to-recovery chain.

## DEC-033 — Diagnosis-to-Action v2 Namespaced Offline Architecture

**Status: `accepted` for local documentation, contracts, registries, candidate
filtering, and offline tests only.**

Diagnosis-to-Action v2 is a versioned successor, not a rewrite of Phase 1,
Phase 3, Phase 5A `DiagnosisResultV2`, R3, or LOCAL_DEMO. Its Python namespace is
`ecomsre.dta_v2` and its schemas use the `dta-v2.*` prefix. Existing tool and
diagnosis contracts may be reused only through explicit adapters.

The target architecture is one Tool-Using Strong Single identity with bounded
read tools, one typed `DtaDiagnosis`, deterministic Runbook candidate filtering,
a second same-Agent action-selection stage, deterministic operational admission
and authorization policy, typed execution, step receipts, and Runbook-specific
verification. Conditional Reviewer, `RECREATE_SERVICE`, arbitrary Shell, model
write tools, remote Docker, Kubernetes, and production remain outside the MVP.

The MVP scenario scope is Payment configuration failure, Recommendation service
stopped, and Email memory leak. Agent-visible scenario files are opaque and
separate from evaluator truth. The runtime Gate cannot read expected root,
mechanism, Runbook, injected fault, or other gold labels. The default Agent
budget is four read-tool dispatches, zero identical normalized repeats, one
diagnosis terminal, and one candidate-bound ActionProposal. Provider turns and
semantic terminals are counted separately; this decision does not claim that a
four-tool investigation uses only two Provider HTTP calls.

The delivery order is Portfolio Demo first, then a separately frozen replay
held-out evaluation. Held-out evaluates diagnosis, evidence, Runbook selection,
no-action, escalation, and cost without executing live writes. The later three
known-scenario live closures are engineering Demo evidence, not held-out
Recovery Accuracy.

## DEC-034 — v2 Bounded Multi-step Safety and Authorization Policy

**Status: `accepted` as a design contract only; no Live authority is created.**

The Payment `ROLLBACK_CONFIGURATION` and Recommendation `RESTART_SERVICE`
Runbooks are LOW risk and permit one forward step. The Email
`MITIGATE_MEMORY_LEAK` Runbook is MEDIUM risk and permits at most two fixed
forward steps under one proposal and one Policy decision: disable the exact
leak flag, then restart the exact owned Email service. Every step has its own
precondition, state binding, and receipt. A partial failure stops without a
third step, alternate Runbook, automatic flag re-enablement, compensation of
the safer completed step, or any second unknown write. If flag disable succeeds
and restart fails, the terminal is `PARTIALLY_APPLIED / ESCALATE_HUMAN`; the
completed flag-disable step remains applied. PR-B must persist one `StepReceipt`
for every attempted step.

For `ecomsre.dta_v2` only, this record narrowly supersedes the `DEC-012`
one-forward-mutation limit for that exact versioned Email Runbook transaction.
Both ordered steps, the logical target `email`, parameter schema, preconditions,
executor/verifier identities, and step cap are frozen in the trusted Registry.
All other `DEC-012` restrictions remain in force. Payment, Recommendation,
Phase 3, DEC-031, DEC-032/LOCAL_DEMO, and every historical runtime retain their
one-forward-mutation limit.

LOW may later use a semantic-scope-bound standing authorization only when the
environment, scenario, Runbook, target, parameters, digests, limits, and expiry
match exactly. MEDIUM requires a fresh exact human approval for every live run.
HIGH is denied. These rules do not alter the historical one-forward-mutation
contracts in Phase 3, DEC-031, or DEC-032.

For the user-designated `dta-v2-master-v1` Goal only, the later `DEC-035`
narrowly replaces that fresh-per-run MEDIUM record with one human-issued Master
record plus an exact expiring run-bound child for every attempt.

This decision authorizes no Docker start/stop/restart, Provider call, fault
injection, remediation, held-out execution, commit, push, PR, merge, release,
or deployment. Each protected action retains its independent authorization
boundary.

## DEC-035 — DTA v2 Master Authorization and Run-bound Delegation

**Status: `accepted` for the user-designated `dta-v2-master-v1` Goal only.**

The active Master Goal is the exact human authorization for all three frozen
DTA v2 Runbook scopes, including the MEDIUM Email transaction. This record
narrowly supersedes only `DEC-034`'s requirement to obtain a new human approval
record for each Email run. It does not change the Email two-step cap, fixed step
order, partial-failure policy, ownership boundary, no-shell rule, or any
historical authorization contract.

PR-B persists one create-once `MasterAuthorizationRecord` that binds the Goal
version and SHA-256, approver `Minghong Sun`, authorization source
`USER_EXPLICIT_DTA_V2_MASTER_GOAL_AUTHORIZATION`, delegated execution mode,
local Unix Docker environment class, Sandbox identity, trusted Registry digest,
the three independently enumerated opaque scenario IDs, each exact Runbook
digest, target, risk, typed parameter-schema digest, and step cap. The
scenario-ID set and authorized-Runbook-scope set remain independent: neither
the authorization record nor Operational Admission contains a scenario-to-gold
Runbook mapping.

The Master record is standing for this exact Goal and has no arbitrary time
expiry. Every exact attempt derives an expiring `AttemptAuthorizationRecord`
bound to the Master digest, run and attempt IDs, opaque scenario ID, current-state,
Diagnosis, resolved-evidence, CandidateSet, Proposal, Registry, selected
Runbook, target, parameter-value, risk, and step-cap digests or fields. The
model cannot create, modify, or broaden either record. Operational Admission
recomputes these bindings and denies expiry, mismatch, remote Docker, unknown
ownership, a second transaction, a false precondition, or a step-cap breach.
HIGH remains denied.

The PR-B implementation and its fake backends remain offline evidence only.
This Decision Record does not itself start Docker, call a Provider, inject a
fault, or perform a real mutation. Any later action is additionally bounded by
the active Goal's exact protected-action authority.

## DEC-036 — DTA v2 Provisional Agent Identity and Provider Development Gate

**Status: `accepted` for PR-D under the user-designated
`dta-v2-master-v1` Goal only.**

PR-D freezes one provisional Tool-Using Strong Single identity in
`config/dta-v2/agent-identity.v1.json`. It binds the preferred model
`gpt-5.4-mini-2026-03-17`, temperature zero, the two system prompts, ordered
read-tool schemas, Diagnosis schema, Action Selection schema, ActionProposal
schema, and Provider-adapter version. Investigation may dispatch at most four
runtime-owned read tools with zero identical normalized repeats. A separate
Action Selection turn receives only Runbook ID, target, Registry-owned risk,
typed parameter constraints, required evidence sources, and non-write
dispositions; it receives no implementation, command, path, container identity,
authorization, or evaluator truth.

The active Goal authorized bounded real Provider development calls with
fake/replay read tools. Three retained attempts terminated
`FAIL / PROVIDER_PROTOCOL_FAILURE`; the subsequent attempt
`4d07fee0c13e440db6d78c9bd3180286` completed the PR-D development gate `PASS`
with two read dispatches and a candidate-bound Payment rollback proposal. All
four attempts recorded zero Docker, fault injection, Runbook execution,
Executor, Verifier, forward/configuration/service mutation, and public writes.
Raw Provider responses and credentials remain private.

This record does not authorize another Provider call, Docker action, fault
injection, real Runbook execution, held-out evaluation, live remediation,
release, or deployment. Those actions require their exact later-stage Goal
authority. The PR-D result is development compatibility evidence only.

## DEC-037 — DTA v2 PR-E Replay Evaluation Protocol and Result

**Status: `accepted` for PR-E under the user-designated
`dta-v2-master-v1` Goal only.**

PR-E used a separate evaluation-case manifest rather than broadening the three
operational scenario IDs frozen by `ScenarioRegistry` and `DEC-035`. The public
dataset contains six development cases and three no-action/ambiguous cases;
only the hashes of three private replay-held-out cases and their evaluator
truth are public. Agent-visible case bytes remain separate from evaluator
truth throughout capture, replay, and scoring.

The Goal-authorized owned capture campaign
`00af08e75935b1c9eb52081311592818` completed `PASS`: it selected the measurable
bounded `1000x` Email variant, restored the exact baseline, completed `CLEAN`
cleanup, left owned containers/networks/volumes at `0/0/0`, changed no
non-owned resource, and recorded zero Agent, Provider, Runbook, Executor,
Verifier, or remediation-write calls. Its closure SHA-256 is
`62263e5bcfc5c4698ec6de44dd1a1b0cf43b7a1b9ddcee8e4dc50931359a61d8`.
Capture was evaluator-controlled dataset generation, not Agent remediation.

Before the held-out seal, the Goal-permitted compatible configured model was
changed to `gpt-5.4-2026-03-05`; the frozen Agent identity SHA-256 is
`aa08b5869aaac7e4ad4b1084367fc99a01c6dd05521ea933fddf9b5fb364ca61`.
Development campaign `4334dc61fdb48f3abfbe51bf1814c860` then completed
`PASS` across 18 entries: both arms correctly completed all six fault cases and
all three no-action/ambiguous cases, with zero unsafe proposal attempts. Truth
isolation and scorer verification passed, all prohibited-action counters were
zero, and the report SHA-256 is
`8b138049bb911e991c9ccc0b9e9fb3493613fd26f835f3726d9e6304fd410871`.

The held-out seal
`0f944e79f0958f285006c3bdc3cf8f82b8a71731d8d96d02b474f254a54e247a`
binds exact code head `2c683a6fe8ac682678064e0ba2b2ab856dc607c3`, model,
Agent identity, both prompts, tools, budgets, schemas, Registry, Candidate
Filter, scorer, and three case/truth hash pairs. Execution
`f187b6214c8313f829b047f7b8dbd461` consumed the six-entry A/B schedule once and
terminated `COMPLETED`, with truth isolation and scorer verification passing,
zero unsafe proposal attempts, and every prohibited-action counter zero. The
One-shot Full-Context arm scored 3/3 on root, mechanism, Runbook Top-1,
evidence validity, and action precision. The Adaptive Tool-Using arm scored
3/3 root, 2/3 mechanism, and 1/3 on Runbook Top-1, evidence validity, and action
precision. The held-out set contained no no-action/escalation cases, so those
two held-out denominators are zero. Report SHA-256 is
`26b4002fe0232a2d8b03295e98b3c023e9409ae30eaba3b2e21ae1d1523524e6`.

This is a negative result for Tool Use superiority. It supports neither a
Tool-Use advantage nor held-out generalization and must not be tuned against or
rerun after result inspection. It is replay diagnosis/action-selection
evidence for the exact frozen head only, not live recovery evidence. DEC-036's
PR-D call authority remains closed; this record does not authorize another
Provider call, Docker action, fault injection, Runbook execution, remediation,
release, or deployment. PR-F actions require their separate exact Goal
authority.

## DEC-038 — DTA v2 PR-F Known-scenario Local Live Demo Result

**Status: `accepted` for PR-F under the user-designated
`dta-v2-master-v1` Goal only.**

PR-F exercised one exact four-slot campaign against the project-owned local
25-service Sandbox: no fault, Payment configuration failure, Recommendation
service stopped, and the bounded `1000x` Email memory-leak variant. A one-shot
campaign capability bound the exact claim and schedule, current Agent/model/
Prompt/tools, Registry, Candidate Filter, admission and authorization policies,
typed controls, Executors, Verifiers, reporting, frozen Compose/image authority,
and cleanup. Operational code did not read scenario-to-gold diagnosis or
Runbook labels.

The accepted campaign completed `LIVE_PASS` for all four slots. No-fault
produced a non-write terminal, Admission `DENY`, and zero forward writes.
Payment diagnosed `CONFIGURATION / CONFIGURATION_ERROR`, selected
`ROLLBACK_CONFIGURATION`, and applied one configuration-restoration step.
Recommendation diagnosed `SERVICE_RUNTIME / SERVICE_UNAVAILABLE`, selected
`RESTART_SERVICE`, and applied one owned-service restart. Email diagnosed
`LOCAL_RESOURCE / MEMORY_LEAK`, selected `MITIGATE_MEMORY_LEAK`, and applied
the exact ordered `DISABLE_LEAK_FLAG` then `RESTART_OWNED_SERVICE` steps.

The Email verifier used live-config schema v2 with an exact 60-second
post-restart settle interval, 20-second resource windows, five samples per
window, and the unchanged `100000.0 B/s` ceiling. Its two canonical recovery
slopes were `-5734.4` and `25190.4 B/s`. All three positive scenarios passed
two recovery windows and Runbook-specific verification. Every slot restored
baseline, finished cleanup `CLEAN`, ended with owned containers/networks/
volumes `0/0/0`, and changed no non-owned resource.

Aggregate counters were 13 read-tool dispatches, 20 Provider turns, three
faults attempted and applied, four forward steps, zero restoration writes,
zero unsafe write attempts, zero rollback/compensation writes, and zero
arbitrary-shell attempts. The public report semantic SHA-256 is
`7ec04bd95f67e1250ba8d899347a0f5d5575b6eadcdc0d29e952e0c118211333`.
Failed predecessor campaigns remain immutable and are not upgraded by this
later result.

PR-F narrowed the investigation Prompt after PR-E, so PR-E's one-time held-out
negative remains bound only to historical identity
`aa08b5869aaac7e4ad4b1084367fc99a01c6dd05521ea933fddf9b5fb364ca61`.
It was not rerun, and it does not become held-out evidence for current PR-F
identity `6efc26c6e5fab6190be9e63c0bec318c6e94fa29196e6693eb63b2845c6ad0a4`.

This record supports `DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS` only for the known
local Portfolio scenarios. It is not production, deployment, release,
arbitrary autonomous remediation, held-out recovery accuracy, Tool Use
superiority, or Multi-Agent superiority. The campaign capability is consumed;
this record creates no continuing Provider, Docker, fault, or mutation
authority.

## DEC-039 — DTA v2.1 Versioned Successor and Immutable DTA v2 Bindings

**Status: `accepted` for the user-designated `dta-v21-p0-master-v1` Goal.**

DTA v2.1 is an independent successor under `ecomsre.dta_v2.v21`, schema prefix
`dta-v21.`, configuration root `config/dta-v21`, and test root
`tests/dta_v21`. It does not extend frozen v2 enums or schema literals in
place. Stable low-level primitives may be reused only through narrow typed
adapters that preserve v2 behavior and evaluator-truth isolation.

The 11 files in `historical-v2-bindings.v1.json`, current v2 Agent identity,
held-out seal, negative held-out result, and live terminal are immutable. Every
v2.1 stage runs the deterministic historical verifier. The old held-out set is
not rerun or relabeled, and its claim does not transfer to any v2.1 identity.
Any mismatch terminates `BLOCKED_DTA_V21_BASELINE_HISTORY_DRIFT`.

## DEC-040 — DTA v2.1 Crossed Service and Fault-mechanism Matrix

**Status: `accepted` for DTA v2.1 P0.**

Service and mechanism must not remain one-to-one. The versioned matrix includes
Email with memory leak and service unavailable, service unavailable across
Recommendation, Email, and Product Catalog, Ad CPU saturation, Shipping
dependency latency, no fault, and missing or conflicting evidence. Candidate
sets overlap and observer alerts contain no answer semantics.

The dependency label is `DEPENDENCY_LATENCY`. It may not become timeout after
model output; only independent pre-freeze calibration plus a prior Decision
Record may establish typed timeout semantics. Deterministic anti-shortcut tests
and per-service and per-mechanism confusion matrices are required.

## DEC-041 — DTA v2.1 Evidence-guided Planner and Compact Deterministic State

**Status: `accepted` for DTA v2.1 P0.**

The Planner explicitly records up to three typed hypotheses, supporting and
contradicting evidence, unresolved evidence-source gaps, and exactly one next
semantic action. The runtime enforces candidate scope, tool allowlists, four
read dispatches, zero identical normalized repeats, evidence references, and
typed failures. It may reject inconsistency but may not use evaluator truth to
choose a tool for the model.

Full evidence remains in the private run-bound store. Subsequent Provider turns
receive a deterministic Evidence Index plus only the newest full bounded
observation, not the accumulated raw transcript. The default pre-freeze state
ceiling is 24,000 UTF-8 bytes. No LLM summarizer is admitted. Confidence cannot
expand candidates, Runbooks, risk, steps, authorization, or write scope.

## DEC-042 — DTA v2.1 Frozen Three-arm Evaluation and Honest Claim Gate

**Status: `accepted` for DTA v2.1 PR-D and PR-E.**

The frozen arms are `ONE_SHOT_FULL_CONTEXT`, `FLAT_ADAPTIVE`, and
`EVIDENCE_GUIDED_PLANNER`, with Planner versus Flat Adaptive as the primary
comparison and One-shot as a descriptive anchor. One model and temperature,
independent arm identities, a 12-case visible development set, an eight-case
private held-out set, a 24-entry one-time schedule, a deterministic scorer, and
preregistered thresholds are frozen before execution.

The advantage terminal is legal only if every preregistered quality, evidence,
action, token, tool, latency, and safety threshold passes. Otherwise the exact
terminal is `DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED`. That
negative is valid engineering evidence and must not be tuned away or rerun.
Engineering acceptance remains independently possible when all protocol,
safety, live, verification, and cleanup gates pass.

## DEC-043 — DTA v2.1 Bounded Local Portfolio and Zero Model Write Authority

**Status: `accepted` only under the exact user-designated
`dta-v21-p0-master-v1` Goal.**

The final local portfolio is no fault, Ad CPU saturation, Email unavailable,
and Product Catalog unavailable. The active Goal authorizes only its exact
evaluator-controlled local faults, one-step `MITIGATE_CPU_SATURATION` on owned
Ad with risk frozen as `LOW`, and one-step `RESTORE_SERVICE_AVAILABILITY` on
exact owned Email or Product Catalog. `RESTORE_DEPENDENCY_LATENCY` is
replay-only. No-fault performs zero writes.

The model emits planning, Diagnosis, and candidate-bound Action Selection
semantics only. It never receives or emits shell, commands, paths, URLs, Docker
identities, feature-flag keys, executor/verifier implementations,
authorization, or raw write APIs. Trusted code resolves candidates, verifies
fresh ownership and run-bound authority, executes one fixed admitted step, and
independently verifies two recovery windows, baseline restoration, cleanup,
and no non-owned drift.

This record creates no generic authority outside the exact Goal. P1 tooling,
training, new Multi-Agent orchestration, Computer Use, Kubernetes, cloud,
generic shell, generic feature-flag writes, generic service restart, UI work,
production, release, and deployment remain excluded.

## DEC-044 — DTA v2.1 PR-F Ad CPU Resource-Only Recovery Protocol

**Status: `accepted` only under the user-designated
`dta-v21-p0-prf-ad-cpu-resource-recovery-v1` amendment.**

The binding fields are:

```text
effective scope: DTA v2.1 P0 PR-F only
fault: adHighCpu off -> on on the exact owned Ad service
fault_impact_kind: RESOURCE_ONLY
resource_fault_observed: true
business_impact_observed: false
business_sli_role: NON_REGRESSION_GUARDRAIL
required recovery claim: RESOURCE_STATE_RECOVERED
forbidden recovery claims: BUSINESS_SLI_RECOVERED, USER_IMPACT_RECOVERED,
  CUSTOMER_IMPACT_RECOVERED
held-out effect: none
PR-D effect: none; accepted calibration remains immutable
PR-E effect: none; seal, execution, score, and negative claim remain immutable
```

This amendment was accepted before the first PR-F live attempt. It prevents
post-hoc oracle changes; it does not enable them.

The PR-F Ad CPU slot is classified as `RESOURCE_ONLY`. Its recovery oracle is
two consecutive fresh ten-second post-mitigation windows from the same run and
attempt, using the accepted PR-D five-sample Ad CPU-percent query and unit. In
each window CPU p95 must be at or below `11.162%`, the lower of accepted
baseline `1.162% + 10` percentage points and ten percent of accepted fault
`406.326%`; CPU capacity ratio must also be at or below `0.5`.

The accepted PR-D Ad calibration observed no business impact. Therefore the
business latency SLI is a `NON_REGRESSION_GUARDRAIL`, never a recovery oracle.
The frozen substantive predicate remains latency p95 at least baseline plus
`5 ms` and at least twice baseline. Both post-mitigation windows must report
that predicate false, service health `PASS`, and the endpoint reachable. The
only positive Ad terminal is `AD_CPU_RESOURCE_RECOVERY_PASS`; public evidence
must keep `business_impact_observed=false` and
`user_visible_recovery_claimed=false`.

The typed protocol binds the accepted PR-D closure raw and semantic hashes,
both selected Ad observation hashes, the calibration-limitations bytes, and
the exact accepted measurement source. This amendment changes only the Ad CPU
business-impact and recovery oracle. PR-D, PR-E, the held-out seal, execution,
result, and negative planner-advantage claim remain immutable and may not be
rerun or relabeled.

## DEC-045 — DTA v2.1 PR-F Closed-World Compose Identity and Reconciled Retry Admission

**Status: `accepted` only under the user-designated
`dta-v21-p0-prf-compose-identity-reconciliation-v1` amendment.**

The effective scope is DTA v2.1 P0 PR-F only. The historical attempt
`dta-v21-prf-01-no-fault-422f015451fd` remains the immutable
`BLOCKED_DTA_V21_PRF_SAFETY` terminal at `READY` on code HEAD
`422f015451fd0a37f1442aa770fcffff75336aaa`.

The blocker arose because the raw resolved Compose document includes the
authorized private host bind-source path for each context's flag directory.
Preflight and a live attempt intentionally use different private roots, so raw
document hashes differ even when every executable Compose property is
otherwise identical.

Raw Compose hashes remain the exact provenance identities. PR-F additionally
uses a versioned execution identity that replaces only the exact `flagd`
`/etc/flagd` and `flagd-ui` `/app/data` attempt-local bind-source values with
`private://dta-v21-prf/attempt-local-flagd` before canonical hashing. The
complete raw safety contract must pass before this closed-world normalization.

The historical disposition does not change: `baseline_restored=false` and
`cleanup=BLOCKED` remain immutable. An append-only reconciliation may prove
that it stopped before baseline evidence, fault, Provider use, or forward
action; that its complete raw Compose difference is exactly the two admitted
source fields; and that fresh owned-resource quiescence exists. This proof may
admit one new campaign, but it cannot relabel the old attempt.

Exactly one new campaign may start from Slot 1 under a new code HEAD after
fresh CI, independent review, v2 preflight, reconciliation, and retry
admission. Failure of that campaign exhausts this amendment. This decision has
no effect on PR-D, PR-E, DEC-044, the held-out result, the Agent or Provider
identity, Runbooks, live slots, or fault and recovery oracles.

## DEC-046 — DTA v2.1 PR-F No-Fault Capability-Miss Preservation and Positive-Slot Continuation

**Status: `accepted` only under the user-designated
`dta-v21-p0-prf-capability-closeout-v1` amendment.**

The frozen PR-E planner returned a safe `NO_ACTION` disposition on the PR-F
No-Fault slot but made a non-null false-positive Diagnosis. The existing
No-Fault verifier correctly rejected that output. The attempt restored baseline,
cleaned all owned resources, and performed no fault or forward write.

The result is therefore a diagnosis capability miss with successful no-write
safety, not a live-slot pass and not a safety incident. No additional No-Fault
sample is authorized. The model output, verifier, Prompt, identity, Provider
configuration, CandidateSet behavior, and evaluator oracle remain unchanged.
The consumed retry's campaign-level terminal remains
`BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED`; this limitation closeout does not delete,
replace, or relabel it.

One append-only continuation may execute only the three unattempted positive
slots, in the fixed order Ad CPU, Email unavailable, Product Catalog
unavailable. If all three pass, PR-F may close with
`DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_NO_FAULT_DIAGNOSIS_MISS`.
`DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS` is not supported and must not be
minted.

This decision is conservative: it preserves a failure rather than converting it
to success; removes repeated sampling rather than adding another chance; narrows
the final claim rather than weakening the oracle; and separates diagnosis
quality from write safety and recovery execution.

The bound Amendment-3 raw SHA-256 is
`24cc236c1892c9992b6d36da377608c34fb22c2bc270f99349e5e8a4e0a0498a`.
This decision changes none of PR-D, PR-E, DEC-044, DEC-045, the held-out
artifacts or conclusion, the Agent/Provider identity, any Runbook, live slot,
or positive fault and recovery oracle.

## DEC-047 — DTA v2.1 PR-F Frozen-Agent Capability-Limitations Closeout

**Status: `accepted` only under the user-designated
`dta-v21-p0-prf-final-capability-closeout-v1` amendment.**

No further Provider or Docker execution is authorized for DTA v2.1 PR-F. The
live No-Fault false-positive Diagnosis remains a capability failure with safe
`NO_ACTION`. The Ad CPU attempt remains
`AD_CPU_PLANNER_DUPLICATE_READ_PROTOCOL_FAILURE_SAFE_RESTORATION`: the frozen
Planner failed with `DUPLICATE_READ_REQUEST` after three Provider turns, before
a complete Diagnosis, CandidateSet, ActionProposal, or Agent remediation. One
evaluator fault operation occurred, while Agent forward writes remained zero.
The bounded runtime restored baseline and completed clean owned-resource
cleanup; that restoration is not an Ad recovery result.

Email service unavailable and Product Catalog service unavailable remain
`NOT_ATTEMPTED`. No recovery PASS is claimed for any positive slot in the
consumed continuation. The historical READY blocker, valid No-Fault capability
miss, Ad protocol failure, both consumption records, PR-D, PR-E, DEC-044,
DEC-045, DEC-046, Prompt, model, identity, tool schemas, planner schemas,
Runbooks, CandidateSet semantics, and evaluator truth remain immutable.

The original four-slot acceptance terminal is not minted. DTA v2.1 closes only
with
`DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_FROZEN_AGENT_CAPABILITY_LIMITATIONS`,
preserving the negative held-out and live evidence. Future capability repair
belongs to a separately versioned v2.2 identity with new development data and a
new preregistered evaluation; it cannot retroactively rewrite v2.1.

The bound Amendment-4 raw SHA-256 is
`bf9484483583202a198e7699d57ee92f94c8a3ed2207cac3489601542645be1e`.

## DEC-048 — DTA v2.1 PR-F Frozen Report Scope and Administrative Successor Attestation

**Status: `accepted` only under the user-designated
`dta-v21-p0-prf-administrative-successor-scope-v1` amendment.**

The immutable v4 capability-closeout report continues to bind the accepted PR
#55 capability tree at merge SHA
`4442dda6cf7d54e163b34355dad2e8235d3957c1`. Its report SHA-256
`24d5fda0f10029817afa4146a99f4d1d19e99e7c6902d84c88dd377a74d7c48f`
and candidate scope SHA-256
`c3988b4ba18ec471c681638caa2074f4690c3fd3fae93ba268b282a150feb7dd`
remain frozen. A later administrative repair may not rewrite or rebind that
report scope.

PR #56 instead carries one append-only, versioned administrative-successor
attestation. It binds the frozen report and original scope, exact base main
tree, exact successor non-public tree scope, exact changed path set, and exact
raw SHA-256 of every authorized non-public changed file. It also records that
Provider, Docker, held-out, scenario, fault, and Runbook execution did not
occur, and proves the public results and private evidence were not changed.

This attestation applies only to the closed PR #56 post-merge metadata and
deterministic test-contract repair. It creates no wildcard, ignore-list, or
reusable exception. Any future non-public change requires a new Decision Record
and a new successor attestation; DEC-048 cannot be reused.

The bound Amendment-6 raw SHA-256 is
`d7537afaf51fe9d84ce9d9abc7eb6d60dba277d1221738aba34f2cb0f9e20375`.

## DEC-049 — DTA v2.2 Versioned Successor and v2.1 Immutability

**Status: `accepted` for the user-designated `dta-v22-p0-master-v1` Goal.**

DTA v2.2 is an independent successor under `ecomsre.dta_v2.v22`, schema prefix
`dta-v22.`, configuration root `config/dta-v22`, and public result prefix
`dta-v22-`. It does not modify or reuse `src/ecomsre/dta_v2/v21` as its
implementation namespace.

The exact v2.2 starting main is
`9da92d54a4fb470c5452cee36a731e81529d05a5`. The historical manifest binds the
frozen DTA v2 portfolio, v2.1 design, identities, held-out execution and seal,
capability-closeout report, PR #55 capability merge and tree, and PR #56
administrative merge, tree, and attestation. The v2 verifier remains part of
the v2.2 gate. Any byte, semantic, identity, claim, terminal, commit, tree, or
ancestry drift stops v2.2 with `BLOCKED_DTA_V22_BASELINE_HISTORY_DRIFT`.

No v2 or v2.1 Provider call, Docker execution, held-out rerun, report rewrite,
or failed-evidence deletion is permitted. The aggregate v2.1 private failure
taxonomy may publish bounded counts only; raw Provider content, case mappings,
private paths, and credentials remain private.

## DEC-050 — DTA v2.2 Runtime-Owned State and Shared Controller Schema

**Status: `accepted` for DTA v2.2 P0.**

The model chooses only one closed hypothesis, one available evidence action, or
one semantic terminal. Runtime owns run identity, turn ordinal, hashes, canonical
ordering, hypothesis IDs, belief status, gaps, action mask, duplicate and
dominance elimination, budgets, and correction usage.

`FLAT_CANONICAL` and `PLANNER_LITE` use the same required
`ControllerDecisionV22` schema, common bootstrap, action catalog, selected
memory mode, read and correction budgets, Diagnosis admission, CandidateSet,
and Action Selection. The primary treatment difference is that Planner-Lite
receives a persistent runtime-owned `BeliefLedgerViewV22`; Flat does not.
`NO_INCIDENT` and `UNRESOLVED` are first-class hypotheses. A model may not
create a service, domain, mechanism, hypothesis ID, budget, digest, or terminal.

One no-tool correction is allowed only for an enumerated, side-effect-free
decision-shape error. It consumes one Provider turn, dispatches no read, grants
no write authority, and cannot be repeated. First-pass and post-correction
protocol acceptance remain separate metrics.

## DEC-051 — DTA v2.2 Canonical Action Catalog and Query Semantics

**Status: `accepted` for DTA v2.2 PR-B onward.**

The model selects an `action_id`; it never generates result limits, metric
subsets, service tuples, sampling windows, sample counts, paths, URLs, commands,
or runtime identities. Every action binds a versioned canonical request, exact
target set, source, coverage key, weighted cost, and digest. Catalog generation
may use only alert context, candidates, static topology, capability registry,
executed coverage, and budget. Evaluator truth, fixture content, expected
mechanism/source/action, and fault-controller state are prohibited inputs.

Executed, dominated, unavailable, and over-budget actions are removed before
the next turn, making exact duplicate dispatch structurally impossible. Read
outcomes distinguish success with records, success empty, unavailable, timeout,
and schema failure. Unsupported metrics are not numeric zero. Trace queries
return a bounded connected neighborhood without rewriting the complete fixture
to a new anchor. The read-only Changes source contains only sanitized opaque
change metadata and may include decoys in every family.

Query-semantic or catalog truth-isolation failure stops with
`BLOCKED_DTA_V22_QUERY_SEMANTICS` or `BLOCKED_DTA_V22_TRUTH_ISOLATION`.

## DEC-052 — DTA v2.2 Semantic Evidence Predicates and Alternative Clauses

**Status: `accepted` for DTA v2.2 PR-C onward.**

Evidence predicates are source-local, generic, deterministic, versioned, and
frozen from visible development thresholds. They may not read evaluator truth
or use case-specific thresholds. Salient Memory retains all evidence refs and
predicates, bounded typed facts, and an exact loss ledger; Full Memory is a
development reference representation.

Diagnosis admission uses versioned alternative clauses rather than a fixed
source set. Runtime clauses admit or deny a model-selected closed hypothesis;
they do not choose the hypothesis. The raw semantic proposal and admitted
Diagnosis are recorded separately. Candidate filtering requires resolved
predicates, one acceptable clause, trusted Registry, and the exact target.
No-Incident requires broad candidate coverage, healthy runtime, sufficient
metric support, and no strong anomaly. A completed UNKNOWN fault is forbidden.

## DEC-053 — DTA v2.2 Factorial Development and Paired Held-out Evaluation

**Status: `accepted` for DTA v2.2 PR-D through PR-F.**

Before capture or freeze, at least 40 synthetic protocol transitions must meet
first-pass acceptance >=95%, post-correction acceptance >=98%, and zero invalid
dispatches. Visible development uses 24 cases across the 2x2 controller x
memory factorial plus Deterministic Router and One-shot Oracle anchors. Fixed
trajectories separately measure Full versus Salient representation without a
Provider or policy change.

Held-out uses 24 private cases across Flat Salient, Planner-Lite Salient,
Deterministic Router Salient, and One-shot Oracle: exactly 96 entries, one seal,
one execution, one unblinding, and no post-unblinding Prompt/schema/scorer/gate
change or retry. One-shot is `ORACLE_CONTEXT_UPPER_BOUND`; tool selection is
not applicable and materialization cost is fully counted.

End-to-end success includes protocol, semantic Diagnosis, acceptable evidence,
and applicable action correctness. Actions use an applicability denominator;
null equality cannot create success. Costs per correct are
`INFINITY / NOT_ESTIMABLE` when an arm has zero correct results. The exact
Planner and memory terminals follow their preregistered gates. A negative
advantage result is valid engineering evidence, not a blocker.

## DEC-054 — DTA v2.2 P0 Zero Live Agent Write Authority

**Status: `accepted` for DTA v2.2 P0 under the active Goal.**

Agent live write authority, live Runbook execution, generic model shell,
production, cloud, Kubernetes, remote Docker, and non-owned mutation are all
zero. P0 action evaluation is replay-only. The model may emit only typed
semantic controller choices and candidate-bound Action Selection.

The active Goal separately authorizes evaluator-controlled project-owned local
capture using only its exact mutation allowlist. Capture is dataset generation:
Agent calls, Provider calls, Runbook executions, and Agent forward writes are
zero; every case has one exact allowlisted fault operation where applicable,
baseline restoration, `CLEAN` project-owned cleanup, and zero non-owned change.
An authority, ownership, restoration, cleanup, or isolation mismatch stops with
`BLOCKED_DTA_V22_SAFETY` and never broadens cleanup.

No Decision Record alone starts Docker, calls a Provider, injects a fault,
executes held-out, publishes a PR, or creates later-stage authority. Those
actions remain bounded by the exact active Goal and current stage gate.

## DEC-055 — DTA v2.2 Execution Report and Administrative Successor Provenance

**Status: `accepted` for DTA v2.2 PR-A through PR-F.**

Every execution report binds the exact pre-merge candidate code head and its
declared evidence scope. A merge or later metadata repair does not rewrite or
rebind a frozen report. Post-merge metadata uses one versioned, append-only
administrative-successor attestation that names the base, successor head/tree,
exact changed path set, and raw SHA-256 for every authorized changed file.

Each attestation records whether Provider, Docker, held-out, scenario, fault,
Runbook, private evidence, or public result activity occurred. It is valid only
for the named PR and cannot create a wildcard or reusable exception. Later
changes require a new Decision Record and successor attestation. Exact-head CI,
fresh review with Must Fix zero, claim accuracy, and the stage's historical,
truth-isolation, and secret-scan gates are required before merge. Provenance or
exact-head mismatch stops with `BLOCKED_DTA_V22_EXACT_HEAD_ACCEPTANCE`.

The starting main has one pre-existing mypy `arg-type` diagnostic in frozen
`ecomsre.dta_v2.v21.live_final_cli`. v2.2 does not modify that module. Its exact
raw bytes are added to the v2.2 historical manifest, and `mypy.ini`
disables only `arg-type` for that exact module. All other v2.1 diagnostics and
all v2.2 diagnostics remain enabled. Historical drift makes this exception
invalid through the v2.2 verifier. That verifier also requires this to be the
only v2.1 override, with exactly `disable_error_code = arg-type`, and rejects a
v2.1 wildcard or global mypy bypass.

## Upstream references

- [OTel Demo 3.0.0 release](https://github.com/open-telemetry/opentelemetry-demo/releases/tag/3.0.0)
- [OTel Demo Docker deployment](https://opentelemetry.io/docs/demo/docker-deployment/)
- [OTel Demo feature flags](https://opentelemetry.io/docs/demo/feature-flags/)
