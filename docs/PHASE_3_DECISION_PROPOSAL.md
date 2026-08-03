# Phase 3 Decision Proposal — DEC-025

## Status

`PROPOSED / REVIEW_REQUIRED`

This document is not binding until the user explicitly accepts this exact
record. It does not authorize Docker execution, a live mutation, commit, push,
PR creation, merge, release, deployment, or Phase 4 work.

## Why a decision is required

The new goal authorizes Phase 3 work, but the Phase 3 entry gate is not yet
satisfied. `OQ-008` still defers the exact action schemas, allowlist entries,
preconditions, rollback contract, and human-approval interface required to
implement accepted `DEC-012`. No `src/ecomsre/phase3` or `tests/phase3`
implementation exists.

This proposal resolves that decision gap without changing `DEC-012`, exposing
evaluator-only scenario truth, or broadening the current local Demo scope.

## Proposed DEC-025 — Phase 3 restricted-remediation contract v1

### 1. Completion boundary

Phase 3 v1 closes as a local, offline/replay-verified implementation of the
complete Planner → Policy Gate → Restricted Executor → independent Verifier →
compensating rollback path.

A live Docker mutation is not part of this closure because it requires a
separate protected-action authorization and the Phase 0 live environment is
still `UNSAFE / REVIEW_REQUIRED`. Until such a smoke is separately authorized
and passed, the public truth marker is:

`PHASE3_LOCAL_RESTRICTED_REMEDIATION_VERIFIED / LIVE_MUTATION_NOT_RUN`

### 2. Exact v1 action allowlist

The only v1 forward action is:

| Field | Frozen value |
|---|---|
| action type | `RESTORE_FROZEN_SERVICE_CONFIGURATION` |
| catalog entry | `phase3.action.ad.runtime-config.restore.v1` |
| target service | `ad` |
| required RCA mechanism | `runtime_configuration_failure` |
| project namespace | `ecomsre-phase0` |
| blast radius | exactly one project-owned service configuration |
| forward mutation limit | exactly one per attempt |

The plan contains no shell, argv, executable name, host path, feature-flag
key/value, scenario identity, credential, public URI, arbitrary parameter map,
or physical resolver detail. The executor-side catalog maps the opaque entry to
one typed project-owned adapter. The adapter is not exposed to the Planner,
model input, approval surface, or observer-visible evidence.

Flag changes not represented by this exact entry, arbitrary configuration
rollbacks, replica changes, restarts, deletes, deploys, Docker commands, and
every other action are unsupported and fail closed. Adding any entry requires
a later accepted Decision Record.

### 3. Planner contract

The deterministic v1 Planner accepts only a validated current-run Phase 2
final RCA and incident binding. It emits the one typed plan only when all of
the following are true:

1. the decision is `RCA_CONFIRMED`;
2. `root_service == "ad"`;
3. `fault_mechanism == "runtime_configuration_failure"`;
4. the RCA is supported by at least two evidence references from two sources;
5. `missing_evidence` is empty;
6. the RCA run, incident, and evidence bindings match the remediation attempt.

Otherwise it emits a typed `NO_ACTION` result and no write capability can be
created. The Planner never receives an executor, ownership authority, approval
authority, rollback capability, or raw command runner.

### 4. Human-approval contract

Human approval is the default. An approval is an immutable typed object bound
to all of:

- `run_id`, `incident_id`, and `attempt_id`;
- canonical `plan_sha256` and `action_sha256`;
- fresh `ownership_sha256`;
- approval mode and decision;
- UTC issue and expiry timestamps;
- a unique nonce and approval ID.

The Policy Gate accepts only an unexpired `APPROVED` record from an injected
approval authority and consumes it once. A missing, denied, stale, forged,
replayed, wrong-run, wrong-plan, wrong-action, or wrong-ownership approval
fails closed before a mutation capability exists.

`LOCAL_TEST_AUTO` is legal only through a separately constructed local-test
approval authority whose typed configuration explicitly contains
`environment="LOCAL_TEST"` and `auto_approval_enabled=true`. The mode is always
written to evidence. No environment-variable presence, CLI default, model
output, or test fixture can silently turn human approval into auto approval.

### 5. Deterministic Policy Gate

The pure Policy Gate revalidates, in this order:

1. run/incident/attempt/plan/action identity and hashes;
2. the exact v1 catalog entry and closed action type;
3. a fresh authenticated ownership context and exact discovered resource set;
4. project namespace, active run binding, and target identity;
5. the current typed precondition state is known and eligible;
6. blast radius is exactly one;
7. forward mutation count is zero;
8. a rollback reservation over the exact preimage is durably present;
9. approval is valid and unconsumed.

Only then may it issue an opaque, single-use execution capability bound to the
same hashes and rollback reservation. The capability contains no raw command
or physical target data.

Unknown ownership produces `RESOURCE_OWNERSHIP_UNKNOWN`. A forbidden action or
policy conflict produces `UNSAFE`. A missing human approval produces
`APPROVAL_REQUIRED`. No rejection path increments the forward mutation count.

### 6. Durable attempt ledger and Restricted Executor

