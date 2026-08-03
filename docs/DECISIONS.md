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

## Upstream references

- [OTel Demo 3.0.0 release](https://github.com/open-telemetry/opentelemetry-demo/releases/tag/3.0.0)
- [OTel Demo Docker deployment](https://opentelemetry.io/docs/demo/docker-deployment/)
- [OTel Demo feature flags](https://opentelemetry.io/docs/demo/feature-flags/)
