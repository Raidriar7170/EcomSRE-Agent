# Diagnosis-to-Action v2.1 P0 Protocol

Goal version: `dta-v21-p0-master-v1`

Protocol scope: versioned successor design for six sequential PRs. This PR-A
document creates no Provider call, Docker action, fault injection, held-out
execution, or live write by itself.

## Mission and claim boundary

DTA v2.1 tests whether a bounded Strong Single SRE Agent can generalize across
crossed service and fault-mechanism combinations by explicitly planning which
evidence it still needs, while reducing adaptive investigation cost and keeping
model write authority at zero.

Engineering completion and evaluation advantage are separate terminals:

- engineering: `DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS`;
- evaluation: `DTA_V21_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED` or
  `DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED`.

A negative preregistered comparison is a valid completed result. Neither
terminal supports production autonomy, cloud remediation, arbitrary incident
recovery, or held-out live recovery accuracy.

## Historical v2 boundary

DTA v2 remains immutable historical evidence. The v2.1 protocol must not:

- edit any file bound by `historical-v2-bindings.v1.json`;
- rerun the old held-out evaluation;
- relabel the old negative result;
- transfer the old claim to a v2.1 identity.

Every v2.1 focused test target and exact-head CI runs the historical verifier
before accepting successor evidence.

## Successor identity

- Python namespace: `ecomsre.dta_v2.v21`.
- Configuration root: `config/dta-v21`.
- Test root: `tests/dta_v21`.
- Schema prefix: `dta-v21.`.
- Public result prefix: `dta-v21-`.

The successor may wrap stable generic primitives through narrow typed adapters,
but defines independent fault, Runbook, scenario, planner, identity,
evaluation, live, and result schemas. Frozen v2 enums and schema literals are
not extended in place.

## Crossed P0 fault matrix

The minimum fault domains are `APPLICATION`, `CONFIGURATION`,
`SERVICE_RUNTIME`, `LOCAL_RESOURCE`, `NETWORK`, `DEPENDENCY`, `QUEUE`, and
`UNKNOWN`. The minimum mechanisms are `CONFIGURATION_ERROR`,
`SERVICE_UNAVAILABLE`, `MEMORY_LEAK`, `CPU_SATURATION`,
`DEPENDENCY_LATENCY`, and `UNKNOWN`.

The dependency mechanism remains `DEPENDENCY_LATENCY`. It may become a typed
timeout only if independent pre-freeze calibration proves timeout semantics and
a Decision Record changes before development results are used.

Required successor cases include:

| Case | Candidates | Expected mechanism | Evidence emphasis | Runbook | Live |
|---|---|---|---|---|---|
| Ad degradation | `ad`, `frontend`, `load-generator` | `CPU_SATURATION` | Metrics, Resources, Runtime | `MITIGATE_CPU_SATURATION` | required |
| Email unavailable | `checkout`, `email`, `frontend` | `SERVICE_UNAVAILABLE` | Runtime plus Metrics or Traces | `RESTORE_SERVICE_AVAILABILITY` | required |
| Product Catalog unavailable | `checkout`, `frontend`, `product-catalog` | `SERVICE_UNAVAILABLE` | Runtime and Traces | `RESTORE_SERVICE_AVAILABILITY` | required |
| Shipping dependency degradation | `checkout`, `frontend`, `quote`, `shipping` | `DEPENDENCY_LATENCY` | Traces and Metrics | `RESTORE_DEPENDENCY_LATENCY` | replay only |
| No fault | overlapping safe candidates | no write | observed evidence | none | required |
| Missing/conflicting evidence | overlapping candidates | unresolved | explicit gaps | none | replay |

Agent-visible alerts use opaque identifiers and business symptoms. They never
contain the fault flag, expected root, mechanism, evidence, Runbook, split, or
answer key. The matrix must contain same-service multiple mechanisms, the same
mechanism on at least three services, a new service with a known mechanism, new
mechanisms, overlapping candidate sets, no-action, and conflicting evidence.

## Evidence-guided planner

The planner maintains at most three typed hypotheses. Each hypothesis binds a
candidate root, domain, mechanism, active/rejected status, supporting and
contradicting evidence references, and unresolved evidence-source gaps. Each
Provider turn emits exactly one admitted semantic decision:

- request one allowed evidence source and target;
- submit a typed Diagnosis; or
- abstain with unresolved gaps.

The trusted runtime enforces candidate targets, allowlisted read tools, four
read dispatches, zero identical normalized repeats, five investigation turns,
one Action Selection turn, evidence-reference continuity, and typed failures.
It does not use evaluator truth to choose a tool for the model. Confidence is
telemetry only and cannot expand authority.

Full typed evidence remains private and immutable. The model-visible state is a
deterministic projection containing the Alert Context, current hypotheses and
gaps, a canonical Evidence Index, the newest full bounded observation, prior
tool names and request hashes, and remaining budgets. Old raw observations are
not resent on every turn. The pre-evaluation serialized ceiling defaults to
24,000 UTF-8 bytes. No LLM summarizer is used.

