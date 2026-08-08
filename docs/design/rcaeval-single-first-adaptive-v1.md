# RCAEval Single-first Adaptive Agent v1

Status: `SINGLE_FIRST_ADAPTIVE_V1_DOWNSTREAM_INTERFACE_BLOCKED_READY_FOR_REVIEW`

Reason: `DOWNSTREAM_INTERFACE_REPAIR_ROUND_LIMIT_EXHAUSTED`

Claim boundary: `DEVELOPMENT_VISIBLE / SHARED_SMOKE / NOT_DESIGN_RESULT / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`.

## Intent

This implementation turns the dev.3 redesign handoff into a bounded Agent:

1. Query Metrics and Logs once.
2. Produce one Strong Single initial diagnosis.
3. Apply a deterministic uncertainty/conflict gate.
4. Return directly or selectively query Logs, Traces, or both.
5. Fuse the initial diagnosis, deterministic Metrics hypotheses, and source-isolated specialist hypotheses with a keep-by-default contradiction policy.
6. Resolve the final indicator with the frozen hybrid deterministic policy.

RE2-SS never exposes Traces. RE2-OB queries Traces only after the gate selects a trace-bearing route. The runtime has no remediation, external writes, hidden labels, TT access, or fallback model.

## Frozen routing and cost contract

The gate uses confidence thresholds `0.75` and `0.55`, normalized Metrics top-1/top-2 margin `0.25`, evidence support, cross-source disagreement, indicator availability, and trace ambiguity.

| Route | Model operations | Tool calls |
| --- | ---: | ---: |
| Direct return | 1 | 2 |
| Logs | 3 | 2 |
| Traces | 3 | 3 |
| Both | 4 | 3 |

Every semantic operation permits at most one byte-identical retry for the inherited dev.3 transport allowlist. Schema, protocol, semantic-result, and validation-driven retries remain forbidden. Each run ID has one create-once terminal; an interrupted sidecar is sealed and never replayed.

## Input and output authority

Initial Diagnosis receives one bounded evidence projection plus exact visible service and evidence-reference sets. It does not receive canonical/internal evidence.

Each Logs or Trace call receives one `SpecialistInput`:

- `source_evidence` contains only the selected source;
- `visible_evidence_refs` equals the selected-source reference set exactly;
- `visible_services` equals the selected-source services plus the Initial root service;
- the Provider-visible Initial context excludes Initial evidence references that are not authorized Specialist output references;
- the Provider-facing hypotheses omit source, which the Runtime attaches authoritatively after validation.

Fusion receives one architecture-blind `FusionInput` whose Initial service, visible services, visible references, and override candidates are explicit and self-validating. Output validation uses only that same input authority. Fusion keeps the Initial service by default and may override only to an authorized root-candidate service with explicit support and contradiction.

The Runtime propagates nine exact Specialist failure codes and eight exact Fusion failure codes. Safe diagnostics contain only code, role, field path, constraint type, and counts; they never persist raw invalid service/ref values, raw Provider arguments, or raw responses.

## Evaluation protocol

- Provider smoke: 12 DESIGN cases stratified across both systems and all six fault types.
- DESIGN: one 60-case arm per candidate, at most three candidates, only after the shared smoke passes.
- Minimum DESIGN gate: completion at least 58/60; Root Service at least 50/60; Pair at least 28/60; Damage at most 3; Rescue greater than Damage; at least 24 direct returns; mean semantic operations at most 3.0; no privacy, schema, or schedule failure.
- Candidate selection: Root Service, Pair, net Rescue, lower Damage, lower mean semantic operations, then more direct returns.
- DEV_VALIDATION: only after a tracked candidate freeze; 120 cases × Strong Single reference and selected Adaptive Agent; no tuning after access.

Damage and Rescue use the authoritative `root_cause_pair_ac_at_1` endpoint.

## Fail-closed validation boundary

Before opening the validation schedule or any validation case directory, the validation entrypoint requires the canonical candidate freeze to be byte-identical to its tracked `HEAD` blob. It verifies selected candidate, config hashes, model, passing DESIGN metrics, implementation ancestry, and absence of tracked or untracked drift across the Adaptive runtime, inherited Provider adapter, and entrypoint scripts.

The shared smoke did not pass after the second and final downstream-interface repair round. No candidate freeze exists, so the validation boundary remained closed. DEV_VALIDATION schedule values and case directories were not opened.

## Preserved execution history

The evidence chain remains append-only:

1. Three pre-fix labels produced 36/36 Initial Diagnosis `INVALID_SCHEMA`; these are not DESIGN candidates.
2. Initial-interface-fix r1 completed Initial Diagnosis in 12/12 cases, but only 7/12 completed end to end; four Logs and one Fusion output failures were still generic.
3. Downstream-fix r1 introduced the shared Specialist/Fusion authority and exact codes; 5/12 completed, while seven Logs operations ended with `SPECIALIST_EVIDENCE_REF_NOT_VISIBLE`.
4. Downstream-fix r2 removed the conflicting non-authoritative Initial ref vocabulary from Specialist input; 11/12 completed, while one Fusion operation ended with `FUSION_OVERLAPPING_EVIDENCE_REF`.

All older terminal and sidecar aggregates were rehashed unchanged, and every new round used a distinct run domain and private root.

## Review disposition

The two-round downstream-interface repair allowance is exhausted. The unchanged smoke gate forbids continuing to DESIGN, candidate selection, candidate freeze, or DEV_VALIDATION. Human review may assess whether the remaining Fusion overlap warrants a future separately authorized version, but this task must not run a third `single-first-adaptive-v1` repair, reuse consumed IDs, or characterize the smoke as an accuracy result.