The attempt ledger is append-only and sequence/hash chained. Before adapter
dispatch it must durably record:

1. the accepted plan;
2. the rollback reservation and preimage digest;
3. the consumed approval;
4. the Gate capability;
5. a forward-mutation intent.

The ledger uses exclusive locking and compare-and-swap sequence checks so
concurrent, replayed, restarted, or duplicated requests cannot dispatch a
second forward mutation.

The Restricted Executor accepts only the single-use Gate capability and the
injected typed adapter. It never accepts shell, argv, a host path, a free-form
action, or a generic subprocess runner. Adapter results are exactly:

- `NOT_APPLIED` — no mutation occurred;
- `APPLIED` — the one mutation occurred and verification may start;
- `UNKNOWN` — mutation state cannot be proved.

`UNKNOWN`, timeout, partial failure, adapter exception, ledger inconsistency,
or crash ambiguity produces `STATE_UNKNOWN` or
`MANUAL_INTERVENTION_REQUIRED`; it permanently prevents a second forward
mutation. Read checks, verification, and a reserved compensating rollback do
not increment the forward counter.

### 7. Independent Verifier

The Verifier receives only a read-only observation capability and immutable
attempt/action hashes. It shares no executor, forward capability, approval
authority, or mutable adapter with the execution plane.

Mitigation passes only when one current-run recovery window proves all of:

- the target remains exactly project-owned;
- the service is ready;
- all required telemetry is fresh and attributable;
- at least 200 relevant attempts were observed within 180 seconds;
- the error rate is at most 1%;
- no monitored service changed from ready to unready;
- no new SEV1/SEV2 regression appeared.

Timeout, stale or missing data, ownership uncertainty, SLO non-recovery, or a
regression cannot pass.

### 8. Compensating rollback

Rollback authority is reserved before the forward mutation and is bound to the
exact preimage digest, action, attempt, ownership context, and one-time nonce.
It can be consumed only after an `APPLIED` forward mutation when independent
verification fails.

Rollback is not a second forward mutation. It restores only the exact reserved
preimage through a separate typed rollback adapter. It cannot select another
action or target. A successful rollback must be read back against the preimage
digest and fresh ownership proof. Failure or unknown rollback state produces
`ROLLBACK_FAILED` and permanently closes the attempt.

If the forward mutation is `UNKNOWN`, automatic rollback is forbidden because
the system cannot prove which state to compensate; the outcome is
`STATE_UNKNOWN` and manual intervention is required.

### 9. Terminal vocabulary

The complete v1 terminal outcomes are:

- `MITIGATION_ACCEPTED`;
- `NO_ACTION`;
- `APPROVAL_REQUIRED`;
- `POLICY_DENIED`;
- `ROLLED_BACK`;
- `UNSAFE`;
- `MANUAL_INTERVENTION_REQUIRED`;
- `ROLLBACK_FAILED`;
- `STATE_UNKNOWN`;
- `RESOURCE_OWNERSHIP_UNKNOWN`.

Every terminal state closes the attempt. The five safety markers retain the
exact meanings owned by `docs/SAFETY_BOUNDARIES.md`; no green test or model
explanation can rewrite one into success.

### 10. Evidence, replay, and leakage

Every Planner, approval, Gate, ledger, execution, verification, rollback, and
terminal record carries a schema version, UTC timestamp, monotonic duration,
run/incident/attempt identity, canonical hashes, and previous-event hash.

Observer-visible evidence may contain the opaque catalog entry, target service,
action category, state transitions, hashes, counts, verification summaries,
and terminal outcome. It must not contain scenario identity, physical flag
key/value, preimage content, evaluator path, raw command, credential, or
physical resolver detail.

Replay verifies the full event chain and reproduces the terminal decision
without executing an adapter. Missing, reordered, duplicated, malformed, or
hash-mismatched events fail closed.

## Required implementation and acceptance evidence

After explicit acceptance, Phase 3 implementation must include:

1. typed contracts and leakage guards;
2. deterministic Planner and `NO_ACTION` path;
3. human/local-test approval authorities and anti-replay;
4. pure Policy Gate and single-use capability;
5. durable one-forward-mutation attempt ledger;
6. Restricted Executor with `NOT_APPLIED/APPLIED/UNKNOWN` classification;
7. independent Verifier;
8. compensating rollback and rollback verification;
9. end-to-end offline workflows for success, denial, mutation uncertainty,
   verification failure with rollback, rollback failure, and state unknown;
10. machine-readable evidence/replay plus CLI and Make targets;
11. Phase 3 focused tests, Phase 2 and Phase 1 non-regression, full repository
    tests, scoped Ruff/mypy, secret/leakage scans, and independent read-only
    review;
12. an evidence-safe closeout document with `Phase 4 entered: NO`.

The implementation must not run Docker or perform any real mutation under this
decision alone.

## Acceptance action

If accepted, this exact record will be added to the Decision Register as
`DEC-025`, `OQ-008` will be closed to it, and implementation may begin. Until
then, the correct state is:

`PHASE3_DECISION_PROPOSED / REVIEW_REQUIRED`
