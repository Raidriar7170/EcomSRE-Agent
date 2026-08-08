# RCAEval Single-first Adaptive Agent v1

Status: `BLOCKED`

Reason: `SHARED_SMOKE_DOWNSTREAM_SCHEMA_FAILURE_OUTSIDE_R2_INITIAL_INTERFACE_SCOPE`

Claim boundary: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`.

## Intent

This implementation turns the dev.3 redesign handoff into a real bounded Agent:

1. Query Metrics and Logs once.
2. Produce one Strong Single initial diagnosis.
3. Apply a deterministic uncertainty/conflict gate.
4. Return directly or selectively query Logs, Traces, or both.
5. Fuse the initial diagnosis, deterministic Metrics hypotheses, and source-isolated specialist hypotheses with a keep-by-default contradiction policy.
6. Resolve the final indicator with the frozen hybrid deterministic policy.

RE2-SS never exposes Traces. RE2-OB queries Traces only after the gate selects a trace-bearing route. The runtime has no remediation, external writes, hidden labels, TT access, or fallback model.

## Frozen routing and cost contract

The initial gate uses confidence thresholds `0.75` and `0.55`, normalized Metrics top-1/top-2 margin `0.25`, evidence support, cross-source disagreement, indicator availability, and trace ambiguity.

| Route | Model operations | Tool calls |
| --- | ---: | ---: |
| Direct return | 1 | 2 |
| Logs | 3 | 2 |
| Traces | 3 | 3 |
| Both | 4 | 3 |

Every semantic operation permits at most one byte-identical retry for the inherited dev.3 transport allowlist. Schema, protocol, semantic-result, and validation-driven retries remain forbidden. Each run ID has one create-once terminal; an interrupted sidecar is sealed and never replayed.

## Output contracts

The initial Agent emits one visible service, an optional canonical indicator, calibrated confidence, exact evidence references, an explanation, and bounded uncertainty flags. Specialists emit ranked hypotheses with explicit root-candidate, propagated-symptom, or uncertain roles and separate supporting/contradicting references. Fusion sees an architecture-blind envelope and may override the initial service only when new evidence clearly contradicts it.

The hybrid indicator resolver keeps a model indicator when it is among the selected service's top two deterministic candidates, uses deterministic top-1 when its normalized margin is at least `0.6`, and otherwise preserves the model output with an uncertainty disposition.

## Evaluation protocol

- Provider smoke: 12 DESIGN cases stratified across both systems and all six fault types.
- DESIGN: one 60-case arm per candidate, at most three candidates.
- Minimum DESIGN gate: completion at least 58/60; Root Service at least 50/60; Pair at least 28/60; Damage at most 3; Rescue greater than Damage; at least 24 direct returns; mean semantic operations at most 3.0; no privacy, schema, or schedule failure.
- Candidate selection: Root Service, Pair, net Rescue, lower Damage, lower mean semantic operations, then more direct returns.
- DEV_VALIDATION: only after a tracked candidate freeze; 120 cases × Strong Single reference and selected Adaptive Agent; no tuning after access.

Damage and Rescue use the authoritative `root_cause_pair_ac_at_1` endpoint.

## Fail-closed validation boundary

Before it opens the validation schedule or any validation case directory, the validation entrypoint requires the canonical `config/rcaeval-adaptive-v1/adaptive-candidate.json` to be byte-identical to its tracked `HEAD` blob. It then verifies the selected candidate, config hashes, model, passing DESIGN metrics, implementation ancestry, and that the Adaptive runtime, inherited Provider adapter, and entrypoint scripts have no tracked or untracked drift from the recorded implementation commit. The new r1 shared smoke did not pass, so no freeze exists and the validation boundary remained closed.

## Current review issue

The earlier three 12-case attempts remain preserved as pre-fix Initial Diagnosis interface failures and do not count as DESIGN candidates. The bounded repair introduced `InitialDiagnosisInput`, removed `canonical_evidence` from the Provider envelope, derived visible services and evidence references from that same envelope, and added safe field-specific `INITIAL_*` validation codes.

In the new candidate-1 r1 shared smoke, all 12 runs completed Initial Diagnosis with zero `INITIAL_*` failures. Seven completed end to end. Four stopped at Logs Specialist output validation and one stopped at Fusion output validation, each with `PROVIDER_OUTPUT_INVALID_SCHEMA`; there were zero transport or semantic retries and zero private-path hits.

The authorized r2 path applies only to a precise shared Initial Diagnosis interface code, which was not observed. The next review should decide whether to authorize a separately bounded repair of the downstream Logs Specialist and Fusion Provider-output schemas. The shared-smoke gate must remain unchanged, prior execution identifiers must not be reused, and DEV_VALIDATION must remain closed until a candidate passes that gate.