Investigation and candidate-bound Action Selection remain separate. Action
Selection sees only Diagnosis, resolved evidence, and trusted candidate views;
it receives no executor, verifier, command, Docker identity, path, URL,
authorization, evaluator truth, or raw write interface.

## Three-arm evaluation

The frozen arms are:

- `ONE_SHOT_FULL_CONTEXT`, a descriptive quality and cost anchor;
- `FLAT_ADAPTIVE`, the primary adaptive baseline;
- `EVIDENCE_GUIDED_PLANNER`, the tested planner plus compact context.

All arms use one frozen compatible model, temperature zero, the same v2.1
Diagnosis/Action schemas, cases, scorer, and applicable candidate stage. Arm
identity manifests bind model, prompts, schemas, projection source, registry,
candidate filter, Provider settings, and identity hash.

The visible development set contains at least 12 cases. The private held-out
set contains exactly eight valid cases, producing 24 scored entries. A
development-only four-case no-compaction ablation attributes token effects to
context projection without becoming part of the held-out advantage claim.

Before held-out execution, code, identities, prompts, tools, schemas, cases,
truth, scorer, thresholds, and schedule are frozen and sealed. All 24 entries
must be durable before one unblinding. No tuning or scored rerun is permitted
after the seal.

The primary preregistered gate compares Planner with Flat Adaptive and requires
all of the following: 100% protocol acceptance, truth/scorer PASS, non-lower
root exact match, mechanism Macro-F1 improvement of at least 0.10, at least one
additional correct applicable case and rate gain of 0.10 for evidence validity
and Runbook or action, mean input tokens at most 75%, mean total tokens at most
80%, no higher mean semantic reads, median latency at most 125%, zero duplicate
calls, zero unsafe or shell attempts, and zero non-owned mutations. Failure of
any condition freezes the exact negative claim terminal.

## Model authority and trusted execution

The model may emit only `EvidencePlanDecisionV21`, `DtaDiagnosisV21`, and
`ActionSelectionDecisionV21`. Trusted code resolves candidates, admits the
proposal, verifies ownership and authorization, executes a fixed operation,
and independently verifies recovery.

P0 admits only:

- `MITIGATE_CPU_SATURATION` for exact owned Ad, risk `LOW`, and one
  flag-restoration step;
- `RESTORE_SERVICE_AVAILABILITY` for exact owned Email or Product Catalog, one
  owned-service start step;
- `RESTORE_DEPENDENCY_LATENCY` as `REPLAY_ONLY`.

No generic shell, feature-flag write, service restart, network operation,
arbitrary host mutation, remote Docker, Kubernetes, cloud, or production target
is admitted.

## Bounded local portfolio

The final ordered portfolio is no fault, Ad CPU saturation, Email unavailable,
and Product Catalog unavailable. Each attempt requires fresh environment and
ownership admission, baseline evidence, exactly one evaluator-controlled fault
where applicable, grounded Diagnosis, deterministic CandidateSet,
candidate-bound Action Selection, run-bound authorization, exact fixed
execution, two independent recovery windows, baseline restoration, owned
cleanup, and an empty non-owned diff.

The no-fault slot performs zero writes. Dependency latency is mandatory replay
evidence but has no P0 live Agent write. Every failed attempt is retained; a
retry requires a real source, prompt, schema, configuration, fixture,
calibration, Provider, test, or verifier change.

## Sequential delivery

The Goal is one continuous contract but not one giant PR:

1. PR-A freezes this protocol, decisions, namespace, progress, and historical
   bindings.
2. PR-B implements ontology, crossed scenarios, Runbook registry, candidate
   filtering, and replay contracts.
3. PR-C implements planner, compact context, Provider adapter, two-stage Agent,
   and provisional arm identities.
4. PR-D performs owned capture and calibration, visible development evaluation,
   preregistration, private held-out construction, and one seal without held-out
   execution.
5. PR-E executes the sealed 24-entry schedule once, unblinds once, scores, and
   freezes the exact claim.
6. PR-F executes the four-slot bounded local portfolio and publishes the final
   evidence and claim-limited interview surfaces.

Every stage starts from the latest merged main in a fresh branch and worktree,
passes focused and full deterministic checks, exact-head GitHub Actions, and a
fresh independent read-only review with Must Fix zero and Claim Accuracy PASS,
then squash merges before the next stage starts.

P1 additions—including recent-change tooling, SFT, preference optimization,
new Multi-Agent orchestration, Reviewer Agent, Computer Use, Kubernetes, cloud
remediation, generic shell, and UI work—remain out of scope.

## Typed blockers

Execution stops rather than weakening gates on baseline history drift, truth
leakage, unavailable compatible Provider, failed fault calibration, unknown
ownership or unsafe mutation, held-out seal/protocol failure, or exact-head
CI/review failure. The owning terminal is the exact `BLOCKED_DTA_V21_*` marker
defined by the active Goal.
